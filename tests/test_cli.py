import io
import json
from pathlib import Path

import pytest

from memory_store.cli import main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_create_ls_apply_read(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    root = str(tmp_path / "other")
    assert run(capsys, "--root", root, "create", "demo", "map", "run") == (
        0,
        "memory_store demo · directories: map, run\n",
        "",
    )
    ops = json.dumps(
        [
            {"op": "add", "path": "map/auth", "text": "Auth: server sessions", "description": "Auth"},
            {"op": "write_doc", "path": "run/contract.md", "text": "# Contract\n"},
        ]
    )
    code, out, _ = run(capsys, "--root", root, "apply", "demo", "--ops", ops)
    assert code == 0
    first, second = out.splitlines()
    assert first.startswith("ok add map/auth ")
    assert second == "ok write_doc run/contract.md"
    fact_id = first.split()[-1]
    assert run(capsys, "--root", root, "ls")[1] == "memory_stores: demo\n"
    assert "- map/auth — Auth · 1 facts" in run(capsys, "--root", root, "ls", "demo", "map")[1]
    _, out, _ = run(capsys, "--root", root, "read", "demo", "map/auth", "run/contract.md")
    assert out == f"## map/auth — Auth\n- [{fact_id}] Auth: server sessions\n\n## run/contract.md\n# Contract\n"
    assert run(capsys, "--root", root, "read", "demo", "map/auth", "--no-ids")[1].endswith("- Auth: server sessions\n")


def test_root_defaults_to_env(capsys: pytest.CaptureFixture[str], root: Path):
    assert run(capsys, "create", "user")[0] == 0
    assert (root / "user" / ".memory_store").is_file()


def test_apply_reads_stdin_and_fails_on_failed_op(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    run(capsys, "create", "demo")
    ops = [{"op": "add", "path": "notes", "text": "x", "description": "Notes"}, {"op": "retire", "path": "notes"}]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ops)))
    code, out, err = run(capsys, "apply", "demo")
    assert code == 1
    assert out.splitlines()[0].startswith("ok add notes ")
    assert out.splitlines()[1].startswith("failed retire notes: ")
    assert err == "error: 1 of 2 ops failed\n"


def test_errors_exit_1_on_stderr(capsys: pytest.CaptureFixture[str]):
    assert run(capsys, "ls", "missing") == (1, "", "error: memory_store_missing: no memory_store at missing\n")
    run(capsys, "create", "demo")
    code, _, err = run(capsys, "apply", "demo", "--ops", "not json")
    assert code == 1 and err.startswith("error: invalid_json: ")
    assert run(capsys, "apply", "demo", "--ops", "{}")[0] == 1
