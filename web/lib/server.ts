import { createClient } from "@supabase/supabase-js";
import { createHash, randomBytes } from "node:crypto";
import { connection } from "next/server";

export const db = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SECRET_KEY!, {
  auth: { persistSession: false },
});
export const bucket = db.storage.from("traces");

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function route(fn: (req: Request) => Promise<unknown>, cacheSeconds = 0) {
  const headers = cacheSeconds ? { "Cache-Control": `public, s-maxage=${cacheSeconds}, stale-while-revalidate=${cacheSeconds}` } : undefined;
  return async (req: Request) => {
    try {
      return Response.json(await fn(req), { headers });
    } catch (e) {
      if (e instanceof HttpError) return Response.json({ error: e.message }, { status: e.status });
      console.error(e);
      // Supabase errors are plain objects, not Error instances.
      return Response.json({ error: e instanceof Error ? e.message : JSON.stringify(e) }, { status: 500 });
    }
  };
}

export function must<T>({ data, error }: { data: T | null; error: unknown }): T {
  if (error) throw error;
  return data as T;
}

// Supabase caps every select at its `max-rows` setting (1000 by default), so full scans page.
const PAGE = 1000;
export async function all<T>(page: (from: number, to: number) => PromiseLike<{ data: T[] | null; error: unknown }>) {
  const out: T[] = [];
  for (let from = 0; ; from += PAGE) {
    const rows = must(await page(from, from + PAGE - 1));
    out.push(...rows);
    if (rows.length < PAGE) return out;
  }
}

const sha256 = (s: string) => createHash("sha256").update(s).digest("hex");

export async function newKey() {
  const key = "vlt_" + randomBytes(32).toString("base64url");
  must(await db.from("keys").insert({ key_hash: sha256(key) }));
  return key;
}

export async function keyId(req: Request): Promise<string> {
  const m = /^Bearer (vlt_[A-Za-z0-9_-]{43})$/.exec(req.headers.get("authorization") ?? "");
  const row: { id: string } | null = m && must(await db.from("keys").select("id").eq("key_hash", sha256(m[1])).maybeSingle());
  if (!row) throw new HttpError(401, "bad key");
  return row.id;
}

export type Meta = {
  hash: string;
  harness: "claude_code" | "codex";
  title: string;
  step_hashes: string[];
  turns: number;
  bytes: number;
  tags: string[];
};

const HEX = /^[0-9a-f]{64}$/;
export const isHashes = (v: unknown, min: number): v is string[] =>
  Array.isArray(v) && v.length >= min && v.every((h) => typeof h === "string" && HEX.test(h));
const isInt = (v: unknown, lo: number, hi = Infinity): v is number => Number.isInteger(v) && (v as number) >= lo && (v as number) <= hi;
// Code points, not UTF-16 units, so the limit matches the Python CLI's len().
const isText = (v: unknown, lo: number, hi: number) => typeof v === "string" && [...v].length >= lo && [...v].length <= hi;

export async function body(req: Request): Promise<Record<string, unknown>> {
  const b = await req.json().catch(() => null);
  if (typeof b !== "object" || b === null || Array.isArray(b)) throw new HttpError(400, "body must be a JSON object");
  return b;
}

export function meta(b: Record<string, unknown>): Meta {
  const bad = [
    typeof b.hash === "string" && HEX.test(b.hash) ? "" : "hash",
    b.harness === "claude_code" || b.harness === "codex" ? "" : "harness",
    isText(b.title, 1, 140) ? "" : "title",
    isHashes(b.step_hashes, 1) ? "" : "step_hashes",
    isInt(b.turns, 1) ? "" : "turns",
    isInt(b.bytes, 1, 50_000_000) ? "" : "bytes",
    Array.isArray(b.tags) && b.tags.length <= 6 && b.tags.every((t) => isText(t, 1, 32) && t === t.toLowerCase()) ? "" : "tags",
  ].filter(Boolean);
  if (bad.length) throw new HttpError(400, `invalid ${bad.join(", ")}`);
  const { hash, harness, title, step_hashes, turns, bytes, tags } = b as Meta;
  return { hash, harness, title, step_hashes, turns, bytes, tags };
}

export type Stats = { turns: number; traces: number; contributors: number; by_harness: Record<Meta["harness"], number> };

// connection() marks every caller (route or page) as per-request, never prerendered.
export async function stats(): Promise<Stats> {
  await connection();
  const rows = await all<{ turns: number; harness: Meta["harness"]; key_id: string }>((from, to) =>
    db.from("traces").select("turns, harness, key_id").is("superseded_by", null).order("hash").range(from, to),
  );
  const by_harness = { claude_code: 0, codex: 0 };
  for (const r of rows) by_harness[r.harness]++;
  return {
    turns: rows.reduce((n, r) => n + r.turns, 0),
    traces: rows.length,
    contributors: new Set(rows.map((r) => r.key_id)).size,
    by_harness,
  };
}

export type FeedItem = Pick<Meta, "hash" | "harness" | "title" | "turns" | "tags"> & { created_at: string; url: string };

export async function feed(tag: string | null): Promise<FeedItem[]> {
  await connection();
  let q = db.from("traces").select("hash, harness, title, turns, tags, created_at").is("superseded_by", null);
  if (tag) q = q.contains("tags", [tag]);
  const rows = must(await q.order("created_at", { ascending: false }));
  return rows.map((r) => ({ ...r, url: bucket.getPublicUrl(`${r.hash}.json`).data.publicUrl }));
}
