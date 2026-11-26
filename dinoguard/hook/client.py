"""Thin hook entrypoint: `dinoguard-hook <harness>` reads native JSON on stdin, prints the native response.

Fail-closed: once a daemon is configured, any failure to reach it (or a version mismatch) denies.
"""
from __future__ import annotations

import json
import sys

FAIL_HINT = "Ask the user to run `dinoguard doctor` in their terminal."


def run(harness: str, stdin_text: str, event_hint: str | None = None) -> tuple[int, str, str]:
    from ..adapters import get_adapter, sniff
    adapter = get_adapter(sniff(harness))
    event_name = event_hint
    try:
        raw = json.loads(stdin_text or "{}")
        if isinstance(raw, dict) and event_hint and not raw.get("hook_event_name") and not raw.get("hookEventName"):
            raw["_event"] = event_hint     # harnesses whose payload doesn't name the event (Copilot)
        event_name = (raw.get("hook_event_name") or event_hint) if isinstance(raw, dict) else event_hint
        event = adapter.to_canonical(raw)
    except Exception as e:  # noqa: BLE001
        r = adapter.fail_closed(event_name, f"DinoGuard could not parse the hook payload ({type(e).__name__}). "
                                            f"The action is blocked. {FAIL_HINT}")
        return r.exit_code, r.stdout, r.stderr

    from .. import config
    try:
        cfg = config.load()
    except Exception:  # noqa: BLE001
        cfg = {"daemon": {"enabled": False}}

    if (cfg.get("daemon") or {}).get("enabled"):
        from ..daemon import client as dclient
        try:
            try:
                decision = dclient.evaluate(event, timeout=dclient.timeout_for(cfg))
            except dclient.DaemonError as first:
                # Not running at all (e.g. after a reboot): start it once and retry. Still the single evaluator;
                # version skew, timeouts and bad tokens are never retried, and a deliberate stop is respected.
                if not (str(first).startswith("daemon unreachable") and _autostart(cfg)):
                    raise
                decision = dclient.evaluate(event, timeout=dclient.timeout_for(cfg))
        except dclient.DaemonError as e:
            _note_guard_down(event, str(e))
            r = adapter.fail_closed(event.event, f"DinoGuard daemon problem: {e}. Every guarded action is blocked "
                                                 f"until this is fixed. {FAIL_HINT}")
            return r.exit_code, r.stdout, r.stderr
    else:
        try:
            from ..service import Service
            decision = Service(cfg=cfg).decide(event)
        except Exception as e:  # noqa: BLE001
            _note_guard_down(event, f"in-process evaluation failed: {type(e).__name__}: {e}")
            r = adapter.fail_closed(event.event, f"DinoGuard failed while checking this action "
                                                 f"({type(e).__name__}). The action is blocked. {FAIL_HINT}")
            return r.exit_code, r.stdout, r.stderr
    r = adapter.to_native(event, decision)
    return r.exit_code, r.stdout, r.stderr


def _autostart(cfg: dict) -> bool:
    """Start the daemon if it simply isn't running. One hook at a time (lock file); returns True if it's up."""
    import os
    import time

    from .. import paths
    if (cfg.get("daemon") or {}).get("autostart", True) is False or (paths.home() / "daemon.stopped").exists():
        return False
    lock = paths.home() / ".autostart.lock"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > 20:
            lock.unlink()                              # a stale lock from a hook that died mid-start
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:                                    # another hook is starting it: wait for that
        from ..daemon.server import is_running
        for _ in range(30):
            if is_running(0.3):
                return True
            time.sleep(0.1)
        return False
    try:
        os.close(fd)
        from ..daemon.server import is_running, start_detached
        if not is_running(0.5):
            start_detached()
        ok = is_running(2.0)
        if ok:
            from ..store.writer import stream_note
            stream_note("guard_up", reason="daemon was not running; the hook started it")
        return ok
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _note_guard_down(event, why: str) -> None:
    try:
        from ..store.writer import stream_note, summarize
        stream_note("guard_down", session=event.session_id, event=event.event, tool=event.tool_name,
                    summary=summarize(event), decision="deny", reason=why)
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    harness = argv[0] if argv else "claude"
    try:   # harnesses send UTF-8; never let the console code page (cp1252 on Windows) garble it
        data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
        stdin_text = data.decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001
        stdin_text = ""
    code, out, err = run(harness, stdin_text, argv[1] if len(argv) > 1 else None)
    # Write UTF-8 bytes: a reason the console code page can't encode must not crash the hook
    # (a crash is a non-blocking error for most harnesses, i.e. it would fail open).
    _write(sys.stdout, out)
    _write(sys.stderr, err)
    return code


def _write(stream, text: str) -> None:
    if not text:
        return
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(text.encode("utf-8", errors="replace"))
        buf.flush()
    else:
        stream.write(text)
        stream.flush()


if __name__ == "__main__":
    sys.exit(main())
