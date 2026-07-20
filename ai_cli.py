# ai_cli.py
"""Configurable AI CLI runner.

Drives whichever coding-agent CLI the user has installed — `claude`, `codex`,
or `opencode` — behind one interface. The pipeline calls `run_text()` /
`run_json()` and never cares which backend is active.

Design choices that make this robust across very different CLIs:

- We do NOT rely on each CLI's own file-reading tools (they differ and need
  varying permission flags). Instead the pipeline reads files itself and
  injects their contents into the prompt as context. Every CLI is asked to do
  exactly one thing: read the prompt on stdin/args and print a text answer.
- JSON steps tolerate any preamble/reasoning a CLI might emit: we extract the
  first balanced JSON value from stdout rather than assuming the whole of
  stdout is clean JSON.
"""
import json
import os
import subprocess

# ── backend registry ──────────────────────────────────────
# Each entry knows how to turn a prompt string into an argv list that runs the
# CLI non-interactively and prints a plain-text answer to stdout.
#
# `argv(prompt)` -> list[str]. The prompt is always passed as a single argv
# element (never interpolated into a shell string) so no escaping/injection is
# possible regardless of prompt content.

def _claude_argv(prompt: str) -> list[str]:
    # -p / --print runs headless; text output format keeps stdout clean.
    return ["claude", "-p", prompt, "--output-format", "text"]


def _codex_argv(prompt: str) -> list[str]:
    # `codex exec` is the non-interactive mode. Bypass the sandbox approval
    # prompt (we only ever read local files / print) so it never blocks.
    return [
        "codex", "exec",
        "--skip-git-repo-check",
        "-c", "sandbox_mode=danger-full-access",
        prompt,
    ]


def _opencode_argv(prompt: str) -> list[str]:
    # `opencode run <message>` prints the assistant's final message to stdout.
    return ["opencode", "run", prompt]


BACKENDS = {
    "claude": _claude_argv,
    "codex": _codex_argv,
    "opencode": _opencode_argv,
}


def active_backend() -> str:
    """Which CLI to use, from $AI_CLI (default: claude)."""
    name = os.environ.get("AI_CLI", "claude").strip().lower()
    if name not in BACKENDS:
        raise RuntimeError(
            f"Unknown AI_CLI={name!r}. Choose one of: {', '.join(BACKENDS)}"
        )
    return name


# ── runner ─────────────────────────────────────────────────
def run_text(prompt: str, context: str = "", timeout: int = 300) -> str:
    """Run the active CLI on `prompt` (+ optional context) and return stdout.

    `context` is appended to the prompt — use it to inject file contents or
    JSON data the model needs, so we don't depend on the CLI's file tools.
    """
    backend = active_backend()
    full_prompt = f"{prompt}\n{context}" if context else prompt
    argv = BACKENDS[backend](full_prompt)

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"The {backend!r} CLI is not installed or not on PATH."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{backend!r} timed out after {timeout}s.")

    if result.returncode != 0:
        err = (result.stderr or "").strip()[:500]
        raise RuntimeError(f"{backend!r} failed (exit {result.returncode}): {err}")

    return (result.stdout or "").strip()


def run_json(prompt: str, context: str = "", timeout: int = 300):
    """Run a prompt that must return JSON and parse it.

    Robust to CLIs that wrap the answer in prose or markdown fences: we pull
    the first balanced JSON value out of stdout.
    """
    raw = run_text(prompt, context, timeout=timeout)
    if not raw:
        raise RuntimeError("AI CLI returned empty output.")
    parsed = _extract_json(raw)
    if parsed is None:
        raise RuntimeError(f"Could not find JSON in AI output:\n---\n{raw[:400]}")
    return parsed


# ── json extraction ────────────────────────────────────────
def _extract_json(text: str):
    """Return the first balanced JSON object/array found in `text`, or None.

    Handles: raw JSON, ```json fenced blocks, and JSON preceded/followed by
    prose. Scans for the first '{' or '[' and walks forward tracking string
    state and nesting depth until the matching close.
    """
    # Fast path: the whole string is valid JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip a leading ```json / ``` fence if present.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        try:
            return json.loads(stripped.strip())
        except json.JSONDecodeError:
            text = stripped

    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        candidate = _match_balanced(text, i)
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _match_balanced(text: str, start: int):
    """From an opening bracket at `start`, return the substring through its
    matching close bracket, respecting strings/escapes. None if unbalanced."""
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
