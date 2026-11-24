"""The memory-store operations: memory_stores, fact lists, documents; locking, ids, rendering."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from memory_store.paths import (
    LOCKS_DIR,
    MARKER,
    MemoryStoreError,
    check_writable,
    default_root,
    inside_root,
    is_document,
    is_memory_store,
    read_directories,
    resolve_root,
    split_path,
    store_dir,
    target_file,
)

__all__ = [
    "MemoryStoreError",
    "apply_memory_ops",
    "create_memory_store",
    "default_root",
    "list_memories",
    "move_memory_store",
    "read_memories",
    "read_memory",
    "render_list",
]

ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
SHORT_ID_MAX_FACTS = 2000
FACT_KEYS = ("id", "text", "created", "updated", "retired", "superseded_by")
OPS = ("add", "update", "retire", "describe", "write_doc")


def _iso(timestamp: float | None = None) -> str:
    moment = datetime.now(UTC) if timestamp is None else datetime.fromtimestamp(timestamp, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _locked(root: Path, file: Path | None = None, *, shared: bool = False) -> Iterator[None]:
    """Flock kept under `<root>/.locks/`: on the lock file of `file`, or on `root.lock` when `file` is None.

    Writers hold `root.lock` shared, `move_memory_store` holds it exclusive; take it before any file lock.
    """
    locks = root / LOCKS_DIR
    locks.mkdir(mode=0o700, exist_ok=True)
    name = "root" if file is None else hashlib.sha256(file.relative_to(root).as_posix().encode()).hexdigest()[:32]
    fd = os.open(locks / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write_atomic(file: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=f".{file.name}.", suffix=".tmp")  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, file)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _dump_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _load_memory(file: Path) -> dict | None:
    """The memory at `file`, or None when it does not exist; refuses invalid files as `corrupt`."""
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MemoryStoreError("corrupt", f"{file.name}: invalid JSON: {exc}") from None
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list) or not isinstance(data.get("description", ""), str):
        raise MemoryStoreError("corrupt", f"{file.name}: expected {{'description': str, 'facts': list}}")
    seen: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict) or not isinstance(fact.get("id"), str) or not isinstance(fact.get("text"), str):
            raise MemoryStoreError("corrupt", f"{file.name}: every fact needs a string id and text")
        if not all(isinstance(fact.get(key), str | None) for key in ("updated", "retired")):
            raise MemoryStoreError("corrupt", f"{file.name}: updated and retired must be strings or null")
        if fact["id"] in seen:
            raise MemoryStoreError("corrupt", f"{file.name}: duplicate id {fact['id']}")
        seen.add(fact["id"])
    return data


def _dump_memory(data: dict) -> str:
    """Stable key order: description, facts, then any extra keys; facts in FACT_KEYS order, extras after."""

    def ordered(item: dict, keys: Sequence[str]) -> dict:
        return {**{k: item.get(k) for k in keys}, **{k: item[k] for k in sorted(item) if k not in keys}}

    data = {**data, "description": data.get("description", ""), "facts": [ordered(f, FACT_KEYS) for f in data["facts"]]}
    return _dump_json(ordered(data, ("description", "facts")))


def _active_facts(data: dict) -> list[dict]:
    """Active facts, newest `updated` first; ties keep the later fact first."""
    active = [f for f in reversed(data["facts"]) if not f.get("retired")]
    return sorted(active, key=lambda f: f.get("updated") or "", reverse=True)


def _random_id(length: int) -> str:
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(length))


def _new_id(facts: list[dict]) -> str:
    """A fresh id, unique against every fact in the memory including retired ones."""
    taken = {f["id"] for f in facts}
    length = 3 if len(facts) <= SHORT_ID_MAX_FACTS else 4
    while 36**length < 2 * len(facts):
        length += 1
    while True:
        candidate = _random_id(length)
        if candidate not in taken:
            return candidate


def create_memory_store(memory_store: str, directories: Sequence[str] = (), *, root: str | Path | None = None) -> dict:
    root_dir = resolve_root(root)
    segments = split_path(memory_store)
    for directory in directories:
        if len(split_path(directory)) != 1:
            raise MemoryStoreError("invalid_path", f"a directory is one path segment: {directory}")
    with suppress(FileExistsError):
        root_dir.mkdir(parents=True, mode=0o700)
        os.chmod(root_dir, 0o700)
    with _locked(root_dir, shared=True):
        return _create_locked(root_dir, segments, directories)


def _create_locked(root_dir: Path, segments: list[str], directories: Sequence[str]) -> dict:
    store = inside_root(root_dir, root_dir.joinpath(*segments))
    try:
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (FileExistsError, NotADirectoryError):
        raise MemoryStoreError("invalid_path", f"{'/'.join(segments)} collides with an existing file") from None
    marker = store / MARKER
    with _locked(root_dir, marker):
        existing = read_directories(store) if marker.exists() else None
        declared = sorted(set(existing or ()) | set(directories))
        for directory in declared:
            try:
                (store / directory).mkdir(exist_ok=True, mode=0o700)
            except FileExistsError:
                raise MemoryStoreError("invalid_path", f"{directory} collides with an existing file") from None
        if declared != existing:
            _write_atomic(marker, _dump_json({"directories": declared}))
    return {"memory_store": "/".join(segments), "directories": declared}


def _find_memory_stores(root: Path, folder: Path, skip: Sequence[str] = ()) -> list[str]:
    """Memory_stores below `folder`, descending through plain folders but not into memory_stores."""
    found: list[str] = []
    for child in sorted(folder.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in skip:
            continue
        if is_memory_store(child):
            found.append(child.relative_to(root).as_posix())
        else:
            found.extend(_find_memory_stores(root, child))
    return found


def _memory_entry(store: Path, file: Path) -> dict:
    entry: dict = {"path": file.relative_to(store).with_suffix("").as_posix()}
    try:
        data = _load_memory(file) or {"facts": []}
    except MemoryStoreError:
        return {**entry, "description": "", "facts": 0, "updated": None, "error": "corrupt"}
    active = _active_facts(data)
    updated = max((f.get("updated") or "" for f in active), default="") or None
    return {**entry, "description": data.get("description", ""), "facts": len(active), "updated": updated}


def list_memories(memory_store: str = "", path: str = "", *, root: str | Path | None = None) -> dict:
    root_dir = resolve_root(root)
    listing: dict = {"memory_stores": [], "directories": [], "memories": [], "documents": []}
    if not memory_store:
        if path:
            raise MemoryStoreError("invalid_path", "path needs a memory_store")
        if root_dir.is_dir():
            listing["memory_stores"] = _find_memory_stores(root_dir, root_dir)
        return listing
    store = store_dir(root_dir, memory_store)
    declared = read_directories(store)
    if not path:
        folder = store
        listing["memory_stores"] = _find_memory_stores(root_dir, store, skip=declared)
        listing["directories"] = sorted(declared)
    else:
        segments = split_path(path)
        if segments[0] not in declared:
            raise MemoryStoreError("undeclared_directory", f"{segments[0]} is not a declared directory")
        folder = inside_root(root_dir, store.joinpath(*segments))
        if not folder.is_dir():
            if len(segments) == 1:
                return listing
            raise MemoryStoreError("not_found", f"no directory {path}")
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                key = "memory_stores" if is_memory_store(child) else "directories"
                base = root_dir if key == "memory_stores" else store
                listing[key].append(child.relative_to(base).as_posix())
    for child in sorted(folder.iterdir()):
        if not child.is_file() or child.name.startswith("."):
            continue
        if child.suffix == ".json":
            listing["memories"].append(_memory_entry(store, child))
        elif child.suffix == ".md":
            stat = child.stat()
            relative = child.relative_to(store).as_posix()
            listing["documents"].append({"path": relative, "bytes": stat.st_size, "modified": _iso(stat.st_mtime)})
    return listing


def render_list(listing: dict) -> str:
    lines: list[str] = []
    if listing.get("memory_stores"):
        lines.append("memory_stores: " + ", ".join(listing["memory_stores"]))
    if listing.get("directories"):
        lines.append("directories: " + ", ".join(listing["directories"]))
    if listing.get("memories"):
        lines.append("memories:")
        for m in listing["memories"]:
            if m.get("error"):
                lines.append(f"- {m['path']} — {m['error']}")
            else:
                description = f" — {m['description']}" if m["description"] else ""
                lines.append(f"- {m['path']}{description} · {m['facts']} facts · updated {m['updated'] or '-'}")
    if listing.get("documents"):
        lines.append("documents:")
        for d in listing["documents"]:
            lines.append(f"- {d['path']} · {d['bytes']} bytes · modified {d['modified']}")
    return "\n".join(lines) + "\n" if lines else "(empty)\n"


def _read_existing_memory(root: Path, store: Path, path: str) -> dict:
    segments = split_path(path)
    if is_document(path):
        raise MemoryStoreError("invalid_path", f"{path} is a document, not a memory")
    data = _load_memory(inside_root(root, target_file(store, segments)))
    if data is None:
        raise MemoryStoreError("not_found", f"no memory {path}")
    return data


def read_memory(memory_store: str, path: str, *, root: str | Path | None = None) -> dict:
    root_dir = resolve_root(root)
    data = _read_existing_memory(root_dir, store_dir(root_dir, memory_store), path)
    return {"description": data.get("description", ""), "facts": _active_facts(data)}


def read_memories(memory_store: str, paths: Sequence[str], ids: bool = True, *, root: str | Path | None = None) -> str:
    root_dir = resolve_root(root)
    store = store_dir(root_dir, memory_store)
    sections: list[str] = []
    for path in paths:
        try:
            if isinstance(path, str) and is_document(path):
                try:
                    text = inside_root(root_dir, target_file(store, split_path(path))).read_text(encoding="utf-8")
                except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                    raise MemoryStoreError("not_found", f"no document {path}") from None
                sections.append(f"## {path}\n{text}".rstrip("\n"))
                continue
            data = _read_existing_memory(root_dir, store, path)
        except MemoryStoreError as exc:
            sections.append(f"## {path} — {exc.code}: {exc.message}")
            continue
        heading = f"## {path} — {data['description']}" if data.get("description") else f"## {path}"
        facts = _active_facts(data)
        lines = [f"- [{f['id']}] {f['text']}" if ids else f"- {f['text']}" for f in facts] or ["(no facts)"]
        sections.append("\n".join([heading, *lines]))
    return "\n\n".join(sections) + "\n" if sections else ""


def _optional_str(op: dict, key: str) -> str | None:
    value = op.get(key)
    if value is not None and not isinstance(value, str):
        raise MemoryStoreError("invalid_op", f"{key} must be a string")
    return value


def _op_target(root: Path, store: Path, op: object) -> Path:
    """Validate one op and return the file it writes."""
    if not isinstance(op, dict):
        raise MemoryStoreError("invalid_op", "an op must be an object")
    name = op.get("op")
    if name not in OPS:
        raise MemoryStoreError("invalid_op", f"unknown op {name!r}; expected one of {', '.join(OPS)}")
    segments = split_path(op.get("path"))
    if (name == "write_doc") != is_document(op["path"]):
        kind = "write_doc needs a .md document path" if name == "write_doc" else f"{name} needs a memory path (no .md)"
        raise MemoryStoreError("invalid_path", f"{kind}: {op['path']}")
    text = _optional_str(op, "text")
    op_id = _optional_str(op, "id")
    description = _optional_str(op, "description")
    if name in ("add", "update") and not text:
        raise MemoryStoreError("invalid_op", f"{name} needs a non-empty text")
    if name == "write_doc" and text is None:
        raise MemoryStoreError("invalid_op", "write_doc needs text")
    if name in ("update", "retire") and not op_id:
        raise MemoryStoreError("invalid_op", f"{name} needs an id")
    if name == "add" and op_id is not None:
        raise MemoryStoreError("invalid_op", "add takes no id; ids are generated")
    if name == "describe" and not description:
        raise MemoryStoreError("invalid_op", "describe needs a non-empty description")
    check_writable(store, segments)
    return inside_root(root, target_file(store, segments))


def _apply_fact_op(data: dict | None, op: dict, now: str) -> tuple[dict, str | None]:
    """Apply add/update/retire/describe to the memory `data` (None when missing); returns the memory and new id."""
    name, path, description = op["op"], op["path"], op.get("description")
    if data is None:
        if name != "add":
            raise MemoryStoreError("not_found", f"no memory {path}")
        if not description:
            raise MemoryStoreError("invalid_op", f"add creates {path}; description required")
        data = {"description": description, "facts": []}
    facts = data["facts"]
    result_id = None
    if name == "add":
        result_id = _new_id(facts)
        facts.append(dict.fromkeys(FACT_KEYS) | {"id": result_id, "text": op["text"], "created": now, "updated": now})
    elif name != "describe":
        fact = next((f for f in facts if f["id"] == op["id"]), None)
        if fact is None:
            raise MemoryStoreError("not_found", f"no fact {op['id']} in {path}")
        if fact.get("retired"):
            raise MemoryStoreError("retired", f"fact {op['id']} in {path} is retired")
        result_id = fact["id"]
        if name == "update":
            result_id = _new_id(facts)
            created = fact.get("created") or now
            new_fact = {"id": result_id, "text": op["text"], "created": created, "updated": now}
            facts.append(dict.fromkeys(FACT_KEYS) | new_fact)
            fact["superseded_by"] = result_id
        fact["retired"] = now
    if description:
        data["description"] = description
    return data, result_id


def _failure(op: object, exc: MemoryStoreError) -> dict:
    op_name, path = (op.get("op"), op.get("path")) if isinstance(op, dict) else (None, None)
    return {"ok": False, "op": op_name, "path": path, "error": exc.code, "message": exc.message}


def _apply_to_file(file: Path, file_ops: list[dict], results: list[dict], indexes: list[int]) -> str | None:
    """Apply the ops of one file (called under its lock); fills `results`, returns the new content or None."""
    if file.suffix == ".md":
        for index, op in zip(indexes, file_ops, strict=True):
            results[index] = {"ok": True, "op": "write_doc", "path": op["path"]}
        return file_ops[-1]["text"]
    try:
        data = _load_memory(file)
    except MemoryStoreError as exc:
        for index, op in zip(indexes, file_ops, strict=True):
            results[index] = _failure(op, exc)
        return None
    now = _iso()
    changed = False
    for index, op in zip(indexes, file_ops, strict=True):
        try:
            data, result_id = _apply_fact_op(data, op, now)
        except MemoryStoreError as exc:
            results[index] = _failure(op, exc)
            continue
        results[index] = {"ok": True, "op": op["op"], "path": op["path"]}
        if result_id is not None:
            results[index]["id"] = result_id
        changed = True
    return _dump_memory(data) if changed and data is not None else None


def apply_memory_ops(memory_store: str, ops: Sequence[dict], *, root: str | Path | None = None) -> list[dict]:
    root_dir = resolve_root(root)
    store_dir(root_dir, memory_store)  # memory_store_missing before the root lock creates .locks
    with _locked(root_dir, shared=True):
        return _apply_locked(root_dir, store_dir(root_dir, memory_store), ops)


def _apply_locked(root_dir: Path, store: Path, ops: Sequence[dict]) -> list[dict]:
    results: list[dict] = [{}] * len(ops)  # every index is replaced below
    by_file: dict[Path, list[int]] = {}
    for index, op in enumerate(ops):
        try:
            by_file.setdefault(_op_target(root_dir, store, op), []).append(index)
        except MemoryStoreError as exc:
            results[index] = _failure(op, exc)
    # Each touched file is one transaction: lock, read, apply its ops in order, write once.
    for file, indexes in by_file.items():
        try:
            with _locked(root_dir, file):
                text = _apply_to_file(file, [ops[i] for i in indexes], results, indexes)
                if text is not None:
                    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    _write_atomic(file, text)
        except OSError as exc:
            error = MemoryStoreError("invalid_path", f"{file.relative_to(store)}: {exc.strerror or exc}")
            for index in indexes:
                results[index] = _failure(ops[index], error)
    return results


def move_memory_store(old: str, new: str, *, root: str | Path | None = None) -> None:
    root_dir = resolve_root(root)
    store_dir(root_dir, old)  # memory_store_missing before the root lock creates .locks
    with _locked(root_dir):
        _move_locked(root_dir, old, new)


def _move_locked(root_dir: Path, old: str, new: str) -> None:
    source = store_dir(root_dir, old)
    old_segments, new_segments = split_path(old), split_path(new)
    if new_segments[: len(old_segments)] == old_segments:
        raise MemoryStoreError("invalid_path", f"cannot move {old} into itself ({new})")
    target = inside_root(root_dir, root_dir.joinpath(*new_segments))
    if target.exists():
        raise MemoryStoreError("invalid_op", f"{new} already exists")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (FileExistsError, NotADirectoryError):
        raise MemoryStoreError("invalid_path", f"{new} collides with an existing file") from None
    os.rename(source, target)
