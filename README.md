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

## How it works

1. **`hippo link`** in a repo: an agent reads the code and writes a doc per feature.
2. **Every Claude Code session** in that repo starts with that memory, and ends by
   saving a short patch note of what changed and why.
3. **Every night** (or on `hippo sync`) the patch notes, and any commits made
   without a session, are folded into the feature docs.

Everything lives in `~/agent-memory/`; nothing is added to your repo except a
gitignored `.hippo` file and Hippo's hooks in the gitignored
`.claude/settings.local.json`.

## Commands

### `hippo install`
One-time machine setup: the `hippo` command, `~/agent-memory/`, and the daily
sync job (macOS). Safe to run again; it only fixes what's missing or out of date.

| Option | Does |
| --- | --- |
| `--sync-at HH:MM` | Time of the daily sync (default `03:00`) |
| `--sync-model MODEL` | Model for sync's agents, e.g. `sonnet` (default) or `haiku` |
| `--no-schedule` / `--schedule` | Remove the daily job / turn it back on |

Use it when:
- setting up Hippo on a new machine
- after updating Hippo, to refresh hooks in every linked repo
- changing the sync time or model: `hippo install --sync-at 02:30`
- you'd rather sync by hand: `hippo install --no-schedule`
- `hippo status` says the daily job is `NOT loaded`

### `hippo link`
Connects the current repo: creates its memory, adds Hippo's hooks for this repo
only, and runs the init agent to write the first feature docs (a few minutes).

| Option | Does |
| --- | --- |
| `--no-init` | Link without writing feature docs yet |
| `--reinit` | Already linked: regenerate the feature docs (old ones are backed up; decisions are kept) |
| `--reuse UUID` | Reconnect to existing memory, e.g. after re-cloning the repo |
| `--new` | Start fresh even if memory for this repo's remote already exists |

Use it when:
- you start using Hippo in a repo
- you re-cloned a repo or moved it: Hippo finds the old memory by its remote and offers to reuse it
- the feature docs drifted badly or the code was restructured: `hippo link --reinit`

### `hippo open`
Shows what Hippo knows about the current repo.

| Option | Does |
| --- | --- |
| *(none)* | Summary: features, unsynced notes, review items |
| `-f NAME` | One feature's overview and technical notes (add `--decisions` for its decisions) |
| `--review` | Items that need your judgment (see `hippo review done`) |
| `--path` | Print only the memory folder's path |
| `--finder` | Also open the memory folder in Finder |

Use it when:
- you want to see what the agent will "remember" in its next session
- checking why a decision was made: `hippo open -f auth --decisions`
- the nightly sync flagged something: `hippo open --review`

### `hippo sync`
Folds patch notes and commits made without a session into the feature docs
now, instead of waiting for the night. Changes are checked afterwards and
undone if anything looks wrong; failed work is retried next time.

| Option | Does |
| --- | --- |
| `--all` | Every linked repo, from any folder |

Use it when:
- you want the docs current right now, e.g. before a big task in a fresh session
- a nightly run failed (you got a notification) and you want to see why
- before `hippo unlink`, so unsynced notes end up in the docs

### `hippo status`
Every linked repo at a glance, from any folder: last sync, unsynced patch
notes, open review items, commits since the last sync, plus whether the daily
job is loaded, how its last run went, and whether Claude Code is logged in.
Read-only.

Use it when:
- checking the nightly sync is doing its job
- something seems off: problems are listed with the command that fixes them
  (e.g. a repo that was moved or deleted)
- you forgot which repos are linked

### `hippo unlink [repo]`
Disconnects a repo: removes `.hippo` and Hippo's hooks (your own settings are
kept), drops it from the nightly sync, and moves its memory to
`~/agent-memory/archived-projects/`. Nothing is deleted. Asks first (Enter means no).

| Option | Does |
| --- | --- |
| `repo` | A repo name or uuid as `hippo status` shows it; works from any folder. Default: the current repo |
| `-y`, `--yes` | Don't ask (needed outside a terminal) |

Use it when:
- you stop working on a project
- a repo was deleted or moved and `hippo status` flags it: `hippo unlink <name>`
- you linked a repo by mistake

Linking the same repo again starts fresh (restoring the archive is planned).

### `hippo review done ID...`
Dismisses items from `hippo open --review` (e.g. `hippo review done r-001 r-003`).
Dismissed items don't come back. The others also close on their own at a later
sync once they're no longer true: code no feature covered is now listed by a
feature (e.g. after a session worked on it), or a missing key file is back.

Use it when an item is noise, or you've dealt with it.

## Commands Hippo's agents use

You don't run these; they're how agents write memory, so every change goes
through a command that validates it (agents can't edit Hippo's files directly).

| Command | Used by | Does |
| --- | --- | --- |
| `hippo patch new` | session agent | Saves the session's patch note (body on stdin) |
| `hippo feature set` | init and sync agents | Creates or updates a feature's `feature.json` |
| `hippo decision add` / `supersede` | sync agent | Records a decision you agreed to in a session, or marks an old one replaced |
| `hippo review add` | sync agent | Flags something that needs your judgment |

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
