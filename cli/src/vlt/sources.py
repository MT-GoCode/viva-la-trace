"""Find local Claude Code and Codex sessions and convert them to ATIF v1.8 trajectories.

vlt's own LLM calls run with --no-session-persistence / --ephemeral, so they never appear here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Harness = Literal["claude_code", "codex"]
CLAUDE_DIR = Path.home() / ".claude" / "projects"
CODEX_DIR = Path.home() / ".codex" / "sessions"
VLT_VERSION = "0.1.0"


@dataclass(frozen=True)
class Session:
    id: str
    harness: Harness
    path: Path
    project: str
    mtime: float
    bytes: int


def _lines(path: Path):
    for line in path.open(encoding="utf-8"):
        if line.strip():
            yield json.loads(line)


def _claude_session(path: Path) -> Session:
    project = next((d["cwd"] for d in _lines(path) if d.get("cwd")), "")
    st = path.stat()
    return Session(path.stem, "claude_code", path, project, st.st_mtime, st.st_size)


def _codex_session(path: Path) -> Session:
    meta = next(_lines(path))["payload"]
    st = path.stat()
    return Session(meta["id"], "codex", path, meta.get("cwd", ""), st.st_mtime, st.st_size)


def list_sessions() -> list[Session]:
    claude = [_claude_session(p) for p in CLAUDE_DIR.glob("*/*.jsonl")]
    codex = [_codex_session(p) for p in CODEX_DIR.glob("**/rollout-*.jsonl")]
    return sorted(claude + codex, key=lambda s: s.mtime, reverse=True)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") in ("text", "input_text", "output_text"))


def _claude_steps(path: Path) -> tuple[list[dict], str | None, str]:
    steps: list[dict] = []
    by_call: dict[str, dict] = {}
    model, version, last_msg_id = None, "", None
    for d in _lines(path):
        if d.get("type") not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
            continue
        version = d.get("version", version)
        msg = d["message"]
        content = msg["content"]
        if d["type"] == "user":
            results = [b for b in content if b.get("type") == "tool_result"] if isinstance(content, list) else []
            for r in results:
                step = by_call.get(r["tool_use_id"])
                if step is not None:
                    step.setdefault("observation", {"results": []})["results"].append(
                        {"source_call_id": r["tool_use_id"], "content": _text(r.get("content") or "")})
            text = _text(content)
            # slash-command wrappers and their local output are harness plumbing, not user turns
            if text and not results and not text.lstrip().startswith(("<command-", "<local-command-")):
                steps.append({"source": "user", "message": text, "timestamp": d.get("timestamp")})
                last_msg_id = None
            continue
        model = msg.get("model") or model
        if msg.get("id") != last_msg_id:
            steps.append({"source": "agent", "message": "", "timestamp": d.get("timestamp"), "model_name": msg.get("model")})
            last_msg_id = msg.get("id")
        step = steps[-1]
        for b in content:
            if b["type"] == "text":
                step["message"] += b["text"]
            elif b["type"] == "thinking" and b.get("thinking"):
                step["reasoning_content"] = (step.get("reasoning_content", "") + b["thinking"])
            elif b["type"] == "tool_use":
                step.setdefault("tool_calls", []).append(
                    {"tool_call_id": b["id"], "function_name": b["name"], "arguments": b["input"]})
                by_call[b["id"]] = step
    return steps, model, version


def _codex_steps(path: Path) -> tuple[list[dict], str | None, str]:
    steps: list[dict] = []
    by_call: dict[str, dict] = {}
    model, version = None, ""

    def agent_step(ts):
        if not steps or steps[-1]["source"] != "agent" or steps[-1].get("observation"):
            steps.append({"source": "agent", "message": "", "timestamp": ts, "model_name": model})
        return steps[-1]

    for d in _lines(path):
        p, ts = d.get("payload", {}), d.get("timestamp")
        if d["type"] == "session_meta":
            version = p.get("cli_version", "")
        elif d["type"] == "turn_context":
            model = p.get("model", model)
        if d["type"] != "response_item":
            continue
        kind = p["type"]
        if kind == "message" and p["role"] == "user":
            text = _text(p["content"])
            if not text.lstrip().startswith(("<environment_context>", "# AGENTS.md", "<user_instructions>")):
                steps.append({"source": "user", "message": text, "timestamp": ts})
        elif kind == "message" and p["role"] == "assistant":
            agent_step(ts)["message"] += _text(p["content"])
        elif kind == "reasoning":
            summary = "\n".join(s.get("text", "") for s in p.get("summary") or [])
            if summary:
                s = agent_step(ts)
                s["reasoning_content"] = s.get("reasoning_content", "") + summary
        elif kind in ("function_call", "custom_tool_call"):
            raw = p.get("arguments", p.get("input", ""))
            try:
                args = json.loads(raw) if kind == "function_call" else {"input": raw}
            except json.JSONDecodeError:
                args = {"input": raw}
            s = agent_step(ts)
            s.setdefault("tool_calls", []).append({"tool_call_id": p["call_id"], "function_name": p["name"], "arguments": args})
            by_call[p["call_id"]] = s
        elif kind in ("function_call_output", "custom_tool_call_output"):
            s = by_call.get(p["call_id"])
            if s is not None:
                out = p["output"] if isinstance(p["output"], str) else json.dumps(p["output"])
                s.setdefault("observation", {"results": []})["results"].append({"source_call_id": p["call_id"], "content": out})
    return steps, model, version


def load(session: Session) -> dict:
    """ATIF v1.8 trajectory. Encrypted reasoning, signatures, paths and usernames are never copied."""
    steps, model, version = (_claude_steps if session.harness == "claude_code" else _codex_steps)(session.path)
    steps = [s for s in steps if s["source"] == "user" or s["message"] or s.get("tool_calls") or s.get("reasoning_content")]
    for i, s in enumerate(steps, 1):
        s["step_id"] = i
    return {
        "schema_version": "ATIF-v1.8",
        "agent": {"name": "claude-code" if session.harness == "claude_code" else "codex", "version": version, "model_name": model},
        "steps": steps,
        "extra": {"vlt": {"harness": session.harness, "vlt_version": VLT_VERSION}},
    }
