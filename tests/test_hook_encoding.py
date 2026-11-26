"""The hook must read UTF-8 (with or without a BOM) and never crash writing a reason, whatever the console code page."""
import io
import json
import os
import subprocess
import sys

from dinoguard.hook import client


def _payload(cmd):
    return {"hook_event_name": "PreToolUse", "session_id": "enc", "cwd": os.getcwd(),
            "tool_name": "Bash", "tool_input": {"command": cmd}}


def test_bom_and_non_ascii_under_cp1252_console(dg_home):
    data = b"\xef\xbb\xbf" + json.dumps(_payload("rm -rf src  # café → 日本"),
                                        ensure_ascii=False).encode("utf-8")
    env = dict(os.environ, PYTHONIOENCODING="cp1252", DINOGUARD_HOME=str(dg_home))
    r = subprocess.run([sys.executable, "-m", "dinoguard.hook", "claude"], input=data, capture_output=True,
                       env=env, timeout=60)
    assert r.returncode in (0, 2), r.stderr
    out = json.loads(r.stdout.decode("utf-8"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class _Cp1252:
    """A console stream that can only encode cp1252, like a default Windows terminal."""
    def __init__(self):
        self.raw = io.BytesIO()
        self.buffer = self.raw

    def write(self, s):
        self.raw.write(s.encode("cp1252"))   # raises on characters like the arrow

    def flush(self):
        pass


def test_unencodable_reason_does_not_crash(monkeypatch):
    out, err = _Cp1252(), _Cp1252()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}"), encoding="utf-8"))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(client, "run", lambda h, t, e=None: (2, "", "blocked → use git stash 日"))
    assert client.main(["claude"]) == 2
    assert err.raw.getvalue().decode("utf-8") == "blocked → use git stash 日"
