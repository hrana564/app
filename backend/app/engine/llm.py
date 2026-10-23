from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.settings import settings

log = logging.getLogger("abm.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass
class ModelReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def is_configured() -> bool:
    return bool(settings.llm_base_url and settings.llm_model)


def _reply(message: Any) -> ModelReply:
    calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
    return ModelReply(content=message.content or "", tool_calls=calls)


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    timeout: float | None = None,
) -> ModelReply:
    # LLM_EXTRA_PARAMS is the deployment's own knob for its provider - the
    # reasoning budget, the thinking toggle, whatever that endpoint calls it.
    # It is optional because the parameter is not standard: an endpoint that has
    # never heard of it answers 400, and a rule that cannot be generated because
    # of a performance flag is worse than a slow one. So a rejection is retried
    # once without it, and the deployment is left working.
    #
    # `timeout` overrides the deployment's model timeout for one call. A call
    # that sits inside somebody's request - the description of a rule, say -
    # cannot afford the generous budget a generation needs, because a hung
    # model would hold that request open for as long as it cared to.
    if not is_configured():
        raise RuntimeError(
            "LLM not configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in backend/.env"
        )

    from openai import OpenAI

    client = OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "not-needed",
        timeout=float(timeout if timeout is not None else settings.llm_timeout_seconds),
    )

    extra = dict(settings.llm_extra_params or {})
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.1,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if extra:
        kwargs["extra_body"] = extra

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        if not extra or not _rejected_the_extra_params(exc):
            raise
        log.warning("the model endpoint rejected %s; retrying without it", extra)
        kwargs.pop("extra_body", None)
        response = client.chat.completions.create(**kwargs)

    return _reply(response.choices[0].message)


def complete(system: str, user: str, timeout: float | None = None) -> str:
    reply = chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout=timeout,
    )
    return (reply.content or "").strip()


def _rejected_the_extra_params(exc: Exception) -> bool:
    # Only then is it worth retrying without them. A rate limit or an auth
    # failure is not the parameter's fault, and retrying would hide it.
    text = str(exc).lower()
    if "400" not in text and "bad_request" not in text:
        return False
    keys = [k.lower() for k in (settings.llm_extra_params or {})]
    return any(k in text for k in keys) or "invalid params" in text


def strip_fences(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
