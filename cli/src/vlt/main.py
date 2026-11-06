"""vlt: take your agent traces back.

State: ~/.vlt/session/<id>.json is the one record per session (single writer: this module).
It holds the fingerprint of the raw file it was scrubbed from, the scrubbed file's hash, approval
and push history. Status is derived from that record and the current file; nothing else is tracked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .cache import ROOT, Store, h
from .sources import Session, list_sessions, load

SERVER = os.environ.get("VLT_SERVER", "https://viva-la-trace.vercel.app")
PARALLEL = 5
console = Console()
store = Store()


def fingerprint(s: Session) -> str:
    return f"{s.mtime}:{s.bytes}"


def record(s: Session) -> dict:
    return store.get("session", s.id) or {}


def status(s: Session) -> str:
    r = record(s)
    if r.get("excluded"):
        return "[dim]excluded[/]"
    if r.get("fingerprint") != fingerprint(s):
        return "[yellow]grew ⟳[/]" if r.get("pushed") else "new"
    if r.get("file_hash") in r.get("pushed", []):
        return "[green]live ✓[/]"
    if r.get("pushed"):
        return "[yellow]needs re-push[/]"
    return "[cyan]scrubbed[/]"


def pick(args_ids: list[str], sessions: list[Session]) -> list[Session]:
    if args_ids == ["all"]:
        return sessions
    # Short numbers are row numbers from `vlt ls`; anything 6+ chars is a session-id prefix (ids are UUIDs).
    return [sessions[int(a) - 1] if a.isdigit() and len(a) < 6 else next(s for s in sessions if s.id.startswith(a))
            for a in args_ids]


def cmd_ls(args):
    sessions = list_sessions()
    t = Table(title=f"{len(sessions)} sessions", header_style="bold red")
    for c in ("#", "harness", "project", "when", "size", "status"):
        t.add_column(c)
    for i, s in enumerate(sessions[: args.n], 1):
        t.add_row(str(i), "claude" if s.harness == "claude_code" else "codex", s.project.replace(str(Path.home()), "~")[-48:],
                  time.strftime("%m-%d %H:%M", time.localtime(s.mtime)), f"{s.bytes / 1e6:.1f}MB", status(s))
    console.print(t)


def _scrub_one(s: Session) -> tuple[Session, dict | None, str]:
    from .scrub import scrub

    lines: list[str] = []
    try:
        traj = load(s)
        cached = store.get("scrubbed", h(traj))
        r = cached or scrub(s.harness, traj, store, log=lines.append)
        store.put("scrubbed", h(traj), r)
        return s, r, " · ".join(lines) or "cached"
    except Exception as e:  # reported per session, never swallowed: the session stays unscrubbed
        return s, None, f"[red]FAILED: {e}[/]"


def cmd_scrub(args):
    targets = pick(args.ids, list_sessions())
    with ProcessPoolExecutor(PARALLEL) as pool:
        for fut in as_completed([pool.submit(_scrub_one, s) for s in targets]):
            s, r, log = fut.result()
            console.print(f"[bold]{s.id[:8]}[/] {log}")
            if r is None:
                continue
            rec = record(s)
            if "excluded" in r:
                store.put("session", s.id, {**rec, "fingerprint": fingerprint(s), "excluded": r["excluded"]})
                console.print(f"  [dim]excluded: {r['excluded']}[/]")
                continue
            body = json.dumps(r["traj"], ensure_ascii=False, sort_keys=True).encode()
            (ROOT / "out").mkdir(parents=True, exist_ok=True)
            (ROOT / "out" / f"{h(body)}.json").write_bytes(body)
            store.put("session", s.id, {**rec, "fingerprint": fingerprint(s), "file_hash": h(body), "excluded": None,
                                        "meta": {k: r[k] for k in ("title", "tags", "step_hashes", "turns")},
                                        "table": r["table"], "dropped": r["dropped"], "rephrased": r["rephrased"]})
            if len(targets) == 1:
                show(s)


def show(s: Session):
    r = record(s)
    t = Table(title=f"{s.id[:8]}: {len(r['table'])} replacements, {len(r['rephrased'])} rephrased, {len(r['dropped'])} dropped",
              header_style="bold red")
    t.add_column("original")
    t.add_column("→ placeholder")
    for k, v in sorted(r["table"].items(), key=lambda kv: kv[1]):
        t.add_row(k if not v.startswith("SECRET") else k[:4] + "…", v)
    console.print(t)
    console.print(f"tags: {', '.join(r['meta']['tags'])}   title: {r['meta']['title'][:80]}")


def api(path: str, body: dict | None = None, key: str | None = None):
    req = urllib.request.Request(SERVER + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})},
                                 method="GET" if body is None else "POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def key() -> str:
    p = ROOT / "key"
    if not p.exists():
        sys.exit("run `vlt login` first")
    return p.read_text().strip()


def cmd_login(args):
    p = ROOT / "key"
    if p.exists():
        console.print("already logged in")
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    p.write_text(api("/api/keys", {})["key"])
    p.chmod(0o600)
    console.print("[green]logged in.[/] Your key is in ~/.vlt/key")


def cmd_push(args):
    k = key()
    sessions = [s for s in (pick(args.ids, list_sessions()) if args.ids else list_sessions()) if (r := record(s)).get("file_hash") and not r.get("excluded")
                and r.get("fingerprint") == fingerprint(s) and r["file_hash"] not in r.get("pushed", [])]
    if not sessions:
        console.print("nothing to push")
        return
    for s in sessions:
        show(s)
    if not args.yes and input(f"push {len(sessions)} trace(s) to the public dataset? [y/N] ").strip().lower() != "y":
        return
    for s in sessions:
        r = record(s)
        body = (ROOT / "out" / f"{r['file_hash']}.json").read_bytes()
        meta = {"hash": r["file_hash"], "harness": s.harness, "bytes": len(body), **r["meta"]}
        init = api("/api/traces/init", meta, k)
        if init["status"] == "upload":
            urllib.request.urlopen(urllib.request.Request(init["url"], data=body, method="PUT",
                                                          headers={"Content-Type": "application/json"}))
        done = api("/api/traces/commit", {**meta, "replaces": [x for x in r.get("pushed", []) if x != r["file_hash"]]}, k)
        store.put("session", s.id, {**r, "pushed": r.get("pushed", []) + [r["file_hash"]]})
        console.print(f"[green]▲[/] {s.id[:8]} {meta['turns']} turns  superseded {len(done['superseded'])}")
    stats = api("/api/stats")
    console.print(f"[bold red]World total: {stats['turns']:,} turns taken back.[/] {SERVER}")


def cmd_exclude(args):
    for s in pick(args.ids, list_sessions()):
        store.put("session", s.id, {**record(s), "excluded": args.reason})
        console.print(f"{s.id[:8]} excluded: {args.reason}")


def main():
    p = argparse.ArgumentParser(prog="vlt", description="Take your agent traces back.")
    sub = p.add_subparsers(dest="cmd")
    ls = sub.add_parser("ls", help="list sessions")
    ls.add_argument("-n", type=int, default=40)
    sc = sub.add_parser("scrub", help="anonymize sessions (# from ls, id prefix, or 'all'), 5 at a time")
    sc.add_argument("ids", nargs="+")
    sh = sub.add_parser("show", help="show a scrubbed session's replacement table")
    sh.add_argument("id")
    pu = sub.add_parser("push", help="upload scrubbed sessions")
    pu.add_argument("ids", nargs="*", help="only these sessions (default: every scrubbed one)")
    pu.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("login", help="get a free upload key")
    ex = sub.add_parser("exclude", help="never upload these sessions")
    ex.add_argument("ids", nargs="+")
    ex.add_argument("--reason", default="excluded by user")
    args = p.parse_args()
    if args.cmd in (None, "ls"):
        args.n = getattr(args, "n", 40)
        return cmd_ls(args)
    if args.cmd == "show":
        return show(pick([args.id], list_sessions())[0])
    {"scrub": cmd_scrub, "push": cmd_push, "login": cmd_login, "exclude": cmd_exclude}[args.cmd](args)
