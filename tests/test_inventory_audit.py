"""Phase 12 (CLAUDE.md §17.4-6): audit v2 (selection, contract, runs, coverage, recurrence, suggest), evals v2,
packs v2."""
import json

import pytest

from dinoguard import config
from dinoguard.audit import contract as contract_mod
from dinoguard.audit.replay import run_audit
from dinoguard.audit.runs import history
from dinoguard.packs.manifest import PackError

CONTRACT = """should:
  - run the tests before finishing
should_not:
  - text: touch the payments folder
    paths: ["payments/"]
  - text: deploy anything
    commands: ['\\b(kubectl|terraform)\\s+apply\\b']
  - be rude to the user
"""


def _l(**kw):
    return json.dumps(kw)


def transcript(tmp_path, sid, cwd, cmds, ts="2026-11-25T09:00:00Z"):
    d = tmp_path / "claudehome" / "projects" / ("-" + cwd.strip("/").replace("/", "-").replace(":", ""))
    d.mkdir(parents=True, exist_ok=True)
    base = {"sessionId": sid, "cwd": cwd, "timestamp": ts}
    lines = [_l(type="user", message={"role": "user", "content": "work on login"}, **base)]
    for i, c in enumerate(cmds):
        lines.append(_l(type="assistant", message={"content": [{"type": "tool_use", "id": f"{sid}{i}", "name": "Bash",
                                                                "input": {"command": c}}]}, **base))
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d / f"{sid}.jsonl"


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "app"
    (p / ".dinoguard").mkdir(parents=True)
    (p / ".dinoguard" / "contract.yaml").write_text(CONTRACT, encoding="utf-8")
    return p


def test_contract_parse_and_violations(proj):
    c = contract_mod.load(str(proj))
    assert c["errors"] == [] and len(c["should_not"]) == 3
    assert contract_mod.review_items(c) == ["run the tests before finishing", "be rude to the user"]
    hit = contract_mod.violations(c, "Bash", {"command": "kubectl apply -f x.yaml"})
    assert [r["text"] for r in hit] == ["deploy anything"]
    assert contract_mod.violations(c, "Write", {"file_path": str(proj / "payments" / "a.py")})
    assert not contract_mod.violations(c, "Write", {"file_path": str(proj / "src" / "payments.py")})
    bad = contract_mod.parse({"should_not": [{"text": "x", "commands": ["("]}, {"paths": ["a"]}]})
    assert len(bad["errors"]) == 2


def test_audit_contract_evidence_lines_and_selection(conn, tmp_path, proj):
    cwd = str(proj).replace("\\", "/")
    f = transcript(tmp_path, "A1", cwd, ["ls", "terraform apply -auto-approve", "cat payments/keys.txt"])
    transcript(tmp_path, "B1", "/other/repo", ["rm -rf build"])
    res = run_audit(conn, days=3650, goal="find contract breaks", project="app", notify=False)
    assert res["sessions"] == 1 and res["tool_calls"] == 3          # /other/repo is not selected
    by = {x["policy"]: x for x in res["findings"]}
    assert "contract:deploy anything" in by and "contract:touch the payments folder" in by
    assert by["contract:deploy anything"]["failure_mode"] == "contract violation: deploy anything"
    where = by["contract:deploy anything"]["evidence"][0]["where"]
    assert where.endswith(":3") and where.startswith(str(f))         # transcript file:line
    assert res["needs_review"][cwd] == ["run the tests before finishing", "be rude to the user"]
    scope = json.loads(conn.execute("SELECT scope_json FROM audits WHERE id=?", (res["audit_id"],)).fetchone()[0])
    assert scope["goal"] == "find contract breaks" and scope["selection"]["project"] == "app"
    # contract findings open insights with a fingerprint
    ins = conn.execute("SELECT * FROM insights WHERE fingerprint='contract:deploy anything'").fetchone()
    assert ins and ins["status"] == "open"
    only_b = run_audit(conn, days=3650, sessions=["B"], notify=False)
    pols = {x["policy"] for x in only_b["findings"]}
    assert only_b["sessions"] == 1 and "no-rm-rf" in pols and not any(p.startswith("contract:") for p in pols)


def test_audit_runs_history_and_coverage(conn, tmp_path):
    from dinoguard.hook.client import run
    run("claude", json.dumps({"session_id": "C1", "cwd": "/w/a", "hook_event_name": "PreToolUse",
                              "tool_name": "Bash", "tool_input": {"command": "ls"}}))
    run("claude", json.dumps({"session_id": "C2", "cwd": "/w/a", "hook_event_name": "PreToolUse",
                              "tool_name": "Bash", "tool_input": {"command": "ls"}}))
    transcript(tmp_path, "C1", "/w/a", ["ls"])
    res = run_audit(conn, days=3650, notify=False)
    assert res["coverage"]["store_sessions"] == 2 and res["coverage"]["store_sessions_covered"] == 1
    assert res["coverage"]["ratio"] == 0.5
    h = history(conn)
    assert h[0]["sessions"] == 1 and h[0]["coverage"]["by_project"] == {"/w/a": 1}


def test_recurrence_reopens_resolved_insight_and_alerts(conn, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("dinoguard.alerts.channels.send", lambda ch, text, cfg=None, subject=None: sent.append((ch, text)))
    config.update(lambda c: c.__setitem__("alerts", {"routes": {"critical": ["phone"]}}))
    transcript(tmp_path, "R1", "/w/r", ["rm -rf build"], ts="2026-11-20T09:00:00Z")
    run_audit(conn, days=3650, notify=False)
    ins = conn.execute("SELECT * FROM insights WHERE fingerprint='no-rm-rf'").fetchone()
    conn.execute("UPDATE insights SET status='resolved', resolved_at='2026-11-21T00:00:00Z' WHERE id=?", (ins["id"],))
    conn.commit()
    again = run_audit(conn, days=3650)                     # same old hit: resolved before? no -> hit is older
    assert again["recurred"] == []
    transcript(tmp_path, "R2", "/w/r", ["rm -rf dist"], ts="2026-11-24T09:00:00Z")
    res = run_audit(conn, days=3650)
    assert [r["insight"] for r in res["recurred"]] == [ins["id"]]
    row = conn.execute("SELECT * FROM insights WHERE id=?", (ins["id"],)).fetchone()
    assert row["status"] == "open" and row["resolved_at"] is None and "recurred" in row["comments_json"]
    assert sent and sent[0][0] == "phone" and "came back" in sent[0][1]
    assert conn.execute("SELECT COUNT(*) FROM insights WHERE fingerprint='no-rm-rf'").fetchone()[0] == 1


def test_suggest_installs_policy_in_observe(conn, tmp_path):
    from dinoguard.cli.data import audit_suggest
    from dinoguard.engine.registry import build
    transcript(tmp_path, "S1", "/w/s", ["rm -rf build"])
    run_audit(conn, days=3650, notify=False)
    fid = conn.execute("SELECT id FROM findings WHERE fingerprint='no-rm-rf'").fetchone()["id"]
    assert audit_suggest(fid, install=True) == 0
    dep = conn.execute("SELECT * FROM deployments ORDER BY created_at DESC").fetchone()
    assert dep["mode"] == "observe"
    assert config.load()["policies"][dep["policy"]]["mode"] == "observe"
    assert build(str(tmp_path)).get(dep["policy"]) is not None


# ---- 17.5 evals v2 -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,sess,passed", [
    ("session should cost under $1", {"cost_usd": 0.4}, True),
    ("session should cost under $1", {"cost_usd": 2.0}, False),
    ("finish in under 10 minutes", {"duration_s": 300}, True),
    ("finish in under 10 minutes", {"duration_s": 900}, False),
    ("no errors", {"errors": 0}, True),
    ("fewer than 50 tool calls", {"tool_calls": 60}, False),
    ("no denies", {"denies": 1}, False),
    ("at most 200k tokens", {"tokens": 150_000}, True),
])
def test_eval_draft_templates(text, sess, passed):
    from dinoguard.evals.draft import draft_spec
    from dinoguard.evals.safe_eval import safe_eval
    d = draft_spec(text)
    assert d["method"] == "template"
    env = {"cost_usd": 0, "duration_s": 0, "errors": 0, "tool_calls": 0, "denies": 0, "instructs": 0, "tokens": 0,
           **sess}
    assert safe_eval(d["spec"], env)["passed"] is passed


def test_eval_draft_unknown_text_fails_clearly():
    from dinoguard.evals.draft import draft_spec
    with pytest.raises(ValueError, match="without an LLM"):
        draft_spec("be nice")


def test_eval_rollback_and_compare(conn):
    from dinoguard.evals import manage
    from dinoguard.evals.runner import store_result
    conn.execute("INSERT INTO sessions(id, harness, environment) VALUES ('e1','claude','ci'), ('e2','cursor','ci')")
    manage.new(conn, "cheap", "code", "cost_usd < 1")
    assert manage.deploy(conn, "cheap") == 1
    manage.new(conn, "cheap", "code", "cost_usd < 5")
    assert manage.deploy(conn, "cheap") == 2
    ev = conn.execute("SELECT * FROM evals WHERE name='cheap' AND enabled=1").fetchone()
    store_result(conn, dict(ev), "e1", {"score": 1.0, "passed": True})
    store_result(conn, dict(ev), "e2", {"score": 0.0, "passed": False})
    by = {r["grp"]: r for r in manage.compare(conn, "cheap", "harness")}
    assert by["claude"]["avg_score"] == 1.0 and by["cursor"]["pass_rate"] == 0.0
    assert manage.compare(conn, "cheap", "environment")[0]["n"] == 2
    assert manage.rollback(conn, "cheap") == 1
    cur = conn.execute("SELECT * FROM evals WHERE name='cheap' AND enabled=1").fetchone()
    assert cur["spec"] == "cost_usd < 1" and cur["version"] == 3
    with pytest.raises(ValueError):
        manage.compare(conn, "cheap", "colour")


# ---- 17.6 packs v2 -----------------------------------------------------------------------------------------

def test_pack_source_forms(tmp_path):
    from dinoguard.packs.source import parse_source
    s = parse_source("acme/pack@v2.1.0")
    assert (s.kind, s.owner, s.repo, s.tag) == ("github", "acme", "pack", "v2.1.0")
    assert parse_source("github:acme/pack@v2").tag == "v2"
    assert parse_source("github:acme/pack").tag is None
    u = parse_source("https://github.com/acme/pack/releases/tag/v3.0.0")
    assert (u.owner, u.repo, u.tag) == ("acme", "pack", "v3.0.0")
    assert parse_source("https://github.com/acme/pack/releases/download/v1/dinoguard-pack.json").tag == "v1"
    assert parse_source(str(tmp_path)).kind == "dir"
    with pytest.raises(PackError):
        parse_source("not a pack!")


def test_pack_offline_and_mirror(monkeypatch):
    from dinoguard.packs import source
    src = source.parse_source("acme/pack@v1")
    monkeypatch.setenv("DG_NO_DOWNLOAD", "1")
    with pytest.raises(PackError, match="DG_NO_DOWNLOAD"):
        source.asset_urls(src)
    monkeypatch.delenv("DG_NO_DOWNLOAD")
    monkeypatch.setenv("DG_PACK_BASE_URL", "https://mirror.example/gh/")
    tag, assets = source.asset_urls(src)
    assert tag == "v1" and assets["SHA256SUMS"] == "https://mirror.example/gh/acme/pack/releases/download/v1/SHA256SUMS"
    _, latest = source.asset_urls(source.parse_source("acme/pack"))
    assert latest["x"].endswith("/acme/pack/releases/latest/download/x")


def test_pack_install_from_local_folder_with_pack_prefixed_selection(conn, tmp_path, monkeypatch):
    from dinoguard.packs.build import build_pack
    from dinoguard.packs.install import install_pack, strip_pack_prefix
    monkeypatch.setenv("DG_NO_DOWNLOAD", "1")                # local folders never need the network
    pol = tmp_path / "pols"
    pol.mkdir()
    (pol / "team_policies.py").write_text(
        "from dinoguard import policy, deny, allow\n"
        "@policy(name='no-refunds', description='x', events=['PreToolUse'])\n"
        "def a(ctx):\n    return allow()\n"
        "@policy(name='no-payouts', description='y', events=['PreToolUse'])\n"
        "def b(ctx):\n    return allow()\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    import shutil
    shutil.copytree(pol, tmp_path / ".dinoguard" / "policies")
    built = build_pack("team", "acme", str(tmp_path / "out"))
    assert set(built["policies"]) >= {"no-refunds", "no-payouts"}
    res = install_pack(str(tmp_path / "out"), policies=["team:no-refunds"])
    assert res["policies"] == ["no-refunds"] and res["publisher"] == "acme"
    assert strip_pack_prefix(["team/x", "y"], "team") == ["x", "y"]
