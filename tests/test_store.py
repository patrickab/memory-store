import json
import multiprocessing
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from memory_store import store
from memory_store.store import (
    MemoryStoreError,
    apply_memory_ops,
    create_memory_store,
    list_memories,
    move_memory_store,
    read_memories,
    read_memory,
    render_list,
)


def add(memory_store: str, path: str, text: str, description: str | None = "desc", root: Path | None = None) -> dict:
    op = {"op": "add", "path": path, "text": text}
    if description is not None:
        op["description"] = description
    return apply_memory_ops(memory_store, [op], root=root)[0]


def test_create_is_idempotent_with_parents_and_adds_directories(root: Path) -> None:
    assert create_memory_store("proj/run1", ["map", "handoffs"], root=root) == {
        "memory_store": "proj/run1",
        "directories": ["handoffs", "map"],
    }
    assert create_memory_store("proj/run1", ["run"], root=root)["directories"] == ["handoffs", "map", "run"]
    assert create_memory_store("proj/run1", root=root)["directories"] == ["handoffs", "map", "run"]
    marker = json.loads((root / "proj/run1/.memory_store").read_text())
    assert marker == {"directories": ["handoffs", "map", "run"]}
    assert all((root / "proj/run1" / d).is_dir() for d in ("map", "handoffs", "run"))
    assert not (root / "proj/.memory_store").exists()


def test_create_rejects_bad_paths(root: Path) -> None:
    for memory_store, directories in (("../x", []), ("/abs", []), ("ok", ["a/b"]), ("ok", [".locks"])):
        with pytest.raises(MemoryStoreError) as info:
            create_memory_store(memory_store, directories, root=root)
        assert info.value.code == "invalid_path"


def test_root_is_0700_and_files_0600(root: Path) -> None:
    create_memory_store("user", root=root)
    add("user", "profile", "likes tea")
    apply_memory_ops("user", [{"op": "write_doc", "path": "notes.md", "text": "hi"}])
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for name in ("profile.json", "notes.md", ".memory_store"):
        assert stat.S_IMODE((root / "user" / name).stat().st_mode) == 0o600


def test_default_root_comes_from_env(root: Path) -> None:
    create_memory_store("user")
    assert (root / "user/.memory_store").is_file()


def test_write_to_missing_memory_store_fails(root: Path) -> None:
    with pytest.raises(MemoryStoreError) as info:
        apply_memory_ops("nope", [{"op": "add", "path": "x", "text": "t", "description": "d"}], root=root)
    assert info.value.code == "memory_store_missing"
    assert not (root / "nope").exists()


def test_write_to_undeclared_directory_or_nested_store_fails(root: Path) -> None:
    create_memory_store("p", ["map"], root=root)
    create_memory_store("p/child", root=root)
    results = apply_memory_ops(
        "p",
        [
            {"op": "add", "path": "mpa/auth", "text": "t", "description": "d"},
            {"op": "add", "path": "child/x", "text": "t", "description": "d"},
            {"op": "add", "path": "map/auth", "text": "t", "description": "d"},
            {"op": "add", "path": "map/deep/auth", "text": "t", "description": "d"},
        ],
        root=root,
    )
    assert [r.get("error") for r in results] == ["undeclared_directory", "undeclared_directory", None, None]
    assert not (root / "p/mpa").exists()
    assert not (root / "p/child/x.json").exists()
    assert (root / "p/map/deep/auth.json").is_file()


def test_add_requires_description_for_new_memory_and_updates_it(root: Path) -> None:
    create_memory_store("p", root=root)
    assert add("p", "m", "fact", description=None)["error"] == "invalid_op"
    assert not (root / "p/m.json").exists()
    assert add("p", "m", "fact", description="first")["ok"]
    assert add("p", "m", "fact2", description=None)["ok"]
    assert read_memory("p", "m")["description"] == "first"
    add("p", "m", "fact3", description="second")
    assert read_memory("p", "m")["description"] == "second"


def test_memory_file_format(root: Path) -> None:
    create_memory_store("p", root=root)
    fact_id = add("p", "m", "Auth: sessions", description="Auth")["id"]
    raw = (root / "p/m.json").read_text()
    assert raw.endswith("}\n")
    data = json.loads(raw)
    assert list(data) == ["description", "facts"]
    fact = data["facts"][0]
    assert list(fact) == ["id", "text", "created", "updated", "retired", "superseded_by"]
    assert fact["id"] == fact_id and len(fact_id) == 3 and fact_id.isalnum() and fact_id == fact_id.lower()
    assert fact["created"].endswith("Z") and len(fact["created"]) == 20
    assert raw == json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def test_update_supersedes_and_hides_old_fact(root: Path) -> None:
    create_memory_store("p", root=root)
    old = add("p", "m", "JWT")["id"]
    result = apply_memory_ops("p", [{"op": "update", "path": "m", "id": old, "text": "sessions"}])[0]
    assert result["ok"] and result["id"] != old
    facts = json.loads((root / "p/m.json").read_text())["facts"]
    assert facts[0]["retired"] and facts[0]["superseded_by"] == result["id"]
    assert facts[1]["id"] == result["id"] and facts[1]["retired"] is None
    assert [f["text"] for f in read_memory("p", "m")["facts"]] == ["sessions"]
    rendered = read_memories("p", ["m"])
    assert "JWT" not in rendered and f"- [{result['id']}] sessions" in rendered
    again = apply_memory_ops("p", [{"op": "update", "path": "m", "id": old, "text": "x"}])[0]
    assert again["error"] == "retired"


def test_retire_hides_fact_everywhere(root: Path) -> None:
    create_memory_store("p", root=root)
    keep = add("p", "m", "keep")["id"]
    gone = add("p", "m", "gone")["id"]
    assert apply_memory_ops("p", [{"op": "retire", "path": "m", "id": gone}])[0] == {
        "ok": True,
        "op": "retire",
        "path": "m",
        "id": gone,
    }
    assert "gone" not in read_memories("p", ["m"])
    assert [f["id"] for f in read_memory("p", "m")["facts"]] == [keep]
    assert list_memories("p")["memories"][0]["facts"] == 1
    results = apply_memory_ops(
        "p",
        [
            {"op": "retire", "path": "m", "id": gone},
            {"op": "retire", "path": "m", "id": "zzz"},
            {"op": "retire", "path": "missing", "id": keep},
        ],
    )
    assert [r["error"] for r in results] == ["retired", "not_found", "not_found"]
    assert len(json.loads((root / "p/m.json").read_text())["facts"]) == 2


def test_read_memories_render_ids_and_not_found(root: Path) -> None:
    create_memory_store("p", ["map"], root=root)
    first = add("p", "map/auth", "first", description="Auth decisions")["id"]
    second = add("p", "map/auth", "second", description=None)["id"]
    assert read_memories("p", ["map/auth"]) == f"## map/auth — Auth decisions\n- [{second}] second\n- [{first}] first\n"
    assert read_memories("p", ["map/auth"], ids=False) == "## map/auth — Auth decisions\n- second\n- first\n"
    rendered = read_memories("p", ["map/nope", "../x"])
    assert "## map/nope — not_found" in rendered and "## ../x — invalid_path" in rendered
    with pytest.raises(MemoryStoreError) as info:
        read_memory("p", "map/nope")
    assert info.value.code == "not_found"


def test_write_doc_creates_and_replaces(root: Path) -> None:
    create_memory_store("p", ["run"], root=root)
    results = apply_memory_ops(
        "p",
        [
            {"op": "write_doc", "path": "run/contract.md", "text": "# v1\n"},
            {"op": "write_doc", "path": "run/contract", "text": "x"},
            {"op": "add", "path": "run/c.md", "text": "x", "description": "d"},
        ],
    )
    assert results[0] == {"ok": True, "op": "write_doc", "path": "run/contract.md"}
    assert [r.get("error") for r in results[1:]] == ["invalid_path", "invalid_path"]
    assert read_memories("p", ["run/contract.md"]) == "## run/contract.md\n# v1\n"
    apply_memory_ops("p", [{"op": "write_doc", "path": "run/contract.md", "text": "# v2\n"}])
    assert (root / "p/run/contract.md").read_text() == "# v2\n"
    assert "not_found" in read_memories("p", ["run/missing.md"])


def test_batch_with_failing_op_applies_the_rest(root: Path) -> None:
    create_memory_store("p", ["map"], root=root)
    ops: list = [
        {"op": "add", "path": "a", "text": "one", "description": "A"},
        {"op": "bogus", "path": "a"},
        {"op": "update", "path": "a", "id": "zzz", "text": "x"},
        "not an op",
        {"op": "add", "path": "a", "text": "two"},
        {"op": "add", "path": "nope/b", "text": "x", "description": "B"},
        {"op": "write_doc", "path": "map/doc.md", "text": "d"},
    ]
    results = apply_memory_ops("p", ops)
    assert [r["ok"] for r in results] == [True, False, False, False, True, False, True]
    assert [r.get("error") for r in results] == [
        None,
        "invalid_op",
        "not_found",
        "invalid_op",
        None,
        "undeclared_directory",
        None,
    ]
    assert results[3]["op"] is None and "message" in results[3]
    assert [f["text"] for f in read_memory("p", "a")["facts"]] == ["two", "one"]


def test_list_memories(root: Path) -> None:
    assert list_memories(root=root) == {"memory_stores": [], "directories": [], "memories": [], "documents": []}
    create_memory_store("user", root=root)
    create_memory_store("proj", ["map", "run"], root=root)
    create_memory_store("proj/run1", root=root)
    create_memory_store("group/inner", root=root)
    add("proj", "decisions", "d1", description="Decisions")
    add("proj", "map/auth", "a1", description="Auth")
    apply_memory_ops("proj", [{"op": "write_doc", "path": "run/c.md", "text": "abc"}])
    assert list_memories()["memory_stores"] == ["group/inner", "proj", "user"]
    top = list_memories("proj")
    assert top["memory_stores"] == ["proj/run1"]
    assert top["directories"] == ["map", "run"]
    assert [(m["path"], m["description"], m["facts"]) for m in top["memories"]] == [("decisions", "Decisions", 1)]
    assert top["memories"][0]["updated"].endswith("Z")
    assert top["documents"] == []
    assert [m["path"] for m in list_memories("proj", "map")["memories"]] == ["map/auth"]
    docs = list_memories("proj", "run")["documents"]
    assert [(d["path"], d["bytes"]) for d in docs] == [("run/c.md", 3)]
    assert not any(".locks" in s for s in list_memories()["memory_stores"])
    with pytest.raises(MemoryStoreError) as info:
        list_memories("proj", "nope")
    assert info.value.code == "undeclared_directory"
    with pytest.raises(MemoryStoreError) as info:
        list_memories("missing")
    assert info.value.code == "memory_store_missing"
    text = render_list(top)
    assert "memory_stores: proj/run1" in text and "- decisions — Decisions · 1 facts" in text
    assert render_list(list_memories("user")) == "(empty)\n"


def test_move_memory_store(root: Path) -> None:
    create_memory_store("old-name", ["map"], root=root)
    add("old-name", "map/x", "fact")
    move_memory_store("old-name", "group/new-name")
    assert not (root / "old-name").exists()
    assert [f["text"] for f in read_memory("group/new-name", "map/x")["facts"]] == ["fact"]
    create_memory_store("other", root=root)
    for old, new, code in (
        ("missing", "x", "memory_store_missing"),
        ("other", "group/new-name", "invalid_op"),
        ("other", "other/inner", "invalid_path"),
    ):
        with pytest.raises(MemoryStoreError) as info:
            move_memory_store(old, new)
        assert info.value.code == code


def test_duplicate_ids_and_invalid_json_are_corrupt(root: Path) -> None:
    create_memory_store("p", root=root)
    fact = {"id": "abc", "text": "t", "created": None, "updated": None, "retired": None, "superseded_by": None}
    (root / "p/dup.json").write_text(json.dumps({"description": "d", "facts": [fact, fact]}))
    (root / "p/bad.json").write_text("{not json")
    for path in ("dup", "bad"):
        with pytest.raises(MemoryStoreError) as info:
            read_memory("p", path)
        assert info.value.code == "corrupt"
        assert apply_memory_ops("p", [{"op": "add", "path": path, "text": "x"}])[0]["error"] == "corrupt"
        assert f"## {path} — corrupt" in read_memories("p", [path])
    assert {m["path"]: m.get("error") for m in list_memories("p")["memories"]} == {"bad": "corrupt", "dup": "corrupt"}


def test_ids_never_reused_after_retire(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_memory_store("p", root=root)
    first = add("p", "m", "one")["id"]
    apply_memory_ops("p", [{"op": "retire", "path": "m", "id": first}])
    draws = iter([first, first, "new"])
    monkeypatch.setattr(store, "_random_id", lambda length: next(draws))
    assert add("p", "m", "two")["id"] == "new"


def test_ids_grow_to_four_chars_past_2000_facts(root: Path) -> None:
    create_memory_store("p", root=root)
    facts = [
        {"id": f"{i:03x}", "text": "t", "created": None, "updated": None, "retired": "x", "superseded_by": None}
        for i in range(2001)
    ]
    (root / "p/big.json").write_text(json.dumps({"description": "d", "facts": facts}))
    assert len(add("p", "big", "new")["id"]) == 4


def test_atomic_write_keeps_old_file_on_failure(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_memory_store("p", root=root)
    add("p", "m", "one")
    before = (root / "p/m.json").read_text()

    def fail_replace(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert add("p", "m", "two")["error"] == "invalid_path"
    assert (root / "p/m.json").read_text() == before
    assert sorted(p.name for p in (root / "p").iterdir()) == [".memory_store", "m.json"]


def _add_many(root: str, worker: int, count: int) -> None:
    for i in range(count):
        result = apply_memory_ops(
            "p", [{"op": "add", "path": "shared", "text": f"w{worker}-{i}", "description": "d"}], root=root
        )[0]
        assert result["ok"], result


def test_concurrent_adds_from_processes_keep_unique_ids_and_all_facts(root: Path) -> None:
    create_memory_store("p", root=root)
    workers, count = 6, 25
    context = multiprocessing.get_context("fork")
    processes = [context.Process(target=_add_many, args=(str(root), w, count)) for w in range(workers)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    facts = json.loads((root / "p/shared.json").read_text())["facts"]
    assert len(facts) == workers * count
    assert len({f["id"] for f in facts}) == workers * count
    assert {f["text"] for f in facts} == {f"w{w}-{i}" for w in range(workers) for i in range(count)}
    assert not [p for p in (root / "p").iterdir() if p.name.endswith(".tmp")]


def test_move_waits_for_a_running_writer(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_memory_store("old", root=root)
    load = store._load_memory
    loading = threading.Event()

    def slow_load(file: Path) -> dict | None:
        loading.set()
        time.sleep(0.2)
        return load(file)

    monkeypatch.setattr(store, "_load_memory", slow_load)
    results: list[dict] = []
    writer = threading.Thread(target=lambda: results.append(add("old", "m", "fact")))
    writer.start()
    assert loading.wait(5)
    move_memory_store("old", "new")
    writer.join(5)
    assert results[0]["ok"]
    assert not (root / "old").exists()
    assert [f["text"] for f in read_memory("new", "m")["facts"]] == ["fact"]
    with pytest.raises(MemoryStoreError) as info:
        add("old", "m", "late")
    assert info.value.code == "memory_store_missing"


def test_os_errors_fail_only_that_files_ops(root: Path) -> None:
    create_memory_store("p", ["map", "d.json"], root=root)
    apply_memory_ops("p", [{"op": "write_doc", "path": "map/a.md", "text": "doc"}])
    results = apply_memory_ops(
        "p",
        [
            {"op": "add", "path": "m", "text": "x", "description": "d"},
            {"op": "add", "path": "map/a.md/x", "text": "y", "description": "d"},
        ],
    )
    assert results[0]["ok"] and results[1]["error"] == "invalid_path"
    assert add("p", "d", "z")["error"] == "invalid_path"
    assert "## map/a.md/x — not_found" in read_memories("p", ["map/a.md/x"])
    assert "## map/a.md/y.md — not_found" in read_memories("p", ["map/a.md/y.md"])


def test_non_utf8_and_bad_timestamps_are_corrupt_entries(root: Path) -> None:
    create_memory_store("p", root=root)
    add("p", "good", "fact")
    (root / "p/bytes.json").write_bytes(b"\xff")
    fact = {"id": "abc", "text": "t", "created": None, "updated": 5, "retired": None, "superseded_by": None}
    (root / "p/stamp.json").write_text(json.dumps({"description": "d", "facts": [fact]}))
    entries = {m["path"]: m.get("error") for m in list_memories("p")["memories"]}
    assert entries == {"bytes": "corrupt", "good": None, "stamp": "corrupt"}
    for path in ("bytes", "stamp"):
        with pytest.raises(MemoryStoreError) as info:
            read_memory("p", path)
        assert info.value.code == "corrupt"


def test_first_creates_race_on_a_fresh_root(root: Path) -> None:
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def create(i: int) -> None:
        barrier.wait()
        try:
            create_memory_store(f"s{i}", root=root)
        except (OSError, MemoryStoreError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert list_memories()["memory_stores"] == [f"s{i}" for i in range(8)]


def test_symlinks_out_of_the_root_are_invalid_path(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    create_memory_store("p", ["map"], root=root)
    (root / "p/map").rmdir()
    (root / "p/map").symlink_to(outside)
    assert add("p", "map/x", "fact")["error"] == "invalid_path"
    assert apply_memory_ops("p", [{"op": "write_doc", "path": "map/a.md", "text": "t"}])[0]["error"] == "invalid_path"
    assert "## map/a.md — invalid_path" in read_memories("p", ["map/a.md"])
    assert list(outside.iterdir()) == []
    create_memory_store("q", root=outside)
    (root / "q").symlink_to(outside / "q")
    for call in (lambda: read_memory("q", "m"), lambda: list_memories("p", "map")):
        with pytest.raises(MemoryStoreError) as info:
            call()
        assert info.value.code == "invalid_path"


def test_symlinked_root_is_allowed(root: Path, tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    root.symlink_to(real)
    create_memory_store("p", root=root)
    assert add("p", "m", "fact")["ok"]
    assert (real / "p/m.json").is_file()


def test_ids_grow_when_the_alphabet_runs_short(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lengths = []
    monkeypatch.setattr(store, "_random_id", lambda length: lengths.append(length) or "z" * length)
    facts = [{"id": f"{i:04x}"} for i in range(36**4 // 2 + 1)]
    assert store._new_id(facts) == "zzzzz"
    assert lengths == [5]


def test_describe_sets_description_only(root: Path) -> None:
    create_memory_store("p", root=root)
    add("p", "m", "fact", description="old")
    before = json.loads((root / "p/m.json").read_text())["facts"]
    assert apply_memory_ops("p", [{"op": "describe", "path": "m", "description": "new"}]) == [
        {"ok": True, "op": "describe", "path": "m"}
    ]
    after = json.loads((root / "p/m.json").read_text())
    assert after["description"] == "new"
    assert after["facts"] == before
    results = apply_memory_ops(
        "p",
        [
            {"op": "describe", "path": "missing", "description": "x"},
            {"op": "describe", "path": "m", "description": ""},
            {"op": "describe", "path": "m"},
            {"op": "describe", "path": "a.md", "description": "x"},
        ],
    )
    assert [r["error"] for r in results] == ["not_found", "invalid_op", "invalid_op", "invalid_path"]
