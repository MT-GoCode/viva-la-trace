"""Anonymize one ATIF trajectory.

flag (detect-secrets + Presidio) -> Haiku decides on flagged turns with +-2 context -> code assigns
placeholders and applies exact replacements -> Haiku rephrases marked user turns -> a fresh Haiku
reviews until it finds nothing -> no secret detected in the raw session may appear in the output.
Every LLM result is cached by the hash of its input, so a grown session only pays for new turns.
"""
from __future__ import annotations

import copy
import json
import re

from . import llm, scan
from .cache import Store, chain_keys, h

CONTEXT = 2
# Haiku's window is 200k tokens; JSON-heavy transcript text runs ~3 chars/token, so 300k chars
# per batch leaves half the window for the prompt and the answer.
BATCH_CHARS = 300_000
# One huge turn (a file the agent read) is sent as the lines around each hit instead of whole.
STEP_CHARS = 60_000
REMOVED = "[removed by vlt]"
PLACEHOLDER = re.compile(r"\b[A-Z]+_\d+\b")
KINDS = ["ORG", "PERSON", "HOST", "URL", "EMAIL", "PROJECT", "ID", "LOCATION", "OTHER"]

_SUB = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "kind"],
        "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": KINDS}}}}
_IDS = {"type": "array", "items": {"type": "integer"}}
DECIDE = {"type": "object", "additionalProperties": False,
          "required": ["substitute", "rephrase", "drop", "exclude_trace", "tags"],
          "properties": {"substitute": _SUB, "rephrase": _IDS, "drop": _IDS,
                         "exclude_trace": {"type": ["string", "null"]},
                         "tags": {"type": "array", "items": {"type": "string"}}}}
REVIEW = {"type": "object", "additionalProperties": False, "required": ["substitute", "drop"],
          "properties": {"substitute": _SUB, "drop": _IDS}}
REPHRASE = {"type": "object", "additionalProperties": False, "required": ["text"], "properties": {"text": {"type": "string"}}}

DECIDE_PROMPT = """You anonymize coding-agent transcripts before they go into a public dataset.

Below are FLAGGED turns, each with up to 2 CONTEXT turns on each side, and the HITS an automatic scanner
found in the flagged turn. The scanner over-selects on purpose; many hits are harmless.

Decide, for the flagged turns only:
1. substitute: exact strings that identify a person, a company or organization, a private project or repo,
   a private host or URL, an email, an account/customer/user ID, or a location. Copy each string exactly as
   it appears. Include sensitive strings the scanner missed. Do NOT list public things: open-source
   libraries, public products and companies used as tools (GitHub, AWS, Anthropic, OpenAI), public URLs,
   language names, common words, code identifiers.
2. rephrase: step_ids of USER turns written in a personal voice, naming people, or describing private work.
3. drop: step_ids whose content is private data that substitution cannot fix (customer records, private
   documents, credential files, personal chats).
4. exclude_trace: a reason if the whole session is clearly employer or client work under confidentiality, else null.
5. tags: 1 to 6 short lowercase tags for languages, frameworks and task type.

TABLE lists strings already replaced; do not list them again.
If unsure whether a string is sensitive, substitute it. A false positive costs little. A leak cannot be undone.
"""
REVIEW_PROMPT = """These coding-agent transcript turns were already anonymized (placeholders look like ORG_1, PERSON_2).
Find anything that STILL identifies a person, company, private project, private host or URL, email, ID or location.
Copy each string exactly. Do not list placeholders, public libraries, public products or code identifiers.
Return empty lists if nothing is left.
"""
REPHRASE_PROMPT = """Rewrite this message from a software developer in plain, neutral English.
Keep every fact, instruction and code snippet. Keep every placeholder token (like ORG_1, PERSON_2) exactly.
Change wording and personal style only. Return the rewritten message as `text`.

MESSAGE:
"""


def _render(step: dict, hits: set[str]) -> str:
    text = json.dumps(step, ensure_ascii=False)
    if len(text) <= STEP_CHARS:
        return text
    lines = text.split("\\n")
    keep = sorted({j for i, line in enumerate(lines) if any(x in line for x in hits)
                   for j in range(max(0, i - 3), min(len(lines), i + 4))})
    return f'{{"step_id": {step["step_id"]}, "vlt_excerpt": {json.dumps(chr(10).join(lines[j] for j in keep))}}}'


def _packets(steps: list[dict], flagged: list[int], hits: list[dict]) -> list[str]:
    out = []
    for i in flagged:
        ctx = [_render(steps[j], set()) if j != i else _render(steps[j], set(hits[j]["secrets"]) | {t for _, t in hits[j]["pii"]})
               for j in range(max(0, i - CONTEXT), min(len(steps), i + CONTEXT + 1))]
        out.append(f"=== FLAGGED step_id {steps[i]['step_id']}\nHITS: {json.dumps(hits[i]['pii'] + [['SECRET', s] for s in hits[i]['secrets']], ensure_ascii=False)}\n"
                   + "\n".join(ctx))
    return out


def _batches(packets: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    batches, cur, size = [], [], 0
    for p in packets:
        if cur and size + len(p[1]) > BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p[1])
    return batches + [cur] if cur else batches


def _replacer(table: dict[str, str]):
    if not table:
        return lambda s: s
    pattern = re.compile("|".join(re.escape(k) for k in sorted(table, key=len, reverse=True)))
    return lambda s: pattern.sub(lambda m: table[m.group(0)], s)


def _map(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: _map(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map(v, fn) for v in obj]
    return obj


def _drop(step: dict) -> dict:
    step = {k: v for k, v in step.items() if k != "reasoning_content"}
    step["message"] = REMOVED
    for c in step.get("tool_calls", []):
        c["arguments"] = {"removed": REMOVED}
    for r in step.get("observation", {}).get("results", []):
        r["content"] = REMOVED
    return step


def scrub(harness: str, traj: dict, store: Store, log=print) -> dict:
    steps = traj["steps"]
    if not any(s["source"] == "agent" for s in steps) or not any(s["source"] == "user" for s in steps):
        return {"excluded": "no agent work"}
    keys = chain_keys(steps)

    hits = []
    for s in steps:
        k = h(s)
        found = store.get("scan", k)
        if found is None:
            found = scan.scan_step(s)
            store.put("scan", k, found)
        hits.append(found)
    flagged = [i for i, x in enumerate(hits) if x["secrets"] or x["pii"]]
    n_secrets = len({v for x in hits for v in x["secrets"]})
    log(f"flag: {len(flagged)}/{len(steps)} turns, {n_secrets} secrets")

    table = store.add_symbols([(v, "SECRET") for x in hits for v in x["secrets"]])
    todo = [i for i in flagged if store.get("decide", keys[i]) is None]
    for batch in _batches(list(zip(todo, _packets(steps, todo, hits)))):
        mine = {k: v for k, v in table.items() if any(k in p for _, p in batch)}
        decision = llm.ask(harness, DECIDE_PROMPT + f"\nTABLE: {json.dumps(mine, ensure_ascii=False)}\n\n" + "\n\n".join(p for _, p in batch), DECIDE)
        for i, _ in batch:
            store.put("decide", keys[i], decision)
        table = store.add_symbols([(x["text"], x["kind"]) for x in decision["substitute"] if x["text"].strip()])
    decisions = [store.get("decide", keys[i]) for i in flagged]
    log(f"decide: {len(todo)} new turns sent to {harness}, {len(flagged) - len(todo)} cached")

    excluded = next((d["exclude_trace"] for d in decisions if d["exclude_trace"]), None)
    if excluded:
        return {"excluded": excluded}

    ids = {s["step_id"]: i for i, s in enumerate(steps)}
    drop = {d_id for d in decisions for d_id in d["drop"] if d_id in ids}
    rephrase = {r for d in decisions for r in d["rephrase"] if r in ids and steps[ids[r]]["source"] == "user"} - drop
    tags = sorted({t.lower()[:32] for d in decisions for t in d["tags"] if t.strip()})[:6]

    def apply() -> list[dict]:
        rep = _replacer({scan.HOME: "~", **table})
        return [_drop(copy.deepcopy(s)) if s["step_id"] in drop else _map(s, rep) for s in steps]

    out = apply()
    rounds = 0
    while True:
        packets = _packets(out, flagged, hits)
        key = h(packets)
        review = store.get("review", key)
        if review is None:
            review = {"substitute": [], "drop": []}
            for batch in _batches(list(zip(flagged, packets))):
                r = llm.ask(harness, REVIEW_PROMPT + "\n\n" + "\n\n".join(p for _, p in batch), REVIEW)
                review["substitute"] += r["substitute"]
                review["drop"] += r["drop"]
            store.put("review", key, review)
        text = json.dumps(out, ensure_ascii=False)
        new_subs = [(x["text"], x["kind"]) for x in review["substitute"] if x["text"].strip() and x["text"] in text and x["text"] not in table]
        new_drops = {d for d in review["drop"] if d in ids} - drop
        rounds += 1
        if not new_subs and not new_drops:
            break
        table = store.add_symbols(new_subs)
        drop |= new_drops
        out = apply()
    log(f"review: clean after {rounds} round(s)")

    originals = [k for k in table if k in json.dumps(steps, ensure_ascii=False)]
    for i, s in enumerate(out):
        if s["step_id"] not in rephrase:
            continue
        src = s["message"]
        new = store.get("rephrase", h(src))
        if new is None:
            new = llm.ask(harness, REPHRASE_PROMPT + src, REPHRASE)["text"]
            missing = set(PLACEHOLDER.findall(src)) - set(PLACEHOLDER.findall(new))
            leaked = [o for o in originals if o in new]
            if missing or leaked:
                raise llm.LLMError(f"rephrase of step {s['step_id']} lost {sorted(missing)} or leaked {len(leaked)} strings")
            store.put("rephrase", h(src), new)
        out[i] = {**s, "message": new}
    log(f"rephrase: {len(rephrase)} user turns, drop: {len(drop)} turns")

    final = json.dumps(out, ensure_ascii=False)
    survived = [v for x in hits for v in x["secrets"] if v in final]
    if survived:
        raise llm.LLMError(f"{len(survived)} detected secrets survived scrubbing")

    result = {**traj, "steps": out}
    result["extra"] = {"vlt": {**traj["extra"]["vlt"], "tags": tags}}
    used = {k: v for k, v in table.items() if k in json.dumps(steps, ensure_ascii=False)}
    title = next((s["message"] for s in out if s["source"] == "user"), "")[:140] or "(untitled)"
    return {"traj": result, "table": used, "tags": tags, "title": title,
            "dropped": sorted(drop), "rephrased": sorted(rephrase),
            "step_hashes": [h(s) for s in out], "turns": sum(s["source"] == "user" for s in out)}
