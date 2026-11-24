import asyncio
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult, TextContent

from memory_store.server import mcp


def text_of(result: CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def call(name: str, **arguments: object) -> str:
    async def run() -> str:
        async with Client(mcp) as client:
            result = await client.call_tool(name, arguments)
        assert not result.is_error, result
        return text_of(result)

    return asyncio.run(run())


def test_tools_and_instructions_are_exposed():
    async def run() -> tuple[list[str], str | None]:
        async with Client(mcp) as client:
            tools = (await client.list_tools()).tools
            return [t.name for t in tools], client.instructions

    names, instructions = asyncio.run(run())
    assert names == ["create_memory_store", "list_memories", "read_memories", "apply_memory_ops"]
    assert instructions and "read_memories" in instructions


def test_create_add_update_list_read(root: Path):
    assert call("create_memory_store", memory_store="demo", directories=["map"]) == (
        "memory_store demo · directories: map"
    )
    added = call(
        "apply_memory_ops",
        memory_store="demo",
        ops=[{"op": "add", "path": "map/auth", "text": "Auth: JWT", "description": "Auth decisions"}],
    )
    assert added.startswith("ok add map/auth ")
    fact_id = added.split()[-1]
    updated = call(
        "apply_memory_ops",
        memory_store="demo",
        ops=[{"op": "update", "path": "map/auth", "id": fact_id, "text": "Auth: server sessions"}],
    )
    new_id = updated.split()[-1]
    assert call("list_memories") == "memory_stores: demo\n"
    assert "map/auth — Auth decisions · 1 facts" in call("list_memories", memory_store="demo", path="map")
    text = call("read_memories", memory_store="demo", paths=["map/auth"])
    assert text == f"## map/auth — Auth decisions\n- [{new_id}] Auth: server sessions\n"
    assert call("read_memories", memory_store="demo", paths=["map/auth"], ids=False).endswith(
        "- Auth: server sessions\n"
    )
    assert (root / "demo" / "map" / "auth.json").is_file()


def test_failed_op_and_store_errors_are_text():
    call("create_memory_store", memory_store="demo")
    result = call("apply_memory_ops", memory_store="demo", ops=[{"op": "retire", "path": "x", "id": "abc"}])
    assert result.startswith("failed retire x: not_found: ")
    assert call("list_memories", memory_store="missing") == "error: memory_store_missing: no memory_store at missing"
    assert call("create_memory_store", memory_store="../escape").startswith("error: invalid_path: ")


def test_stdio_round_trip(root: Path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from memory_store.server import main; main()"],
        env={"MEMORY_STORE_ROOT": str(root)},
    )

    async def run() -> str:
        async with Client(params, read_timeout_seconds=10) as client:
            await client.call_tool("create_memory_store", {"memory_store": "demo"})
            result = await client.call_tool("list_memories", {})
        return text_of(result)

    assert asyncio.run(asyncio.wait_for(run(), 20)) == "memory_stores: demo\n"
