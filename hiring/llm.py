"""One function to ask an LLM for JSON, whichever LLM you use.

    complete_json(system, prompt, schema)  ->  dict matching `schema`

Pick the backend with the LLM_BACKEND environment variable (e.g. in .env):

  claude_cli (default)  Your Claude subscription, through Claude Code installed and
                        logged in on the machine running the site (`claude` command).
                        No API key. Uses your plan's usage limits; meant for this demo.
  api                   Anthropic API. Needs ANTHROPIC_API_KEY.
  ollama                A free local model through Ollama (http://localhost:11434).

Other settings:
  LLM_MODEL     model name (optional for claude_cli; default claude-sonnet-5-5 for api,
                llama3.1 for ollama)
  CLAUDE_BIN    path to the claude command (default: "claude")
  OLLAMA_URL    default http://localhost:11434
  LLM_TIMEOUT   seconds per call (default 300)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


def backend() -> str:
    return os.environ.get("LLM_BACKEND", "claude_cli").strip().lower()


def _timeout() -> int:
    return int(os.environ.get("LLM_TIMEOUT", "300"))


def _extract_json(text: str):
    """Accept a bare JSON object, or one wrapped in ```json fences / extra words."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise LLMError(f"The model did not return valid JSON: {text[:300]!r}")


# ---------------- backends ----------------
def _claude_cli(system: str, prompt: str, schema: dict) -> dict:
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p",
           "--output-format", "json",
           "--tools", "",                       # no tools: it only reads and answers
           "--no-session-persistence",
           "--strict-mcp-config",
           "--system-prompt", system,
           "--json-schema", json.dumps(schema)]
    if os.environ.get("LLM_MODEL"):
        cmd += ["--model", os.environ["LLM_MODEL"]]
    # run from an empty folder so no project settings or files are picked up
    with tempfile.TemporaryDirectory() as cwd:
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=_timeout(), cwd=cwd)
        except FileNotFoundError:
            raise LLMError("The `claude` command was not found. Install Claude Code and log in "
                           "with your subscription on this machine, or set CLAUDE_BIN.")
        except subprocess.TimeoutExpired:
            raise LLMError(f"Claude did not answer within {_timeout()} s.")
    if p.returncode != 0 and not p.stdout.strip():
        raise LLMError(f"claude exited with code {p.returncode}: {p.stderr.strip()[:500]}")
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"Unexpected output from claude: {p.stdout[:300]!r}")
    if out.get("is_error"):
        raise LLMError(f"Claude reported an error: {str(out.get('result'))[:500]}")
    if isinstance(out.get("structured_output"), dict):
        return out["structured_output"]
    return _extract_json(out.get("result", ""))


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code} from {url}: {e.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach {url}: {e.reason}")


def _api(system: str, prompt: str, schema: dict) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("LLM_BACKEND=api but ANTHROPIC_API_KEY is not set.")
    data = _post("https://api.anthropic.com/v1/messages", {
        "model": os.environ.get("LLM_MODEL", "claude-sonnet-5-5"),
        "max_tokens": 8000,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        # forcing a tool call is the reliable way to get JSON that matches the schema
        "tools": [{"name": "submit", "description": "Submit the answer.", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "submit"},
    }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
    for block in data.get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input", {})
    raise LLMError("The API response contained no answer.")


def _ollama(system: str, prompt: str, schema: dict) -> dict:
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    data = _post(url, {
        "model": os.environ.get("LLM_MODEL", "llama3.1"),
        "stream": False,
        "format": schema,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }, {})
    return _extract_json(data.get("message", {}).get("content", ""))


_BACKENDS = {"claude_cli": _claude_cli, "api": _api, "ollama": _ollama}


def complete_json(system: str, prompt: str, schema: dict, retries: int = 1) -> dict:
    """Ask the configured LLM; returns a dict with at least the schema's required keys."""
    fn = _BACKENDS.get(backend())
    if fn is None:
        raise LLMError(f"Unknown LLM_BACKEND {backend()!r} (use {', '.join(_BACKENDS)}).")
    last = None
    for _ in range(retries + 1):
        try:
            out = fn(system, prompt, schema)
            missing = [k for k in schema.get("required", []) if k not in out]
            if missing:
                raise LLMError(f"Answer is missing {missing}")
            return out
        except LLMError as e:
            last = e
    raise last


__all__ = ["complete_json", "LLMError", "backend"]
