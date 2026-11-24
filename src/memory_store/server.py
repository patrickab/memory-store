"""MCP stdio server over memory_store.store: argument parsing, the call, the formatted result."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from memory_store import store
from memory_store.cli import render_results
from memory_store.store import MemoryStoreError

INSTRUCTIONS = """\
Persistent memory shared across sessions and agents. Root from MEMORY_STORE_ROOT (default ~/.pi/memory_store).
1. list_memories() shows memory_stores; list_memories(memory_store) shows its memories and documents.
2. read_memories(memory_store, paths) before you write: it shows each fact with its [id].
3. apply_memory_ops: update a fact by id when its subject changes, never add a second fact on one subject.
4. Facts are atomic, verified, minimal words; nothing the repo already records.
5. A memory_store must exist (create_memory_store) and writes go only to its top level or a declared directory."""

mcp = MCPServer("memory-store", instructions=INSTRUCTIONS)


def _error(exc: MemoryStoreError) -> str:
    return f"error: {exc.code}: {exc.message}"


@mcp.tool(
    description="Create a memory_store (parents like mkdir -p) with its declared directories. "
    "Idempotent; a repeat call adds new directories."
)
def create_memory_store(
    memory_store: Annotated[str, Field(description="Relative path, e.g. 'user' or 'project/run-1'")],
    directories: Annotated[list[str], Field(description="Directories to declare, e.g. ['map', 'handoffs']")] = [],  # noqa: B006
) -> str:
    try:
        created = store.create_memory_store(memory_store, directories)
    except MemoryStoreError as exc:
        return _error(exc)
    declared = ", ".join(created["directories"]) or "(none)"
    return f"memory_store {created['memory_store']} · directories: {declared}"


@mcp.tool(
    description="Cheap index: nested memory_stores, directories, memories (description, active facts, updated) "
    "and documents. No memory_store lists the root's memory_stores; path lists a declared directory."
)
def list_memories(memory_store: str = "", path: str = "") -> str:
    try:
        return store.render_list(store.list_memories(memory_store, path))
    except MemoryStoreError as exc:
        return _error(exc)


@mcp.tool(
    description="Read memories (path without extension, rendered with fact [ids]) and documents (path ending .md, "
    "verbatim). Read before you write; the ids for update/retire come from here."
)
def read_memories(
    memory_store: str,
    paths: Annotated[list[str], Field(description="e.g. ['decisions', 'map/auth', 'run/contract.md']")],
    ids: bool = True,
) -> str:
    try:
        return store.read_memories(memory_store, paths, ids)
    except MemoryStoreError as exc:
        return _error(exc)


@mcp.tool(
    description="Batch of ops {op, path, id?, text?, description?}: add (text; description when the memory is new), "
    "update (id from read_memories, text), retire (id), describe (description), write_doc (path ending .md, text). "
    "One fact per subject: when it changes, update it, never add a second. Read before you write; "
    "if a memory contradicts the code or the user, fix it now. Atomic facts, minimal words, only what you verified, "
    "nothing the repo already records. Failed ops do not block the others."
)
def apply_memory_ops(
    memory_store: str,
    ops: Annotated[list[dict[str, Any]], Field(description="e.g. [{'op': 'add', 'path': 'decisions', 'text': '...'}]")],
) -> str:
    try:
        return render_results(store.apply_memory_ops(memory_store, ops))
    except MemoryStoreError as exc:
        return _error(exc)


def main() -> None:
    mcp.run("stdio")
