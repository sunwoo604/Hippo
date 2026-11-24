# Hippo

Persistent, structured project memory for coding agents. Hippo keeps feature
docs, decisions and session patch notes for each linked repo under
`~/agent-memory/`, and a daily job folds new notes and commits into the docs.

## Installation

Requires Python 3.12+ and [Claude Code](https://code.claude.com). The daily sync
job is macOS-only.

### As a Claude Code plugin (recommended)

```bash
claude plugin install hippo --marketplace OWNER/hippo
```

On older Claude Code versions without `--marketplace`, run these two commands in
a Claude Code session instead:

```
/plugin marketplace add OWNER/hippo
/plugin install hippo@hippo
```

Then ask Claude to "set up Hippo", or run `hippo install` yourself. The plugin
ships the `hippo` command and the Hippo skill, so no pip install is needed.

### As a standalone CLI

```bash
pipx install git+https://github.com/OWNER/hippo
hippo install
```

`pip install git+https://github.com/OWNER/hippo` inside a virtualenv works too.

Either way, run `hippo link` in each repo you want Hippo to remember. That adds
Hippo's hooks to the repo's `.claude/settings.local.json`.

## Commands

| Command | What it does |
| --- | --- |
| `hippo install` | One-time machine setup, including the daily sync job (macOS) |
| `hippo link` | Connect the current repo and generate its feature docs |
| `hippo open` | Show the repo's memory (`-f <feature>`, `--decisions`, `--review`) |
| `hippo sync` | Fold patch notes and unnoted commits into the docs now (`--all` for every repo) |
| `hippo review done <id>` | Dismiss an item in needs-review |
| `hippo status` | Every linked repo at a glance (last sync, unsynced notes, review items, new commits) plus the daily job and Claude login |
| `hippo unlink [repo]` | Disconnect a repo and archive its memory to `~/agent-memory/archived-projects/` (works from anywhere with a repo name or uuid, e.g. for a deleted repo) |

## Daily sync

`hippo install` registers a launchd job (`~/Library/LaunchAgents/com.hippo.sync.plist`)
that runs `hippo sync --all` every day at 03:00. If the Mac was asleep, it runs
on wake; if it was shut down, it runs at the next login. A failed run shows a
macOS notification; details are in `~/agent-memory/logs/`.

```bash
hippo install --sync-at 02:30        # change the time
hippo install --sync-model haiku     # cheaper model for sync's agents
hippo install --no-schedule          # remove the job (--schedule turns it back on)
```

## Settings: `~/agent-memory/config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `sync_model` | `sonnet` | Model for sync's agents. The `HIPPO_MODEL` environment variable overrides it. |
| `max_turns` | `50` | Turn limit for each sync agent run. A run that hits it fails safely: the docs are restored and the work is retried next time. |
| `sync_at` | `03:00` | Time of the daily sync. |
| `schedule` | `true` | `false` after `hippo install --no-schedule`. |

## Tuning the turn limit and cost

Every agent run (init, the notes pass, the commits pass) adds one line to
`~/agent-memory/logs/agent-runs.jsonl`:

```json
{"at": "2026-11-22T03:00:41", "kind": "notes", "project": "my-app", "uuid": "...",
 "model": "sonnet", "turns": 14, "max_turns": 50, "cost_usd": 0.21,
 "outcome": "ok", "notes": 2}
```

- `kind`: `init`, `notes` (patch notes folded in) or `catch-up` (commits without notes)
- `outcome`: `ok`, `failed`, or `hit_turn_limit`
- `notes` / `commits`: how much work the run had
- `cost_usd`: the API-equivalent cost Claude Code reports (on a Pro/Max plan this counts toward usage limits instead)

To see the turns used per kind of run:

```bash
python3 -c "
import json, collections, statistics
runs = collections.defaultdict(list)
for line in open('$HOME/agent-memory/logs/agent-runs.jsonl'):
    r = json.loads(line); runs[r['kind']].append(r)
for kind, rs in runs.items():
    t = [r['turns'] or 0 for r in rs]
    print(f\"{kind:9} runs {len(rs):3}  turns median {statistics.median(t):4} max {max(t):3}  \"
          f\"hit limit {sum(r['outcome'] == 'hit_turn_limit' for r in rs)}  \"
          f\"cost \${sum(r['cost_usd'] or 0 for r in rs):.2f}\")
"
```

How to tune:
- **Runs hitting the limit** (`hit_turn_limit`) with a big batch (many notes or
  commits): raise `max_turns`, e.g. to 80.
- **Normal runs far below the limit** (typical: 9–20 turns): you can lower it
  to catch runaway runs sooner, but keep it well above the largest normal run.
- **Cost too high**: try `hippo install --sync-model haiku` and compare the
  docs it writes over a few days.
