import { all, body, bucket, db, HttpError, isHashes, keyId, meta, must, route } from "@/lib/server";

const isStrictPrefix = (a: string[], b: string[]) => a.length < b.length && a.every((h, i) => h === b[i]);

export const POST = route(async (req) => {
  const key_id = await keyId(req);
  const b = await body(req);
  const m = meta(b);
  if (!isHashes(b.replaces, 0)) throw new HttpError(400, "invalid replaces");
  const replaces = new Set(b.replaces);

  const path = `${m.hash}.json`;
  const info = await bucket.info(path);
  if (info.error && "code" in info.error && info.error.code === "NoSuchKey") throw new HttpError(400, "trace not uploaded");
  const { size } = must(info);
  if (size !== m.bytes) throw new HttpError(400, `uploaded size ${size} != bytes ${m.bytes}`);

  must(await db.from("traces").upsert({ ...m, key_id }, { onConflict: "hash", ignoreDuplicates: true }));

  const live = await all<{ hash: string; step_hashes: string[] }>((from, to) =>
    db.from("traces").select("hash, step_hashes").eq("key_id", key_id).is("superseded_by", null).neq("hash", m.hash).order("hash").range(from, to),
  );
  const superseded = live.filter((r) => replaces.has(r.hash) || isStrictPrefix(r.step_hashes, m.step_hashes)).map((r) => r.hash);
  if (superseded.length) {
    must(await db.from("traces").update({ superseded_by: m.hash }).in("hash", superseded));
    must(await bucket.remove(superseded.map((h) => `${h}.json`)));
  }
  return { status: "ok", superseded };
});
