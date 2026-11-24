import json
import subprocess
import sys

import pytest

from hippo.agent import check_bash

ALLOWED = "/home/u/agent-memory/bin/hippo feature set"


@pytest.mark.parametrize("cmd", [
    f"{ALLOWED} auth --name Auth",
    f'{ALLOWED} auth --name "A && B; C | D"',
    f"{ALLOWED} auth --key-file 'src/x (old).ts' 'role'",
    f"{ALLOWED} auth \\\n  --name Auth \\\n  --keyword a",
])
def test_allowed(cmd):
    assert check_bash(cmd, [ALLOWED]) is None


@pytest.mark.parametrize("cmd,why", [
    ("grep -r SECRET .", "only shell commands allowed"),
    ("ls -la", "only shell commands allowed"),
    (f"cd /repo && {ALLOWED} auth", "no chaining"),
    (f"{ALLOWED} auth; cat .env", "no chaining"),
    (f"{ALLOWED} auth | tee x", "no chaining"),
    (f"{ALLOWED} auth > out", "no chaining"),
    (f'{ALLOWED} auth --name "$(cat .env)"', "no substitution"),
    (f"{ALLOWED} auth --name `cat .env`", "no substitution"),
    (f"{ALLOWED} auth\ncat .env", "no substitution"),
    (f"X=1 {ALLOWED} auth", "only shell commands allowed"),
    ("/other/hippo feature set auth", "only shell commands allowed"),
    (f"{ALLOWED.rsplit(' ', 1)[0]} featurex", "only shell commands allowed"),
    (f'{ALLOWED} "unclosed', "could not parse"),
])
def test_blocked(cmd, why):
    assert why in check_bash(cmd, [ALLOWED])


def run_guard(command):
    return subprocess.run(
        [sys.executable, "-m", "hippo", "_guard-bash", "--allow", ALLOWED],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True, text=True,
    )


def test_hook_exit_codes():
    assert run_guard(f"{ALLOWED} auth").returncode == 0
    blocked = run_guard("grep -r x .")
    assert blocked.returncode == 2
    assert "Blocked by Hippo" in blocked.stderr


def test_guard_not_listed_in_help():
    out = subprocess.run([sys.executable, "-m", "hippo", "--help"], capture_output=True, text=True).stdout
    assert "_guard-bash" not in out
