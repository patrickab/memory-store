import asyncio

import pytest

pytest.importorskip("textual")

from memory_store import store
from memory_store.tui import MemoryBrowser, load_roots


def seed(root):
    store.create_memory_store("user", root=root)
    store.create_memory_store("project/run", ["map"], root=root)
    store.apply_memory_ops(
        "user", [{"op": "add", "path": "profile", "text": "likes tea", "description": "who"}], root=root
    )
    ops = [{"op": "add", "path": "map/auth", "text": "sessions, not JWT", "description": "auth"}]
    store.apply_memory_ops("project/run", ops, root=root)


def test_load_roots_expands_globs_and_skips_missing(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert load_roots([str(tmp_path / "*"), str(tmp_path / "missing")]) == [tmp_path / "a", tmp_path / "b"]


def test_browse_filter_and_edit(tmp_path):
    seed(tmp_path)

    async def run():
        app = MemoryBrowser([tmp_path])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("down", "down", "down")  # root, project, run, user
            await pilot.pause()
            assert [entry.path for entry in app.entries] == ["profile"]
            await pilot.press("l", "l", "a")
            await pilot.pause()
            await pilot.press(*"new", "enter")
            await pilot.pause()
            assert [fact["text"] for fact in app.facts] == ["new", "likes tea"]
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert [fact["text"] for fact in app.facts] == ["likes tea"]
            await pilot.press("slash", *"jwt")
            await pilot.pause()
            assert [entry.path for entry in app.entries] == ["map/auth"]

    asyncio.run(run())
