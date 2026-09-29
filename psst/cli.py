"""The `psst` command. Run `psst --help`, or `psst <command> --help` for any command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent
LEGACY_REPO = Path(os.environ.get("PSST_LEGACY_REPO", ROOT))


def cmd_db_migrate(args) -> int:
    applied = db.migrate()
    print("Applied: " + ", ".join(applied) if applied else "The database is up to date.")
    return 0


def cmd_import_legacy(args) -> int:
    from . import legacy
    with db.connect(actor="legacy-import") as conn:
        result = legacy.run_import(conn, Path(args.repo))
    print(f"Imported {result.places} places, {result.facts} facts, {result.sources} sources.")
    for old, new in result.merged:
        print(f"  merged {old} into {new} (same coordinate source)")
    return 0


COMMANDS: list[tuple[str, str, object, list]] = []


def command(name: str, help_text: str, *arguments):
    def register(function):
        COMMANDS.append((name, help_text, function, list(arguments)))
        return function
    return register


def arg(*names, **options):
    return names, options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psst", description="The Psst content pipeline.")
    groups = parser.add_subparsers(dest="group", metavar="command")
    tree: dict[str, argparse._SubParsersAction] = {}
    for name, help_text, function, arguments in COMMANDS:
        parts = name.split()
        if len(parts) == 1:
            sub = groups.add_parser(parts[0], help=help_text, description=help_text)
        else:
            if parts[0] not in tree:
                group = groups.add_parser(parts[0], help=f"{parts[0]} commands")
                tree[parts[0]] = group.add_subparsers(dest="action", metavar="action", required=True)
            sub = tree[parts[0]].add_parser(parts[1], help=help_text, description=help_text)
        for names, options in arguments:
            sub.add_argument(*names, **options)
        sub.set_defaults(func=function)
    return parser


command("db migrate", "Apply database migrations.")(cmd_db_migrate)
command("import legacy", "Import schema 1 area files (idempotent).",
        arg("--repo", default=str(LEGACY_REPO), help="repository with areas/ (default: this one)"))(cmd_import_legacy)


def main(argv: list[str] | None = None) -> int:
    # Every command module registers itself on import.
    from . import commands  # noqa: F401
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return 130
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
