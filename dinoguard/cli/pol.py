"""`dinoguard policies ...` and `dinoguard policy ...`."""
from __future__ import annotations

import inspect
import json
import os

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .. import config
from ..engine.modes import effective
from ..engine.registry import SELF_PROTECT, build

console = Console(highlight=False)


def _reload_daemon():
    try:
        from ..daemon.client import DaemonError, request
        request("reload", timeout=3.0)
    except Exception:  # noqa: BLE001 - daemon optional
        pass


def _parse_value(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def _is_pack_ref(t: str | None) -> bool:
    return bool(t) and "/" in t


def cmd_policies(args) -> int:
    a, t = args.action, args.target
    if a == "add":
        from ..packs.install import install_pack
        res = install_pack(t, policies=args.policy, categories=args.category, select_all=args.all, mode=args.mode)
        console.print(f"[green]✔[/] installed {res['publisher']}/{res['name']}@{res['tag']} digest {res['digest'][:16]}…")
        console.print("[dim]SHA256SUMS checks integrity of the download; it is not a signature of the publisher.[/]")
        _reload_daemon()
        return 0
    if a == "show" and _is_pack_ref(t) and not build(os.getcwd(), packs=False).get(t):
        from ..packs.install import show_manifest
        console.print_json(json.dumps(show_manifest(t)))
        console.print("[dim]Read the manifest only; no pack code was run.[/]")
        return 0
    if a == "remove":
        from ..packs.install import remove_pack
        remove_pack(t)
        console.print(f"[green]✔[/] removed {t}")
        _reload_daemon()
        return 0

    cfg = config.load()
    reg = build(os.getcwd(), args.policy_files)
    if a == "list":
        tbl = Table("name", "category", "on", "mode", "authority", "source", title=f"{len(reg.policies)} policies")
        for p in reg.policies:
            e = effective(p, cfg)
            tbl.add_row(p.name, p.category, "[green]on[/]" if e.enabled else "[dim]off[/]",
                        ("[red]enforce[/]" if e.mode == "enforce" else "[cyan]observe[/]") if e.enabled else "",
                        p.authority, p.source)
        console.print(tbl)
        for e in reg.errors:
            console.print(f"[yellow]![/] failed to load {e['file']}: {e['error']}")
        return 0
    if not t:
        console.print("[red]policy name required[/]")
        return 2
    p = reg.get(t)
    if p is None and ":" in t:          # pack:policy -> the registered pack/policy name
        p = reg.get(t.replace(":", "/", 1))
        t = p.name if p else t
    if p is None:
        console.print(f"[red]unknown policy {t!r}[/]")
        return 2
    if a == "show":
        e = effective(p, cfg)
        console.print_json(json.dumps({**p.summary(), "enabled": e.enabled, "mode": e.mode, "effective_params": e.params},
                                      default=str))
        try:
            console.print(Syntax(inspect.getsource(p.fn), "python"))
        except (OSError, TypeError):
            pass
        return 0
    if p.name == SELF_PROTECT and a in ("disable", "observe"):
        console.print("[red]guard-self-protect is always on and always enforced.[/]")
        return 2
    if a == "enable":
        config.set_policy(t, enabled=True)
    elif a == "disable":
        config.set_policy(t, enabled=False)
    elif a in ("observe", "enforce"):
        config.set_policy(t, enabled=True, mode=a)
    elif a == "set":
        params = {}
        for kv in args.values:
            k, _, v = kv.partition("=")
            params[k] = _parse_value(v)
        config.set_policy(t, params=params)
    console.print(f"[green]✔[/] {t}: {a} {' '.join(args.values or [])}")
    _reload_daemon()
    return 0


def cmd_policy(args) -> int:
    from ..authoring import draft as drafting
    from ..deploy import versions
    a, t = args.action, args.target
    if a == "new":
        d = drafting.draft_policy(t, name=args.name)
        console.print(f"[green]✔[/] drafted [bold]{d['name']}[/] ({d['method']}) → {d['path']}")
        console.print(Syntax(d["code"], "python"))
        console.print("Next: dinoguard policy test", d["name"], "· backtest · publish · deploy --mode observe")
        return 0
    if a == "test":
        res = drafting.test_policy(t)
        for r in res["results"]:
            mark = "[green]✔[/]" if r["ok"] else "[red]✘[/]"
            console.print(f" {mark} expected {r['expect']:<5} got {r['got']:<8} {r['example']}")
        return 0 if res["passed"] else 1
    if a == "backtest":
        from ..authoring.backtest import backtest
        res = backtest(t, days=args.days)
        console.print(f"[bold]{t}[/] over {res['events']} stored events ({args.days} days): would have blocked "
                      f"[red]{res['blocked']}[/], interrupted [yellow]{res['interrupted']}[/] working calls, "
                      f"instructed {res['instructed']}")
        for hit in res["hits"][:20]:
            console.print(f"  {hit['ts']} {hit['decision']:<8} {hit['tool']} {hit['summary']}")
        return 0
    if a == "publish":
        v = versions.publish(t, notes=args.notes)
        console.print(f"[green]✔[/] published {t} v{v} (immutable)")
        return 0
    if a == "deploy":
        v = versions.deploy(t, args.ver, args.mode)
        console.print(f"[green]✔[/] deployed {t} v{v} in {args.mode} mode")
        _reload_daemon()
        return 0
    if a == "rollback":
        v = versions.rollback(t)
        console.print(f"[green]✔[/] rolled back {t} to v{v}")
        _reload_daemon()
        return 0
    if a == "versions":
        for row in versions.list_versions(t):
            console.print(f" v{row['version']} {row['created_at']} {row['notes'] or ''}")
        return 0
    return 2
