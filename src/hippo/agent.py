"""Run Claude Code headlessly inside a tight sandbox. Used by init and consolidation.

The sandbox (verified against the real CLI before this was built):
- `--restricted`: no shell or code tools unless named, file tools confined to
  the working dir + `--add-dir`, user/project settings files ignored.
- `dontAsk` + explicit allow rules: anything not allowed is refused, never asked.
  A Bash allow rule must match every part of a compound command (`&&`, `;`, `|`).
- Deny rules win over allow rules; a Read deny also blocks writes to that path.
- The user's own ~/.claude/CLAUDE.md and auto memory are kept out, so personal
  workflow instructions don't steer Hippo's agents.
- Claude Code runs "read-only" shell commands (ls, find, grep -r) without
  asking, and Read deny rules don't cover commands that read files without
  naming them (`grep -r SECRET .` leaked a .env in testing). So a PreToolUse
  hook (`hippo _guard-bash`) rejects every Bash call except the allowed
  Hippo commands, with no chaining, redirection or substitution.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from hippo import paths
from hippo.errors import HippoError

# Files whose contents must never reach memory. Matched at any depth in the repo.
SECRET_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "credentials.*", "secrets.*",
    ".npmrc", ".netrc", ".pypirc", ".pgpass", ".htpasswd",
)
SECRET_DIRS = (".ssh", ".aws", ".gnupg", "secrets")

ToolCallback = Callable[[str, dict], None]


@dataclass
class AgentResult:
    ok: bool
    text: str = ""
    error: str = ""
    turns: int | None = None
    cost_usd: float | None = None
    subtype: str = ""
    model: str = "default"
    max_turns: int | None = None


def abs_rule(tool: str, path: Path, suffix: str = "") -> str:
    """Permission rule for an absolute path: `//` anchors at the filesystem root."""
    return f"{tool}(/{path}{suffix})"


def secret_denies(repo: Path) -> list[str]:
    rules = [abs_rule("Read", repo, f"/**/{p}") for p in SECRET_PATTERNS]
    rules += [abs_rule("Read", repo, f"/**/{d}/**") for d in SECRET_DIRS]
    return rules


def settings(allow: list[str], deny: list[str], bash_prefixes: list[str]) -> dict:
    home = Path.home()
    guard = [str(paths.launcher()), "_guard-bash"]
    for prefix in bash_prefixes:
        guard += ["--allow", prefix]
    return {
        "claudeMdExcludes": [str(home / ".claude" / "CLAUDE.md"), str(home / ".claude" / "rules" / "**")],
        "autoMemoryEnabled": False,
        "permissions": {"defaultMode": "dontAsk", "allow": allow, "deny": deny},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": shlex.join(guard), "timeout": 30},
        ]}]},
    }


# --- Bash guard -------------------------------------------------------------

SHELL_OPERATORS = set("&|;<>()")
FORBIDDEN = ("\n", "`", "$(", "<(", ">(", "${")


def check_bash(command: str, prefixes: list[str]) -> str | None:
    """None if `command` is exactly one allowed Hippo command; else why not."""
    allowed = " or ".join(f"`{p} ...`" for p in prefixes)
    command = command.replace("\\\n", " ")  # backslash-newline is a line continuation
    if any(f in command for f in FORBIDDEN):
        return f"Blocked by Hippo: no substitution or multi-line commands. Only {allowed} may run."
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return f"Blocked by Hippo: could not parse the command. Only {allowed} may run."
    if any(t and set(t) <= SHELL_OPERATORS for t in tokens):
        return f"Blocked by Hippo: no chaining, pipes or redirection. Only {allowed} may run."
    for prefix in prefixes:
        want = shlex.split(prefix)
        if tokens[:len(want)] == want:
            return None
    return f"Blocked by Hippo: the only shell commands allowed here are {allowed}."


def guard_main(prefixes: list[str]) -> int:
    """PreToolUse hook entry point: exit 2 blocks the call and shows Claude the reason."""
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("Blocked by Hippo: unreadable hook input.", file=sys.stderr)
        return 2
    reason = check_bash(event.get("tool_input", {}).get("command", ""), prefixes)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


def run(
    prompt: str, *, cwd: Path, add_dirs: list[Path], tools: list[str],
    allow: list[str], deny: list[str], bash_prefixes: list[str] = (),
    on_tool: ToolCallback | None = None, timeout: int = 1800,
    model: str | None = None, max_turns: int | None = None,
) -> AgentResult:
    """`model`: None uses the user's Claude default; HIPPO_MODEL overrides either."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="hippo-agent-", delete=False) as f:
        json.dump(settings(allow, deny, list(bash_prefixes)), f)
        settings_file = f.name
    cmd = ["claude", "-p", "--restricted", "--tools", ",".join(tools),
           "--settings", settings_file, "--no-session-persistence",
           "--output-format", "stream-json", "--verbose"]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    model = os.environ.get("HIPPO_MODEL") or model  # e.g. haiku, to keep a test run cheap
    if model:
        cmd += ["--model", model]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]  # works in -p mode though not in --help
    env = {**os.environ, "HIPPO_AGENT": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        Path(settings_file).unlink(missing_ok=True)
        raise HippoError("`claude` not found on PATH.") from None

    timed_out = threading.Event()
    timer = threading.Timer(timeout, lambda: (timed_out.set(), proc.kill()))
    timer.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        result = _read_stream(proc.stdout, on_tool)
        stderr = proc.stderr.read()
        proc.wait()
    finally:
        timer.cancel()
        Path(settings_file).unlink(missing_ok=True)

    run_info = {"model": model or "default", "max_turns": max_turns}
    if timed_out.is_set():
        return AgentResult(False, error=f"timed out after {timeout // 60} minutes", **run_info)
    if result is None:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit code {proc.returncode}"
        return AgentResult(False, error=f"claude exited without a result ({detail})", **run_info)
    info = dict(turns=result.get("num_turns"), cost_usd=result.get("total_cost_usd"),
                subtype=str(result.get("subtype") or ""), **run_info)
    if result.get("subtype") == "error_max_turns":
        return AgentResult(False, error=f"stopped at the {max_turns}-turn limit "
                                        f"(max_turns in ~/agent-memory/config.json)", **info)
    if result.get("is_error") or proc.returncode != 0:
        return AgentResult(False, text=str(result.get("result", "")),
                           error=str(result.get("result") or result.get("subtype") or "agent error"),
                           **info)
    return AgentResult(True, text=str(result.get("result", "")), **info)


def record(kind: str, project: str, uuid: str, result: AgentResult, **size: int) -> None:
    """One JSON line per agent run in logs/agent-runs.jsonl, for tuning max_turns and cost."""
    if result.subtype == "error_max_turns":
        outcome = "hit_turn_limit"
    else:
        outcome = "ok" if result.ok else "failed"
    line = {"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, "project": project,
            "uuid": uuid, "model": result.model, "turns": result.turns,
            "max_turns": result.max_turns, "cost_usd": result.cost_usd, "outcome": outcome, **size}
    try:
        d = paths.logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "agent-runs.jsonl").open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass


def _read_stream(stdout, on_tool: ToolCallback | None) -> dict | None:
    """Follow stream-json events; report each tool call, return the final result event."""
    result = None
    for line in stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") in ("assistant", "user") and on_tool:
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_use":
                    on_tool(block.get("name", "?"), block.get("input") or {})
                elif block.get("type") == "tool_result" and block.get("is_error"):
                    on_tool(FAILED, {"text": _text(block.get("content"))})
        elif event.get("type") == "result":
            result = event
    return result


FAILED = "_failed"  # pseudo tool name: the previous tool call returned an error


def _text(content) -> str:
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return " ".join(str(content or "").split())


def stats(result: AgentResult) -> str:
    bits = []
    if result.turns is not None:
        bits.append(f"{result.turns} turns")
    if result.cost_usd is not None:
        bits.append(f"~${result.cost_usd:.2f} API-equivalent")
    return f" ({', '.join(bits)})" if bits else ""


def progress(memory_dir: Path, repo: Path) -> ToolCallback:
    """One short line per tool call, so the user can see what the agent is doing."""
    def rel(path: str) -> str:
        for base, label in ((memory_dir, ""), (repo, "")):
            try:
                return label + str(Path(path).relative_to(base))
            except ValueError:
                continue
        return path

    def on_tool(name: str, args: dict) -> None:
        if name == "Read":
            line = ("read", rel(args.get("file_path", "")))
        elif name in ("Glob", "Grep"):
            line = ("search", args.get("pattern", ""))
        elif name in ("Write", "Edit"):
            line = ("write", rel(args.get("file_path", "")))
        elif name == "Bash":
            words = args.get("command", "").split()
            if len(words) > 3 and words[1:3] == ["feature", "set"]:
                line = ("save", f"feature {words[3]}")
            elif len(words) > 3 and words[1:3] == ["decision", "add"]:
                line = ("save", f"decision for {words[3]}")
            elif len(words) > 3 and words[1:3] == ["decision", "supersede"]:
                line = ("save", f"supersede {words[3]}")
            elif len(words) > 2 and words[1:3] == ["review", "add"]:
                line = ("flag", " ".join(words[3:])[:70])
            else:
                line = ("run", " ".join(words[:3]))
        elif name == FAILED:
            text = args.get("text", "")
            if "hook error: [" in text:  # "PreToolUse:Bash hook error: [<guard command>]: <reason>"
                line = ("blocked", text.rpartition("]: ")[2][:120])
            else:
                line = ("failed", text[:120])
        else:
            line = (name.lower(), "")
        print(f"  {line[0]:<8}{line[1]}", flush=True)

    return on_tool
