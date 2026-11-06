import { body, bucket, db, keyId, meta, must, route } from "@/lib/server";

export const POST = route(async (req) => {
  await keyId(req);
  const { hash } = meta(await body(req));
  if (must(await db.from("traces").select("hash").eq("hash", hash).maybeSingle())) return { status: "exists" };
  // upsert lets a client re-upload after a failed commit.
  const { signedUrl } = must(await bucket.createSignedUploadUrl(`${hash}.json`, { upsert: true }));
  return { status: "upload", url: signedUrl };
});
