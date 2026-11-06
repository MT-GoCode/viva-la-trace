"use client";
import { useEffect, useRef, useState } from "react";
import type { FeedItem, Stats } from "@/lib/server";

const POLL_MS = 3000;
const HARNESS = { claude_code: "Claude Code", codex: "Codex" } as const;

function Counter({ value }: { value: number }) {
  const [shown, setShown] = useState(value);
  const [bump, setBump] = useState(0);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    if (start === value) return;
    setBump((b) => b + 1);
    const t0 = performance.now();
    let frame = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / 1200);
      from.current = Math.round(start + (value - start) * (1 - (1 - p) ** 3));
      setShown(from.current);
      if (p < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return (
    <div key={bump} className={bump ? "counter bump" : "counter"}>
      {shown.toLocaleString("en-US")}
    </div>
  );
}

export default function Live(initial: { stats: Stats; feed: FeedItem[] }) {
  const [stats, setStats] = useState(initial.stats);
  const [items, setItems] = useState(initial.feed);
  const [tag, setTag] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    const pull = async () => {
      const [s, f] = await Promise.all([
        fetch("/api/stats").then((r) => r.json()),
        fetch(tag ? `/api/feed?tag=${encodeURIComponent(tag)}` : "/api/feed").then((r) => r.json()),
      ]);
      if (!live) return;
      setStats(s);
      setItems(f);
    };
    pull();
    const id = setInterval(pull, POLL_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [tag]);

  const cc = stats.by_harness.claude_code;
  const total = cc + stats.by_harness.codex;
  const ccPct = total ? (100 * cc) / total : 50;
  const urls = `data:text/plain;charset=utf-8,${encodeURIComponent(items.map((i) => i.url).join("\n") + "\n")}`;

  return (
    <>
      <section className="hero">
        <Counter value={stats.turns} />
        <div className="counter-label">turns taken back</div>
        <p className="tagline">The labs keep your traces. <em>Take them back.</em></p>
      </section>

      <section className="stats">
        <div><b>{stats.traces.toLocaleString("en-US")}</b> traces</div>
        <div><b>{stats.contributors.toLocaleString("en-US")}</b> contributors</div>
        <div className="split">
          <div className="split-bar">
            <span className="cc" style={{ width: `${ccPct}%` }} />
            <span className="cx" style={{ width: `${100 - ccPct}%` }} />
          </div>
          <div className="split-legend">
            <span>Claude Code {cc}</span>
            <span>Codex {stats.by_harness.codex}</span>
          </div>
        </div>
      </section>

      <section className="install">
        <code>$ pipx install viva-la-trace</code>
        <button
          onClick={() => navigator.clipboard.writeText("pipx install viva-la-trace").then(() => setCopied(true))}
          onMouseLeave={() => setCopied(false)}
        >
          {copied ? "copied!" : "copy"}
        </button>
      </section>

      <section className="feed">
        <header>
          <h2>Live feed</h2>
          {tag && <button className="tag active" onClick={() => setTag(null)}>#{tag} ✕</button>}
          <a className="all" href={urls} download="viva-la-trace-urls.txt">download all ↓ urls.txt</a>
        </header>
        {items.length === 0 && <p className="empty">No traces yet. Be the first.</p>}
        <ul>
          {items.map((i) => (
            <li key={i.hash}>
              <span className={`harness ${i.harness}`}>{HARNESS[i.harness]}</span>
              <span className="t">{i.title}</span>
              <span className="turns">{i.turns} turns</span>
              <span className="tags">
                {i.tags.map((t) => (
                  <button key={t} className="tag" onClick={() => setTag(t)}>#{t}</button>
                ))}
              </span>
              <a className="dl" href={i.url} download>json ↓</a>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
