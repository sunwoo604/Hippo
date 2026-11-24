"""needs-review.json: things in the memory that need the user's judgment.

Most items are found by code during `hippo sync` and close themselves once
they stop being true:
  unclaimed         files changed on the default branch, with no session note,
                    that no feature lists as a key file (one item per folder)
  missing-key-file  a feature's key file that isn't on the default branch
Hippo's agents add judgment calls with `hippo review add` (kind "agent"); those
stay until the user dismisses them with `hippo review done <id>`.

  [{"id": "r-001", "created": "2026-11-18", "kind": "unclaimed",
    "path": "src/payments/", "detail": "...", "files": [...], "commits": [...],
    "status": "open" | "resolved" | "dismissed", "closed": "2026-11-20"}]

A dismissed item is never reopened, so the same finding isn't raised again.
"""

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from hippo import features, ui
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text
from hippo.project import resolve

FILE = "needs-review.json"
KEEP_RESOLVED_DAYS = 30
MAX_DETAIL = 300


def load(memory_dir: Path) -> list[dict]:
    f = memory_dir / FILE
    if not f.exists():
        return []
    try:
        items = json.loads(f.read_text())
    except json.JSONDecodeError:
        return []
    return items if isinstance(items, list) else []


def save(memory_dir: Path, items: list[dict]) -> None:
    cutoff = (date.today() - timedelta(days=KEEP_RESOLVED_DAYS)).isoformat()
    items = [i for i in items if not (i.get("status") == "resolved" and i.get("closed", "") < cutoff)]
    atomic_write_text(memory_dir / FILE, json.dumps(items, indent=2, ensure_ascii=False) + "\n")


def open_items(memory_dir: Path) -> list[dict]:
    return [i for i in load(memory_dir) if i.get("status") == "open"]


def _next_id(items: list[dict]) -> str:
    nums = [int(i["id"][2:]) for i in items if str(i.get("id", "")).startswith("r-")
            and i["id"][2:].isdigit()]
    return f"r-{max(nums, default=0) + 1:03d}"


def _close(item: dict, status: str) -> None:
    item["status"] = status
    item["closed"] = date.today().isoformat()


def reconcile(memory_dir: Path, found: list[dict], checkable: set[str]) -> list[dict]:
    """Merge code-detected findings into the file and close what's no longer true.

    `found`: items with kind/path(/feature)/detail(/files/commits).
    `checkable`: kinds whose open items not in `found` are now resolved.
    Returns the items newly opened (or reopened).
    """
    items = load(memory_dir)
    key = lambda i: (i.get("kind"), i.get("feature"), i.get("path"))
    by_key = {key(i): i for i in items if i.get("kind") != "agent"}
    found_keys = set()
    opened = []
    today = date.today().isoformat()
    for new in found:
        k = key(new)
        found_keys.add(k)
        old = by_key.get(k)
        if old is None:
            item = {"id": _next_id(items), "created": today, **new, "status": "open"}
            items.append(item)
            by_key[k] = item
            opened.append(item)
        elif old["status"] == "dismissed":
            continue
        else:
            for field in ("files", "commits"):
                if field in new:
                    old[field] = sorted(set(old.get(field, [])) | set(new[field]))
            old["detail"] = new["detail"]
            if old["status"] == "resolved":
                old["status"] = "open"
                old.pop("closed", None)
                opened.append(old)
    for item in items:
        if item.get("status") == "open" and item.get("kind") in checkable and key(item) not in found_keys:
            _close(item, "resolved")
    save(memory_dir, items)
    return opened


# --- commands -------------------------------------------------------------------

def run_add(args: argparse.Namespace) -> int:
    if not os.environ.get("HIPPO_AGENT"):
        raise HippoError("review items are added by Hippo's own agents during `hippo sync`.")
    detail = " ".join(args.detail.split())
    if not detail:
        raise HippoError("say what needs the user's judgment, in one sentence.")
    if len(detail) > MAX_DETAIL:
        raise HippoError(f"keep it to one sentence (at most {MAX_DETAIL} characters).")
    p = resolve(Path.cwd())
    if args.feature and not features.feature_file(p.dir, args.feature).exists():
        raise HippoError(f"no feature named '{args.feature}'. Leave out --feature if this is "
                         f"about code no feature covers yet.")
    items = load(p.dir)
    for i in items:
        if i.get("kind") == "agent" and i.get("detail") == detail and i.get("feature") == args.feature:
            ui.say("exists", f"{i['id']} already says this")
            return 0
    item = {"id": _next_id(items), "created": date.today().isoformat(), "kind": "agent",
            "feature": args.feature, "path": None, "detail": detail, "status": "open"}
    items.append(item)
    save(p.dir, items)
    ui.say("added", f"{item['id']}: {detail}")
    return 0


def run_done(args: argparse.Namespace) -> int:
    if os.environ.get("HIPPO_AGENT"):
        raise HippoError("only the user dismisses review items.")
    p = resolve(Path.cwd())
    items = load(p.dir)
    by_id = {i.get("id"): i for i in items}
    unknown = [x for x in args.ids if x not in by_id]
    if unknown:
        raise HippoError(f"no review item {', '.join(unknown)}",
                         next_steps=["Run `hippo open --review` to list them."])
    for x in args.ids:
        if by_id[x]["status"] == "open":
            _close(by_id[x], "dismissed")
            ui.say("dismissed", f"{x}: {by_id[x]['detail']}")
        else:
            ui.say("ok", f"{x} is already {by_id[x]['status']}")
    save(p.dir, items)
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("review", help="dismiss items in needs-review")
    rsub = p.add_subparsers(dest="review_command", required=True, metavar="<action>")
    d = rsub.add_parser("done", help="dismiss review items (see `hippo open --review`)")
    d.add_argument("ids", nargs="+", metavar="ID")
    d.set_defaults(func=run_done)
    a = rsub.add_parser("add", help="(Hippo's agents) add an item needing the user's judgment")
    a.add_argument("--feature", metavar="SLUG")
    a.add_argument("detail", metavar="SENTENCE")
    a.set_defaults(func=run_add)
