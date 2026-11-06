# viva-la-trace contracts

## HTTP API (owner: web/, Next.js on Vercel)

Auth: `Authorization: Bearer <key>` where key = `vlt_` + 43 url-safe base64 chars (32 random bytes).
Server stores only `sha256(key)` hex in `keys.key_hash`. Unknown key on an authed route: 401 `{"error": "bad key"}`.
All bodies JSON. All errors: non-2xx with `{"error": "<message>"}`.

Trace metadata object `Meta` (sent by CLI, validated at the boundary, 400 on any violation):
- `hash`: 64 lowercase hex = sha256 of the exact uploaded file bytes
- `harness`: `"claude_code"` | `"codex"`
- `title`: string, 1..140 chars (first user message, already anonymized)
- `step_hashes`: array of 64-hex strings, length >= 1
- `turns`: int >= 1 (number of user steps)
- `bytes`: int, 1..50_000_000, equal to uploaded file size
- `tags`: array of 0..6 strings, each 1..32 chars, lowercase

Routes:
1. `POST /api/keys` (no auth) -> 200 `{"key": "vlt_..."}`
2. `POST /api/traces/init` (auth) body `Meta` ->
   - `{"status": "exists"}` if a row with that hash exists
   - else `{"status": "upload", "url": "<supabase signed upload url for traces/<hash>.json>"}`
   CLI then `PUT`s the file bytes to `url` with `Content-Type: application/json`.
3. `POST /api/traces/commit` (auth) body `Meta` + `"replaces": [hash, ...]` (may be empty) ->
   server checks the object `traces/<hash>.json` exists in storage (400 if not), inserts the row (idempotent: existing hash = no-op),
   then supersedes: every row with the same key_id that is in `replaces` OR whose step_hashes is a strict prefix of the new step_hashes
   gets `superseded_by = hash` and its storage object deleted.
   -> 200 `{"status": "ok", "superseded": [hash, ...]}`
4. `GET /api/mine` (auth) -> `[{hash, harness, title, turns, tags, created_at, superseded_by}]` newest first, all of this key's rows
5. `GET /api/stats` -> `{"turns": int, "traces": int, "contributors": int, "by_harness": {"claude_code": int, "codex": int}}`
   counting only rows where superseded_by is null; contributors = distinct key_id.
6. `GET /api/feed?tag=<optional>` -> all non-superseded rows, newest first, `[{hash, harness, title, turns, tags, created_at, url}]`,
   `url` = public storage URL of `traces/<hash>.json`.

Storage: Supabase bucket `traces` (public read). Object path `<hash>.json`. Schema: `supabase/schema.sql`.
Server env (Vercel, server-only): `SUPABASE_URL`, `SUPABASE_SECRET_KEY`.
Public base URL of the deployed site is the CLI's `VLT_SERVER` default.

## Trace file format

ATIF v1.8 (Harbor, Apache-2.0) JSON, one trajectory per file, `schema_version: "ATIF-v1.8"`.
`agent.name` is `"claude-code"` or `"codex"`. `extra.vlt` = `{"harness", "tags", "vlt_version"}`. No paths, no usernames.
