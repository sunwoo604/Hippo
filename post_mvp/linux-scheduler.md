# Daily sync on Linux

**What:** `hippo install` sets up the daily job only on macOS (a launchd
agent). On Linux it prints a note and the user runs `hippo sync --all`
themselves. Add a systemd user timer (`OnCalendar=*-*-* 03:00`,
`Persistent=true` so a missed run happens at next boot) or a crontab entry,
plus a notification on failure (`notify-send`).

**Why postponed (2026-11-22):** the only user today is on macOS (user decision).

**Notes:** `hippo sync --all --scheduled` already holds the platform-neutral
parts: the "already ran since it was due" check, the login check, logging and
the failure notification hook (`schedule.notify`, macOS-only for now). The
launchd job sets PATH, HOME and USER itself; `claude` needs USER to find its
login, so a systemd unit must set it too.
