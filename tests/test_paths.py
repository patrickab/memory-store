from pathlib import Path

import pytest

from memory_store.paths import (
    MemoryStoreError,
    check_writable,
    default_root,
    inside_root,
    split_path,
    store_dir,
    target_file,
)
from memory_store.store import create_memory_store


@pytest.mark.parametrize(
    ("path", "segments"),
    [
        ("auth", ["auth"]),
        ("map/auth", ["map", "auth"]),
        ("gigachad-bot/memory-store-v1", ["gigachad-bot", "memory-store-v1"]),
        ("run/contract.md", ["run", "contract.md"]),
        ("a/b/c_d.e-f", ["a", "b", "c_d.e-f"]),
    ],
)
def test_split_path_multi_level(path: str, segments: list[str]) -> None:
    assert split_path(path) == segments


@pytest.mark.parametrize(
    "path",
    ["", "/abs", "/etc/passwd", "..", "a/../b", "a/..", ".", "a/./b", ".locks", "a/.memory_store", "a//b", "a/", "a b"],
)
def test_split_path_rejects_escapes_and_bad_segments(path: str) -> None:
    with pytest.raises(MemoryStoreError) as info:
        split_path(path)
    assert info.value.code == "invalid_path"


def test_split_path_rejects_non_string() -> None:
    with pytest.raises(MemoryStoreError) as info:
        split_path(None)
    assert info.value.code == "invalid_path"


def test_default_root_from_env(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert default_root() == root
    monkeypatch.delenv("MEMORY_STORE_ROOT")
    assert default_root() == Path.home() / ".pi" / "memory_store"


def test_target_file_memory_and_document(root: Path) -> None:
    assert target_file(root, ["map", "auth"]) == root / "map" / "auth.json"
    assert target_file(root, ["run", "contract.md"]) == root / "run" / "contract.md"


def test_store_dir_requires_marker(root: Path) -> None:
    (root / "plain").mkdir(parents=True)
    with pytest.raises(MemoryStoreError) as info:
        store_dir(root, "plain")
    assert info.value.code == "memory_store_missing"
    create_memory_store("plain", root=root)
    assert store_dir(root, "plain") == root / "plain"


def test_check_writable(root: Path) -> None:
    create_memory_store("p", ["map"], root=root)
    create_memory_store("p/map/nested", root=root)
    store = root / "p"
    check_writable(store, ["top"])
    check_writable(store, ["map", "auth"])
    check_writable(store, ["map", "sub", "auth"])
    for segments in (["mpa", "auth"], ["map", "nested", "x"]):
        with pytest.raises(MemoryStoreError) as info:
            check_writable(store, segments)
        assert info.value.code == "undeclared_directory"


def test_inside_root_refuses_symlinks_out(root: Path, tmp_path: Path) -> None:
    root.mkdir()
    (root / "in").symlink_to(root)
    (root / "out").symlink_to(tmp_path)
    assert inside_root(root, root / "in" / "x.json") == root / "in" / "x.json"
    with pytest.raises(MemoryStoreError) as info:
        inside_root(root, root / "out" / "x.json")
    assert info.value.code == "invalid_path"
