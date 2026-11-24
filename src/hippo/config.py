"""Machine-wide settings in ~/agent-memory/config.json.

  route        how Hippo was installed (cli | plugin)
  version      Hippo version that last ran `hippo install`
  sync_model   model for `hippo sync`'s agents (HIPPO_MODEL overrides it)
  max_turns    turn limit for each sync agent run; a run that hits it fails
               safely and is retried next time (see logs/agent-runs.jsonl)
  sync_at      time of the daily job, HH:MM
  schedule     false: no daily job (`hippo install --no-schedule`)
"""

import json

from hippo import paths
from hippo.fsutil import atomic_write_text

ROUTES = ("cli", "plugin")
DEFAULTS = {"sync_model": "sonnet", "max_turns": 50, "sync_at": "03:00", "schedule": True}


def load() -> dict:
    f = paths.config_file()
    return json.loads(f.read_text()) if f.exists() else {}


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def save(config: dict) -> None:
    atomic_write_text(paths.config_file(), json.dumps(config, indent=2) + "\n")
