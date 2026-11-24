from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh, not yet existing root; also the env default so nothing reaches ~/.pi."""
    path = tmp_path / "root"
    monkeypatch.setenv("MEMORY_STORE_ROOT", str(path))
    return path
