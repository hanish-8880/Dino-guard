"""The evaluator service: canonical event -> decision, with history, tokens, reviewer, approvals and storage.

Used in-process by the hook (before a daemon is configured) and by the daemon.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

from . import config as config_mod
from .engine import registry as reg_mod
from .engine.decisions import Decision
from .engine.events import CanonicalEvent
from .engine.runner import Evaluation, evaluate
from .store import db, drift, queries, writer
from .tokens import transcript


class Service:
    def __init__(self, cfg: dict | None = None, registry: reg_mod.Registry | None = None,
                 conn: sqlite3.Connection | None = None, explicit_policies=(), approver=None, reviewer=None):
        self._cfg = cfg
        self._registry = registry
        self._registries: dict[str, reg_mod.Registry] = {}
        self._explicit = tuple(explicit_policies)
        self.conn = conn or db.connect()
        self._lock = threading.RLock()
        self._approver = approver
        self._reviewer = reviewer

    @property
    def cfg(self) -> dict:
        return self._cfg if self._cfg is not None else config_mod.load()

    def registry_for(self, cwd: str | None) -> reg_mod.Registry:
        if self._registry is not None:
            return self._registry
        key = os.path.normcase(cwd or "")
        if key not in self._registries:
            self._registries[key] = reg_mod.build(cwd, self._explicit)
        return self._registries[key]

    def reload(self) -> None:
        with self._lock:
            self._registries.clear()

    def reviewer(self, cfg: dict):
        if self._reviewer is not None:
            return self._reviewer
        if not (cfg.get("reviewer") or {}).get("url"):
            return None
        from .reviewer.review import Reviewer
        return Reviewer(cfg, conn=self.conn)

    def approver(self, cfg: dict):
        if self._approver is not None:
            return self._approver
        from .approvals.flow import make_approver
        return make_approver(cfg, self.conn)

    def handle(self, event: CanonicalEvent) -> Evaluation:
        t0 = time.perf_counter()
        with self._lock:
            cfg = self.cfg
            reg = self.registry_for(event.session.get("cwd"))
            history = queries.session_history(self.conn, event.session_id)
            if event.event == "UserPromptSubmit" and event.prompt:
                history.prompts.append(event.prompt)
                if not history.first_prompt:
                    history.first_prompt = event.prompt
            tokens = transcript.session_tokens(self.conn, event.session_id, event.session.get("transcript_path"))
        ev = evaluate(event, reg, cfg, history=history, tokens=tokens, reviewer=self.reviewer(cfg),
                      approver=self.approver(cfg))
        latency = (time.perf_counter() - t0) * 1000
        if event.event == "PreToolUse" and ev.decision.kind != "deny":
            from .undo import maybe_checkpoint
            with self._lock:
                maybe_checkpoint(self.conn, event, ev.decision, cfg)
        with self._lock:
            writer.record(self.conn, ev, tokens, latency, cfg)
            drift.observe(self.conn, event)
        if event.event == "SessionEnd":
            self._on_session_end(event.session_id)
        return ev

    def _on_session_end(self, session_id: str) -> None:
        try:
            from .evals.runner import run_for_session
            run_for_session(self.conn, session_id, self.cfg)
        except Exception:  # noqa: BLE001 - evals must never break the hook
            pass

    def decide(self, event: CanonicalEvent) -> Decision:
        return self.handle(event).decision
