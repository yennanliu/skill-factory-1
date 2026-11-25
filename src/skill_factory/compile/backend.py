"""Two ways to get one structured JSON answer from Claude.

- ClaudeCliBackend (default): `claude -p --json-schema … --tools ""`, using Claude Code's own login.
- ApiBackend: the Anthropic Python SDK (needs ANTHROPIC_API_KEY or an `ant auth login` profile).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_MODEL = "claude-opus-5-5"


class BackendError(Exception):
    pass


@dataclass(frozen=True)
class Answer:
    data: dict[str, Any]
    cost_usd: float | None


class Backend(Protocol):
    name: str

    def generate(self, system: str, user: str, schema: dict[str, Any], model: str) -> Answer: ...


class ClaudeCliBackend:
    name = "claude-cli"

    def __init__(self, timeout_seconds: int = 900) -> None:
        self.timeout = timeout_seconds

    def command(self, system: str, user: str, schema: dict[str, Any], model: str) -> list[str]:
        return [
            "claude",
            "-p",
            user,
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(schema),
            "--output-format",
            "json",
            "--tools",
            "",  # a pure generation call: no file or shell access
            "--model",
            model,
            "--setting-sources",
            "",  # don't load the user's hooks, plugins or CLAUDE.md
            "--no-session-persistence",
            "--max-turns",
            "3",
        ]

    def generate(self, system: str, user: str, schema: dict[str, Any], model: str) -> Answer:
        if shutil.which("claude") is None:
            raise BackendError("claude CLI not found on PATH")
        try:
            proc = subprocess.run(
                self.command(system, user, schema, model),
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"claude -p timed out after {self.timeout}s") from e
        try:
            out = json.loads(proc.stdout)
        except ValueError as e:
            raise BackendError(
                f"claude -p returned no JSON (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
            ) from e
        if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
            raise BackendError(
                f"claude -p failed: {out.get('subtype')} {str(out.get('result'))[:500]}"
            )
        return Answer(out["structured_output"], out.get("total_cost_usd"))


class ApiBackend:
    name = "api"

    def generate(self, system: str, user: str, schema: dict[str, Any], model: str) -> Answer:
        import anthropic

        try:
            client = anthropic.Anthropic()
            # Streaming: a full SKILL.md plus eval drafts can be long.
            with client.beta.messages.stream(
                model=model,
                max_tokens=64000,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": schema},
                },
                # Re-run a policy decline on a fallback model inside the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise BackendError(
                "no Anthropic API credentials (set ANTHROPIC_API_KEY or run `ant auth login`), "
                "or use --backend claude-cli"
            ) from e
        except anthropic.RateLimitError as e:
            raise BackendError(f"rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            raise BackendError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise BackendError(f"could not reach the API: {e}") from e
        except anthropic.AnthropicError as e:  # e.g. no credentials found at all
            raise BackendError(
                f"{e} (set ANTHROPIC_API_KEY, run `ant auth login`, or use --backend claude-cli)"
            ) from e
        if message.stop_reason == "refusal":
            raise BackendError("the model declined to compile this spec")
        if message.stop_reason == "max_tokens":
            raise BackendError("output hit max_tokens; the spec asks for too much in one skill")
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            return Answer(json.loads(text), None)
        except ValueError as e:
            raise BackendError("structured output was not valid JSON") from e


def get_backend(name: str) -> Backend:
    if name == "claude-cli":
        return ClaudeCliBackend()
    if name == "api":
        return ApiBackend()
    raise ValueError(f"unknown backend {name!r} (claude-cli | api)")
