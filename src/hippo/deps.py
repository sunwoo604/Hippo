"""Checks for the external tools Hippo shells out to."""

import json
import shutil
import subprocess
import sys

from hippo import ui
from hippo.errors import HippoError

AUTH_TIMEOUT = 30

# Login needs a browser and a real terminal, so it's always the user's step.
LOGIN_STEPS = [
    "In your own terminal (not through Claude), run:  claude auth login",
    "Then run `hippo install` again.",
]

FIXES = {
    "git": "Install git (e.g. `xcode-select --install` or `brew install git`).",
    "claude": "Install Claude Code: https://docs.claude.com/en/docs/claude-code",
}


def require_tools(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise HippoError(
            f"required tool not found on PATH: {', '.join(missing)}",
            next_steps=[FIXES[t] for t in missing] + ["Then run `hippo install` again."],
        )


def is_interactive() -> bool:
    return sys.stdin.isatty()


def claude_logged_in() -> bool:
    try:
        r = subprocess.run(
            ["claude", "auth", "status", "--json"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=AUTH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HippoError(
            f"`claude auth status` did not respond within {AUTH_TIMEOUT}s.",
            next_steps=["Check that Claude Code works: run `claude auth status`.",
                        "Then run `hippo install` again."],
        ) from None
    try:
        return json.loads(r.stdout).get("loggedIn") is True
    except (json.JSONDecodeError, AttributeError):
        return False


def require_claude_login() -> None:
    """Headless runs can't prompt for login, so settle it up front.

    In a terminal, offer to run `claude auth login`; otherwise fail with the fix.
    """
    if claude_logged_in():
        return
    if is_interactive() and ui.confirm(
        "Claude Code is not logged in (or the login expired). Log in now? [Y/n] "
    ):
        # Inherit the terminal: login is interactive and may open a browser.
        subprocess.run(["claude", "auth", "login"])
        if claude_logged_in():
            return
        raise HippoError("still not logged in after `claude auth login`.", LOGIN_STEPS)
    raise HippoError("Claude Code is not logged in (or the login expired).", LOGIN_STEPS)
