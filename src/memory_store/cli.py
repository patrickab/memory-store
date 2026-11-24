"""The memory-store command line over memory_store.store: argument parsing, the call, the formatted result."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from memory_store import store
from memory_store.store import MemoryStoreError


def render_results(results: list[dict]) -> str:
    """One line per op result: `ok <op> <path> [id]` or `failed <op> <path>: <code>: <message>`."""
    lines = []
    for result in results:
        if result["ok"]:
            new_id = f" {result['id']}" if result.get("id") else ""
            lines.append(f"ok {result['op']} {result['path']}{new_id}")
        else:
            lines.append(f"failed {result['op']} {result['path']}: {result['error']}: {result['message']}")
    return "\n".join(lines) + "\n" if lines else "(no ops)\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-store", description="File-based memory for agents.")
    parser.add_argument("--root", help="root directory (default: MEMORY_STORE_ROOT or ~/.pi/memory_store)")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create a memory_store and declare directories (idempotent)")
    create.add_argument("memory_store")
    create.add_argument("directories", nargs="*", metavar="dir")

    ls = commands.add_parser("ls", help="list memory_stores, directories, memories and documents")
    ls.add_argument("memory_store", nargs="?", default="")
    ls.add_argument("path", nargs="?", default="")

    read = commands.add_parser("read", help="read memories (no extension) and documents (.md)")
    read.add_argument("memory_store")
    read.add_argument("paths", nargs="+", metavar="path")
    read.add_argument("--no-ids", dest="ids", action="store_false", help="omit fact ids")

    apply = commands.add_parser("apply", help="apply a JSON list of ops (from --ops or stdin)")
    apply.add_argument("memory_store")
    apply.add_argument("--ops", help='e.g. \'[{"op": "add", "path": "decisions", "text": "..."}]\'')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            created = store.create_memory_store(args.memory_store, args.directories, root=args.root)
            declared = ", ".join(created["directories"]) or "(none)"
            print(f"memory_store {created['memory_store']} · directories: {declared}")
        elif args.command == "ls":
            print(store.render_list(store.list_memories(args.memory_store, args.path, root=args.root)), end="")
        elif args.command == "read":
            print(store.read_memories(args.memory_store, args.paths, args.ids, root=args.root), end="")
        else:
            raw = args.ops if args.ops is not None else sys.stdin.read()
            try:
                ops = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"error: invalid_json: {exc}", file=sys.stderr)
                return 1
            if not isinstance(ops, list):
                print("error: invalid_json: ops must be a JSON list", file=sys.stderr)
                return 1
            results = store.apply_memory_ops(args.memory_store, ops, root=args.root)
            print(render_results(results), end="")
            failed = sum(1 for r in results if not r["ok"])
            if failed:
                print(f"error: {failed} of {len(results)} ops failed", file=sys.stderr)
                return 1
    except MemoryStoreError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
