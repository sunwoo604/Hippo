"""The daily job: a macOS launchd agent that runs `hippo sync --all --scheduled`.

  ~/Library/LaunchAgents/com.hippo.sync.plist
    - at sync_at (default 03:00); if the Mac was asleep then, launchd runs it on wake
    - also at login (RunAtLoad), for days the Mac was shut down at that time;
      `--scheduled` makes that run a no-op if today's job already ran
    - output goes to ~/agent-memory/logs/launchd.log

macOS only for now (Linux: post_mvp/linux-scheduler.md). Written by `hippo
install`, which the user runs; Hippo never loads it from anywhere else.
"""

import getpass
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from hippo import paths
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text

LABEL = "com.hippo.sync"
TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
BASE_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
LAUNCHD_LOG_LIMIT = 1_000_000  # bytes; the log is cut back to empty past this


def supported() -> bool:
    return sys.platform == "darwin"


def plist_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def marker_file() -> Path:
    return paths.root() / "state" / "last-scheduled-run"


def outcome_file() -> Path:
    return paths.root() / "state" / "last-scheduled-outcome.json"


def launchd_log() -> Path:
    return paths.logs_dir() / "launchd.log"


def parse_time(value: str) -> tuple[int, int]:
    m = TIME.match(value.strip())
    if not m:
        raise HippoError(f"'{value}' isn't a time like 03:00 (24-hour HH:MM).")
    return int(m[1]), int(m[2])


def job_path() -> str:
    """PATH for the job: launchd's default lacks where `claude` usually lives."""
    dirs = []
    for tool in ("claude", "git"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).parent))
    dirs += [str(paths.bin_dir()), *BASE_PATH]
    return os.pathsep.join(dict.fromkeys(dirs))


def plist(at: str) -> dict:
    hour, minute = parse_time(at)
    return {
        "Label": LABEL,
        "ProgramArguments": [str(paths.launcher()), "sync", "--all", "--scheduled"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": True,
        "StandardOutPath": str(launchd_log()),
        "StandardErrorPath": str(launchd_log()),
        # claude finds its login (macOS keychain) through USER; don't rely on launchd for it.
        "EnvironmentVariables": {"PATH": job_path(), "HOME": str(Path.home()),
                                 "USER": getpass.getuser()},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True)


def install(at: str) -> tuple[str, str]:
    """Write and (re)load the job. Returns (status, detail) for `hippo install`."""
    f = plist_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    new = plistlib.dumps(plist(at)).decode()
    status = "ok" if f.exists() and f.read_text() == new else ("updated" if f.exists() else "created")
    atomic_write_text(f, new)
    if not marker_file().exists():
        # Loading runs the job once (RunAtLoad); don't sync everything right now.
        mark_ran(datetime.now())
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")  # fails harmlessly if not loaded
    r = _launchctl("bootstrap", domain, str(f))
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip() or f"exit code {r.returncode}"
        raise HippoError(f"couldn't load the daily job ({detail}).",
                         next_steps=[f"Run `launchctl bootstrap {domain} {f}` to see the error, "
                                     "then run `hippo install` again."])
    return status, f"daily sync at {at} ({f.name})"


def remove() -> bool:
    f = plist_file()
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    existed = f.exists()
    f.unlink(missing_ok=True)
    return existed


# --- deciding whether a scheduled run should sync -------------------------------

def last_slot(now: datetime, at: str) -> datetime:
    """The most recent time the job was due, at or before `now`."""
    hour, minute = parse_time(at)
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def last_run() -> datetime | None:
    try:
        return datetime.fromisoformat(marker_file().read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def due(now: datetime, at: str) -> bool:
    """True unless the job already ran since it was last due (login after a 3am run)."""
    ran = last_run()
    return ran is None or ran < last_slot(now, at)


def mark_ran(when: datetime) -> None:
    atomic_write_text(marker_file(), when.isoformat(timespec="seconds") + "\n")


def save_outcome(ok: bool, detail: str) -> None:
    """What the last scheduled run did, for `hippo status`."""
    atomic_write_text(outcome_file(), json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"), "ok": ok, "detail": detail}) + "\n")


def last_outcome() -> dict | None:
    try:
        return json.loads(outcome_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def loaded() -> bool:
    """Whether launchd has the job (`hippo install` loads it)."""
    return _launchctl("print", f"gui/{os.getuid()}/{LABEL}").returncode == 0


def trim_launchd_log() -> None:
    f = launchd_log()
    if f.exists() and f.stat().st_size > LAUNCHD_LOG_LIMIT:
        f.write_text("")


# --- telling the user ---------------------------------------------------------------

def notify(message: str) -> None:
    """A macOS notification. Never raises: a failed notification mustn't fail the job."""
    quoted = '"' + message.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        subprocess.run(["osascript", "-e", f'display notification {quoted} with title "Hippo"'],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
