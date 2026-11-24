"""Register Hippo with Claude Code, per linked repo.

`hippo link` writes Hippo's hooks and one permission rule into the repo's
`.claude/settings.local.json` (personal and gitignored), so Hippo runs only in
repos the user linked and never touches ~/.claude/settings.json. The same file
is used whichever way Hippo was installed (plugin or CLI). `hippo install`
refreshes it in every linked repo, so a moved launcher never goes stale.

Only Hippo's own entries are ever touched: hook commands containing
"hippo _hook" and the `hippo patch new` allow rule.
"""

import copy
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from hippo import paths
from hippo.fsutil import atomic_write_text

# Claude Code event -> (hippo _hook name, timeout in seconds)
EVENTS = {
    "SessionStart": ("session-start", 30),
    "Stop": ("stop", 30),
    "SessionEnd": ("session-end", 10),
}
HIPPO_HOOK = re.compile(r"hippo[\"']?\s+_hook\s")
HIPPO_RULE = re.compile(r"^Bash\(.*hippo patch new \*\)$")
SETTINGS = Path(".claude") / "settings.local.json"


def spec() -> dict:
    launcher = paths.launcher()
    return {event: [{"hooks": [{"type": "command", "command": f"{launcher} _hook {name}",
                                "timeout": timeout}]}]
            for event, (name, timeout) in EVENTS.items()}


def allow_rule() -> str:
    return f"Bash({paths.launcher()} patch new *)"


def settings_file(repo: Path) -> Path:
    return repo / SETTINGS


def _without_hippo(settings: dict) -> dict:
    new = copy.deepcopy(settings)
    hooks = new.get("hooks", {})
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            group = {**group, "hooks": [h for h in group.get("hooks", [])
                                        if not HIPPO_HOOK.search(h.get("command", ""))]}
            if group["hooks"]:
                kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if "hooks" in new and not hooks:
        del new["hooks"]
    perms = new.get("permissions", {})
    if "allow" in perms:
        perms["allow"] = [r for r in perms["allow"] if not HIPPO_RULE.match(r)]
        if not perms["allow"]:
            del perms["allow"]
    if "permissions" in new and not perms:
        del new["permissions"]
    return new


def with_hippo(settings: dict) -> dict:
    """`settings` with exactly one current copy of Hippo's entries."""
    new = _without_hippo(settings)
    hooks = new.setdefault("hooks", {})
    for event, groups in spec().items():
        hooks[event] = hooks.get(event, []) + groups
    perms = new.setdefault("permissions", {})
    perms["allow"] = perms.get("allow", []) + [allow_rule()]
    return new


def ensure(repo: Path) -> tuple[str, str]:
    """Write Hippo's entries into the repo's settings.local.json. Returns (status, detail)."""
    f = settings_file(repo)
    try:
        current = json.loads(f.read_text()) if f.exists() else {}
    except json.JSONDecodeError as e:
        return ("skipped", f"not valid JSON ({e.msg}, line {e.lineno}); left untouched, "
                           "so Hippo's hooks aren't set up in this repo")
    new = with_hippo(current)
    if new == current:
        return ("ok", "Hippo hooks and permission")
    if f.exists():
        _backup(f, repo)
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, json.dumps(new, indent=2) + "\n")
    return ("updated" if current else "created",
            "hooks (session start, stop, session end) + permission for `hippo patch new`")


def remove(repo: Path) -> tuple[str, str]:
    """Take Hippo's entries out of the repo's settings.local.json (for `hippo unlink`)."""
    f = settings_file(repo)
    if not f.exists():
        return ("ok", "no Hippo hooks")
    try:
        current = json.loads(f.read_text())
    except json.JSONDecodeError as e:
        return ("skipped", f"not valid JSON ({e.msg}, line {e.lineno}); left untouched. "
                           "Remove the `hippo _hook` entries yourself")
    new = _without_hippo(current)
    if new == current:
        return ("ok", "no Hippo hooks")
    _backup(f, repo)
    if new:
        atomic_write_text(f, json.dumps(new, indent=2) + "\n")
        return ("updated", "removed Hippo's hooks and permission")
    f.unlink()
    return ("removed", "it only held Hippo's hooks and permission")


def _backup(f: Path, repo: Path) -> None:
    d = paths.backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, d / f"{repo.name}-settings.local-{datetime.now():%Y-%m-%dT%H%M%S}.json")
