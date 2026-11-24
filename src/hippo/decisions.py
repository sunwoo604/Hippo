"""Decisions: features/<slug>/decisions.json, choices the user explicitly agreed to.

Only Hippo's consolidation agent records them, from the Decisions section of
patch notes, through these commands (never by editing JSON):

  hippo decision add <slug> --decision ... --why ... [--rejected ...] \\
        [--also-affects <slug>] --source patches/<note>.md
  hippo decision supersede <old-id> --by <new-id>
"""

import argparse
import json
import os
import re
from pathlib import Path

from hippo import features, ui
from hippo.errors import HippoError
from hippo.fsutil import atomic_write_text
from hippo.memory import frontmatter
from hippo.project import resolve

ID = re.compile(r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<n>\d{3,})$")


def decisions_file(memory_dir: Path, slug: str) -> Path:
    return memory_dir / "features" / slug / "decisions.json"


def load(memory_dir: Path, slug: str) -> list[dict]:
    f = decisions_file(memory_dir, slug)
    return json.loads(f.read_text()) if f.exists() else []


def save(memory_dir: Path, slug: str, decisions: list[dict]) -> None:
    atomic_write_text(decisions_file(memory_dir, slug),
                      json.dumps(decisions, indent=2, ensure_ascii=False) + "\n")


def next_id(slug: str, decisions: list[dict]) -> str:
    numbers = [int(m["n"]) for d in decisions if (m := ID.match(d.get("id", "")))]
    return f"{slug}-{max(numbers, default=0) + 1:03d}"


def _require_hippo_agent() -> None:
    if not os.environ.get("HIPPO_AGENT"):
        raise HippoError(
            "decisions are recorded by Hippo's consolidation, from patch notes.",
            next_steps=["Describe the decision in a patch note's '## Decisions' section "
                        "(only if the user explicitly agreed to it)."],
        )


def _one_line(name: str, value: str | None, errors: list[str]) -> str:
    value = (value or "").strip()
    if not value:
        errors.append(f"--{name} is required")
    elif "\n" in value:
        errors.append(f"--{name} must be one line")
    return value


def run_add(args: argparse.Namespace) -> int:
    _require_hippo_agent()
    p = resolve(Path.cwd())
    existing = {f.slug for f in features.load_all(p.dir)[0]}
    errors = []
    if args.slug not in existing:
        errors.append(f"feature '{args.slug}' doesn't exist (create it first with `hippo feature set`)")
    decision = _one_line("decision", args.decision, errors)
    why = _one_line("why", args.why, errors)
    rejected = [r.strip() for r in args.rejected or [] if r.strip()]
    also = []
    for slug in args.also_affects or []:
        if slug not in existing:
            errors.append(f"--also-affects '{slug}' isn't a feature")
        elif slug != args.slug and slug not in also:
            also.append(slug)
    source = (args.source or "").strip()
    note = p.dir / source
    if not source.startswith("patches/") or not note.is_file():
        errors.append(f"--source must be a patch note in this project, like patches/<file>.md "
                      f"(got '{source}')")
    if errors:
        raise HippoError("decision was not recorded:\n" + "\n".join(f"  - {e}" for e in errors),
                         next_steps=["Fix the values above and run the same command again."])

    decisions = load(p.dir, args.slug)
    for d in decisions:
        if d.get("source") == source and d.get("decision") == decision:
            raise HippoError(f"already recorded as {d['id']} (same decision from the same note)")
    created = str(frontmatter(note.read_text()).get("created_at", ""))
    entry = {
        "id": next_id(args.slug, decisions),
        "date": created[:10] or None,
        "status": "active",
        "superseded_by": None,
        "decision": decision,
        "why": why,
        "rejected": rejected,
        "also_affects": also,
        "source": source,
    }
    save(p.dir, args.slug, decisions + [entry])
    ui.say("created", f"decision {entry['id']} in features/{args.slug}/decisions.json")
    return 0


def find(memory_dir: Path, decision_id: str) -> tuple[str, list[dict], dict] | None:
    m = ID.match(decision_id)
    if not m:
        return None
    decisions = load(memory_dir, m["slug"])
    for d in decisions:
        if d.get("id") == decision_id:
            return m["slug"], decisions, d
    return None


def run_supersede(args: argparse.Namespace) -> int:
    _require_hippo_agent()
    p = resolve(Path.cwd())
    old, new = find(p.dir, args.old), find(p.dir, args.by)
    errors = []
    if old is None:
        errors.append(f"no decision '{args.old}'")
    if new is None:
        errors.append(f"no decision '{args.by}' (record the new decision first)")
    if old and old[2].get("status") != "active":
        errors.append(f"'{args.old}' is already {old[2].get('status')}")
    if args.old == args.by:
        errors.append("a decision can't supersede itself")
    if errors:
        raise HippoError("nothing was superseded:\n" + "\n".join(f"  - {e}" for e in errors))
    slug, decisions, entry = old
    entry.update(status="superseded", superseded_by=args.by)
    save(p.dir, slug, decisions)
    ui.say("updated", f"decision {args.old} superseded by {args.by}")
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("decision", help="record decisions from patch notes (used by consolidation)")
    dsub = p.add_subparsers(dest="decision_command", required=True, metavar="<action>")
    a = dsub.add_parser("add", help="record a decision the user agreed to")
    a.add_argument("slug", help="the feature the decision belongs to")
    a.add_argument("--decision", help="the choice, in one line")
    a.add_argument("--why", help="why it was chosen, in one line")
    a.add_argument("--rejected", action="append", metavar="TEXT",
                   help="an alternative that was turned down, and why (repeatable)")
    a.add_argument("--also-affects", action="append", metavar="SLUG",
                   help="another feature the decision affects (repeatable)")
    a.add_argument("--source", help="the patch note it came from: patches/<file>.md")
    a.set_defaults(func=run_add)
    s = dsub.add_parser("supersede", help="mark an older decision as replaced")
    s.add_argument("old", metavar="OLD_ID")
    s.add_argument("--by", required=True, metavar="NEW_ID")
    s.set_defaults(func=run_supersede)
