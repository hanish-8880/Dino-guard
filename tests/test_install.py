import json
import os

from dinoguard.adapters.install import install_claude, uninstall_claude, wiring


def test_install_uninstall_byte_identical(tmp_path):
    p = tmp_path / "claudehome" / "settings.json"
    p.parent.mkdir(parents=True)
    original = b'{\n    "model": "opus",\n  "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-hook"}]}]}\n}'
    p.write_bytes(original)
    install_claude("user")
    data = json.loads(p.read_text())
    assert any("dinoguard" in h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"])
    assert any(h["command"] == "my-hook" for g in data["hooks"]["PreToolUse"] for h in g["hooks"])
    assert data["model"] == "opus"
    assert all(wiring("user")["events"][e] for e in ("PreToolUse", "Stop", "SessionStart"))
    uninstall_claude("user")
    assert p.read_bytes() == original


def test_install_twice_then_uninstall(tmp_path):
    p = tmp_path / "claudehome" / "settings.json"
    p.parent.mkdir(parents=True)
    original = b'{"theme": "dark"}'
    p.write_bytes(original)
    install_claude("user")
    install_claude("user")
    data = json.loads(p.read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1   # idempotent
    uninstall_claude("user")
    assert p.read_bytes() == original


def test_install_when_no_settings(tmp_path):
    p = tmp_path / "claudehome" / "settings.json"
    install_claude("user")
    assert p.exists()
    uninstall_claude("user")
    assert not p.exists()


def test_uninstall_after_user_edit_keeps_their_change(tmp_path):
    p = tmp_path / "claudehome" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"a": 1}')
    install_claude("user")
    data = json.loads(p.read_text())
    data["b"] = 2
    p.write_text(json.dumps(data))
    uninstall_claude("user")
    after = json.loads(p.read_text())
    assert after == {"a": 1, "b": 2}


def test_backup_written(tmp_path, dg_home):
    p = tmp_path / "claudehome" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"x": 1}')
    install_claude("user")
    assert any(f.read_text() == '{"x": 1}' for f in (dg_home / "backups").iterdir())


def test_project_scope(project):
    install_claude("project", str(project))
    assert (project / ".claude" / "settings.json").exists()
    uninstall_claude("project", str(project))
    assert not (project / ".claude" / "settings.json").exists()
    assert os.path.isdir(project / ".claude")
