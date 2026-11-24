"""Path validation and resolution: segments, root, memory_stores, writable targets."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

MARKER = ".memory_store"
LOCKS_DIR = ".locks"
SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


class MemoryStoreError(Exception):
    """An operation failed; `code` is one of the documented error codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def default_root() -> Path:
    env = os.environ.get("MEMORY_STORE_ROOT")
    return Path(env).expanduser() if env else Path.home() / ".pi" / "memory_store"


def resolve_root(root: str | Path | None) -> Path:
    return Path(root).expanduser() if root is not None else default_root()


def split_path(path: object) -> list[str]:
    """Split a relative `/` path into validated segments; leading-dot segments cannot be addressed."""
    if not isinstance(path, str) or not path:
        raise MemoryStoreError("invalid_path", f"path must be a non-empty string: {path!r}")
    if path.startswith("/"):
        raise MemoryStoreError("invalid_path", f"absolute paths are not allowed: {path}")
    segments = path.split("/")
    for segment in segments:
        if not SEGMENT.fullmatch(segment) or segment.startswith("."):
            raise MemoryStoreError("invalid_path", f"invalid segment {segment!r} in {path}")
    return segments


def is_memory_store(folder: Path) -> bool:
    return (folder / MARKER).is_file()


def inside_root(root: Path, path: Path) -> Path:
    """`path` unchanged; refuses it when symlinks take it out of the (resolved) root."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise MemoryStoreError("invalid_path", f"{path.relative_to(root)} leaves the root through a symlink")
    return path


def store_dir(root: Path, memory_store: str) -> Path:
    folder = inside_root(root, root.joinpath(*split_path(memory_store)))
    if not is_memory_store(folder):
        raise MemoryStoreError("memory_store_missing", f"no memory_store at {memory_store}")
    return folder


def read_directories(store: Path) -> list[str]:
    marker = store / MARKER
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MemoryStoreError("memory_store_missing", f"no marker in {store}") from None
    except json.JSONDecodeError as exc:
        raise MemoryStoreError("corrupt", f"{marker}: invalid JSON: {exc}") from None
    directories = data.get("directories") if isinstance(data, dict) else None
    if not isinstance(directories, list) or not all(isinstance(d, str) for d in directories):
        raise MemoryStoreError("corrupt", f"{marker}: 'directories' must be a list of strings")
    return directories


def is_document(path: str) -> bool:
    return path.endswith(".md")


def target_file(store: Path, segments: list[str]) -> Path:
    """File of a memory (`<path>.json`) or a document (`<path>`, ends `.md`)."""
    if segments[-1].endswith(".md"):
        return store.joinpath(*segments)
    return store.joinpath(*segments[:-1], segments[-1] + ".json")


def check_writable(store: Path, segments: list[str]) -> None:
    """Writes go to the memory_store top level or inside a declared directory, never into a nested memory_store."""
    if len(segments) == 1:
        return
    if segments[0] not in read_directories(store):
        raise MemoryStoreError("undeclared_directory", f"{segments[0]} is not a declared directory")
    folder = store
    for segment in segments[:-1]:
        folder = folder / segment
        if is_memory_store(folder):
            raise MemoryStoreError("undeclared_directory", f"{folder.relative_to(store)} is a nested memory_store")
