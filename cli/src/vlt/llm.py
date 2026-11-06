"""Headless calls to the user's own subscription. Each harness's traces go back only to the vendor that already saw them.

Neither call persists a session, so vlt never reads its own work.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


class LLMError(RuntimeError):
    pass


def ask(harness: str, prompt: str, schema: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        if harness == "claude_code":
            cmd = ["claude", "-p", "--model", "haiku", "--no-session-persistence", "--output-format", "json",
                   "--json-schema", json.dumps(schema), "--tools", "", "--strict-mcp-config", "--setting-sources", ""]
            out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tmp)
            if out.returncode != 0:
                raise LLMError(out.stderr.strip() or out.stdout[-500:])
            result = json.loads(out.stdout)
            if result.get("is_error") or "structured_output" not in result:
                raise LLMError(f"claude returned no structured output: {result.get('subtype')}")
            return result["structured_output"]
        schema_file, answer = Path(tmp, "schema.json"), Path(tmp, "answer.json")
        schema_file.write_text(json.dumps(schema))
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
               "--output-schema", str(schema_file), "-o", str(answer), "-"]
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tmp)
        if out.returncode != 0 or not answer.exists():
            raise LLMError(out.stderr.strip()[-500:])
        return json.loads(answer.read_text())
