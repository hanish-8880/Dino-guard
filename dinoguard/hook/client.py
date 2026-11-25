"""Thin hook entrypoint: `dinoguard-hook <harness>` reads native JSON on stdin, prints the native response.

Fail-closed: once a daemon is configured, any failure to reach it (or a version mismatch) denies.
"""
from __future__ import annotations

import json
import sys

FAIL_HINT = "Ask the user to run `dinoguard doctor` in their terminal."


def run(harness: str, stdin_text: str, event_hint: str | None = None) -> tuple[int, str, str]:
    from ..adapters import get_adapter
    adapter = get_adapter(harness)
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
    try:
        stdin_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        stdin_text = ""
    code, out, err = run(harness, stdin_text, argv[1] if len(argv) > 1 else None)
    if out:
        sys.stdout.write(out)
        sys.stdout.flush()
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
