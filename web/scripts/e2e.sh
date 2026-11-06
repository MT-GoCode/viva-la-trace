#!/usr/bin/env bash
# Real end-to-end check of the HTTP API against a deployed base URL, then deletes everything it created.
# Usage: SUPABASE_URL=... SUPABASE_SECRET_KEY=... scripts/e2e.sh https://host
set -euo pipefail
BASE=${1:?base url}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail() { echo "FAIL: $*"; exit 1; }
ok() { echo "ok   $*"; }
api() { curl -s -w '\n%{http_code}' "$@"; }  # body, then status on last line
code() { tail -n1 <<<"$1"; }
json() { sed '$d' <<<"$1"; }
sha() { shasum -a 256 "$@" | cut -d' ' -f1; }
hex() { printf %s "$1" | shasum -a 256 | cut -d' ' -f1; }
SB=(-H "apikey: $SUPABASE_SECRET_KEY" -H "Authorization: Bearer $SUPABASE_SECRET_KEY")

KEY=$(curl -sf -X POST "$BASE/api/keys" | jq -r .key)
[[ $KEY =~ ^vlt_[A-Za-z0-9_-]{43}$ ]] || fail "key shape: $KEY"; ok "POST /api/keys"
AUTH=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")
KEY_ID=$(curl -sf "${SB[@]}" "$SUPABASE_URL/rest/v1/keys?select=id&key_hash=eq.$(hex "$KEY")" | jq -r '.[0].id')
cleanup() {
  curl -sf -X DELETE "${SB[@]}" "$SUPABASE_URL/rest/v1/traces?key_id=eq.$KEY_ID" >/dev/null
  curl -sf -X DELETE "${SB[@]}" -H "Content-Type: application/json" "$SUPABASE_URL/storage/v1/object/traces" \
    -d "{\"prefixes\": [\"$H1.json\", \"$H2.json\"]}" >/dev/null
  curl -sf -X DELETE "${SB[@]}" "$SUPABASE_URL/rest/v1/keys?id=eq.$KEY_ID" >/dev/null
  echo "cleaned up key $KEY_ID"
}

S1=$(hex "step one $RANDOM$RANDOM"); S2=$(hex "step two $RANDOM$RANDOM")
printf '{"schema_version":"ATIF-v1.8","steps":["%s"]}' "$S1" >"$T/a.json"
printf '{"schema_version":"ATIF-v1.8","steps":["%s","%s"]}' "$S1" "$S2" >"$T/b.json"
H1=$(sha "$T/a.json"); H2=$(sha "$T/b.json")
trap 'cleanup; rm -rf "$T"' EXIT
meta() { jq -nc --arg h "$1" --argjson s "$2" --argjson b "$3" --argjson n "$4" --argjson r "$5" \
  '{hash:$h, harness:"codex", title:"e2e test ✊ trace", step_hashes:$s, turns:$n, bytes:$b, tags:["e2e","punk"], replaces:$r}'; }
M1=$(meta "$H1" "[\"$S1\"]" "$(wc -c <"$T/a.json")" 1 '[]')
M2=$(meta "$H2" "[\"$S1\",\"$S2\"]" "$(wc -c <"$T/b.json")" 2 '[]')

r=$(api -X POST "$BASE/api/traces/init" -H "Authorization: Bearer vlt_$(printf 'x%.0s' {1..43})" -d "$M1")
[[ $(code "$r") == 401 && $(json "$r" | jq -r .error) == "bad key" ]] || fail "unknown key: $r"; ok "401 on unknown key"
r=$(api -X POST "$BASE/api/traces/init" "${AUTH[@]}" -d "$(jq -c '.title=""' <<<"$M1")")
[[ $(code "$r") == 400 && $(json "$r" | jq -r .error) == "invalid title" ]] || fail "bad meta: $r"; ok "400 on empty title"
r=$(api -X POST "$BASE/api/traces/commit" "${AUTH[@]}" -d "$M1")
[[ $(code "$r") == 400 ]] || fail "commit before upload: $r"; ok "400 commit before upload"

TURNS0=$(curl -sf "$BASE/api/stats" | jq .turns)
upload() {  # meta file
  local r; r=$(api -X POST "$BASE/api/traces/init" "${AUTH[@]}" -d "$1")
  [[ $(code "$r") == 200 && $(json "$r" | jq -r .status) == upload ]] || fail "init: $r"
  curl -sf -X PUT -H "Content-Type: application/json" --data-binary "@$2" "$(json "$r" | jq -r .url)" >/dev/null || fail "PUT"
}
upload "$M1" "$T/a.json"; ok "init -> upload url, PUT file"
r=$(api -X POST "$BASE/api/traces/commit" "${AUTH[@]}" -d "$(jq -c '.bytes+=1' <<<"$M1")")
[[ $(code "$r") == 400 ]] || fail "size mismatch: $r"; ok "400 on bytes mismatch"
r=$(api -X POST "$BASE/api/traces/commit" "${AUTH[@]}" -d "$M1")
[[ $(json "$r" | jq -c .) == '{"status":"ok","superseded":[]}' ]] || fail "commit 1: $r"; ok "commit 1"
r=$(api -X POST "$BASE/api/traces/commit" "${AUTH[@]}" -d "$M1")
[[ $(json "$r" | jq -r .status) == ok ]] || fail "idempotent commit: $r"; ok "commit 1 again is a no-op"
[[ $(curl -sf -X POST "$BASE/api/traces/init" "${AUTH[@]}" -d "$M1" | jq -r .status) == exists ]] || fail "exists"; ok "init -> exists"

sleep 3  # stats/feed are CDN-cached for 2 s
[[ $(curl -sf "$BASE/api/stats" | jq .turns) == $((TURNS0 + 1)) ]] || fail "stats turns"; ok "stats turns +1"
curl -sf "$BASE/api/feed?tag=e2e" | jq -e --arg h "$H1" 'any(.[]; .hash==$h and .harness=="codex" and .turns==1)' >/dev/null || fail "feed 1"
curl -sf "$(curl -sf "$BASE/api/feed?tag=e2e" | jq -r --arg h "$H1" '.[]|select(.hash==$h).url')" | cmp -s - "$T/a.json" || fail "public url bytes"
ok "feed?tag=e2e shows trace 1, public url serves the exact bytes"

upload "$M2" "$T/b.json"
r=$(api -X POST "$BASE/api/traces/commit" "${AUTH[@]}" -d "$M2")
[[ $(json "$r" | jq -c .) == "{\"status\":\"ok\",\"superseded\":[\"$H1\"]}" ]] || fail "commit 2: $r"; ok "commit 2 supersedes prefix trace 1"
sleep 3
[[ $(curl -sf "$BASE/api/stats" | jq .turns) == $((TURNS0 + 2)) ]] || fail "stats after supersede"; ok "stats turns = +2 (trace 1 dropped)"
curl -sf "$BASE/api/feed?tag=e2e" | jq -e --arg a "$H1" --arg b "$H2" 'any(.[]; .hash==$b) and all(.[]; .hash!=$a)' >/dev/null || fail "feed 2"
ok "feed shows trace 2 only"
# Authenticated read bypasses the public CDN cache, which may still serve the deleted object.
curl -s "${SB[@]}" "$SUPABASE_URL/storage/v1/object/traces/$H1.json" | jq -e '.error=="not_found"' >/dev/null || fail "object 1 not deleted"
ok "superseded storage object deleted"
curl -sf "$BASE/api/mine" "${AUTH[@]}" | jq -e --arg a "$H1" --arg b "$H2" \
  'length==2 and .[0].hash==$b and .[0].superseded_by==null and .[1].hash==$a and .[1].superseded_by==$b' >/dev/null || fail "mine"
ok "GET /api/mine newest first with superseded_by"
echo "ALL PASSED"
