import { all, db, keyId, route } from "@/lib/server";

export const GET = route(async (req) => {
  const key_id = await keyId(req);
  return all((from, to) =>
    db.from("traces").select("hash, harness, title, turns, tags, created_at, superseded_by").eq("key_id", key_id)
      .order("created_at", { ascending: false }).order("hash").range(from, to),
  );
});
