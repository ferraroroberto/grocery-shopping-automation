"""Shared "call the hub LLM" helper (issue #95).

`inventory_extract.extract()` and `voice_command.parse_voice_items()` each
build an `Anthropic` client pointed at the local hub (`claude-local-calls` on
`:8000`, `api_key="local-dummy"`), call `messages.create()`, and concatenate
the response's text blocks into one string — near-verbatim in both. This
module factors that shared request/extraction shape into one function; the
system prompt, user text, and error translation stay with each caller.
"""

from __future__ import annotations

from anthropic import Anthropic


def call_hub_llm(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    user_text: str,
    max_tokens: int = 4096,
    timeout: float = 90,
) -> str:
    """Call the hub LLM (Anthropic-shape) and return its concatenated text.

    Raises `anthropic.APIError` on failure, untranslated — callers wrap it in
    their own domain error type with their own message.
    """
    client = Anthropic(api_key="local-dummy", base_url=base_url, timeout=timeout)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
