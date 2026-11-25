import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def dg_home(tmp_path, monkeypatch):
    """Every test gets its own DinoGuard home, Claude settings and projects dir."""
    home = tmp_path / "dghome"
    home.mkdir()
    monkeypatch.setenv("DINOGUARD_HOME", str(home))
    monkeypatch.setenv("DG_CLAUDE_HOME", str(tmp_path / "claudehome"))
    monkeypatch.setenv("DG_CLAUDE_SETTINGS", str(tmp_path / "claudehome" / "settings.json"))
    monkeypatch.setenv("DG_CLAUDE_PROJECTS", str(tmp_path / "claudehome" / "projects"))
    monkeypatch.setenv("DG_NO_FIRST_RUN", "1")
    for k in ("DG_REVIEWER_URL", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    return home


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture
def conn(dg_home):
    from dinoguard.store import db
    c = db.connect()
    yield c
    c.close()


@pytest.fixture(scope="session")
def builtins():
    from dinoguard.engine.registry import Registry
    return Registry().load_builtins()


os.environ.setdefault("DG_NO_FIRST_RUN", "1")
