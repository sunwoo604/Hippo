"""Entry point for the `hippo` command."""

import argparse
import sys

from hippo import (__version__, agent, decisions, features, hooks, install, link, open_memory,
                   patches, review, status, sync, ui, unlink)
from hippo.errors import HippoError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hippo",
        description="Persistent, structured project memory for coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"hippo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("install", help="one-time machine setup (also sets up the daily sync)")
    on_off = p.add_mutually_exclusive_group()
    on_off.add_argument("--no-schedule", action="store_true", help="remove the daily sync job")
    on_off.add_argument("--schedule", action="store_true", help="turn the daily sync job back on")
    p.add_argument("--sync-at", metavar="HH:MM", help="time of the daily sync (default 03:00)")
    p.add_argument("--sync-model", metavar="MODEL",
                   help="model for sync's agents, e.g. sonnet or haiku (default sonnet)")
    p.set_defaults(func=install.run)

    p = sub.add_parser("link", help="connect the current repo and run init")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--reuse", metavar="UUID", help="reuse existing memory with this uuid")
    choice.add_argument("--new", action="store_true", help="start fresh even if memory exists")
    choice.add_argument("--reinit", action="store_true",
                        help="already linked: regenerate the feature docs")
    p.add_argument("--no-init", action="store_true", help="link without running the init agent")
    p.set_defaults(func=link.run)

    unlink.add_parser(sub)

    p = sub.add_parser("sync", help="fold patch notes into the feature docs now")
    p.add_argument("--all", action="store_true", help="sync every linked project")
    p.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)  # the daily job
    p.set_defaults(func=sync.run)

    p = sub.add_parser("status", help="every linked repo at a glance, plus the daily job")
    p.set_defaults(func=status.run)

    p = sub.add_parser("open", help="show the current repo's memory")
    view = p.add_mutually_exclusive_group()
    view.add_argument("-f", "--feature", metavar="NAME", help="show one feature's details")
    view.add_argument("--path", action="store_true", help="print only the memory dir path")
    view.add_argument("--review", action="store_true", help="list the items that need your review")
    p.add_argument("--decisions", action="store_true", help="with --feature: show its decisions")
    p.add_argument("--finder", action="store_true", help="also reveal it in Finder")
    p.set_defaults(func=open_memory.run)

    features.add_parser(sub)
    patches.add_parser(sub)
    decisions.add_parser(sub)
    review.add_parser(sub)

    # Internal: PreToolUse hook for Hippo's headless agents (no help = not listed).
    # Internal: Claude Code hook handlers, run by Claude Code itself (not listed).
    p = sub.add_parser("_hook")
    p.add_argument("event", choices=hooks.EVENTS)
    p.set_defaults(func=lambda a: hooks.main(a.event))

    p = sub.add_parser("_guard-bash")
    p.add_argument("--allow", action="append", default=[], metavar="PREFIX")
    p.set_defaults(func=lambda a: agent.guard_main(a.allow))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except HippoError as e:
        print(f"hippo: {e}", file=sys.stderr)
        ui.next_steps(e.next_steps, file=sys.stderr)
        return 1
