"""LiteLLM request normalization and bounded history shared by chat and probes."""
import copy
import json

import litellm

import browser_auth
import llm_config
import providers
import model_catalog


def preserve_openrouter_reasoning_chunks(stream, model: str) -> None:
    """Work around LiteLLM 1.102.1 dropping reasoning-details-only chunks.

    Its generic stream wrapper doesn't count this OpenRouter field as content.
    Extend the predicate on this request only, without changing the payload,
    synthesizing text, or patching other providers. Covered by an HTTP wire test.
    """
    from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper

    if not providers.of(model).reasoning_details or not isinstance(stream, CustomStreamWrapper):
        return
    original = stream.is_chunk_non_empty

    def has_content(completion_obj, model_response, response_obj):
        upstream = response_obj.get("original_chunk")
        if any(getattr(getattr(c, "delta", None), "reasoning_details", None)
               for c in getattr(upstream, "choices", [])):
            return True
        return original(completion_obj, model_response, response_obj)

    stream.is_chunk_non_empty = has_content


async def params_for(entry: dict, options: dict) -> dict:
    params = llm_config.resolve_params(entry)
    model = params["model"]
    if entry.get("auth_mode") == "browser":
        if model.startswith(("openai/", "chatgpt/")):
            await browser_auth.openai_ready()
            # Subscription inference only has a Responses endpoint. New model
            # names may not yet be in LiteLLM's catalog, so force its bridge
            # instead of letting it fall back to /chat/completions.
            name = model.split("/")[-1]
            # Unknown models otherwise trigger LiteLLM's fake streaming path,
            # which removes stream=true; ChatGPT requires it on every request.
            litellm.register_model({f"chatgpt/{name}": {
                "litellm_provider": "chatgpt", "mode": "responses",
                "supports_native_streaming": True,
            }})
            params = {"model": "chatgpt/responses/" + name}
        else:
            raise ValueError("Claude browser sessions use the Claude agent runtime")
    elif model.startswith("openai/gpt-") and not params.get("api_base"):
        # GPT-6 function calling requires Responses; LiteLLM bridges the stream.
        params["model"] = model.replace("openai/", "openai/responses/", 1)
    style = providers.of(model).effort
    if options.get("effort") and style == "openrouter":
        # Use the gateway's native field. LiteLLM's older reasoning_effort
        # mapping silently changes max to xhigh and misses newer models.
        params.pop("reasoning_effort", None)
        params["extra_body"] = {**params.get("extra_body", {}),
                                "reasoning": {"effort": options["effort"]}}
    elif options.get("effort") and style == "anthropic":
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": options["effort"]}
    elif options.get("effort") and style == "reasoning_effort":
        params["reasoning_effort"] = options["effort"]
    elif options.get("effort"):
        # The provider has no effort field (e.g. Mistral); sending one 400s.
        params.pop("reasoning_effort", None)
    elif style == "openrouter" and model_catalog.entry_profile(entry).get("reasoning") is True:
        # Some providers expose only an on/off switch, not effort levels. A
        # builder still needs reasoning (e.g. Grok 4.20 disables it by default).
        extra = params.setdefault("extra_body", {})
        extra.setdefault("reasoning", {"enabled": True})
    # App-owned fields cannot be overridden with arbitrary settings parameters.
    for key in ("messages", "stream", "tools", "tool_choice", "num_retries"):
        params.pop(key, None)
    return params


def _system_tail(message: dict) -> str:
    """Text of the system turn's last block.

    The content is a list of blocks when prompt caching has put a cache
    breakpoint after the stable prefix, and a plain string otherwise.
    """
    content = message["content"]
    return content if isinstance(content, str) else content[-1]["text"]


def _append_system(message: dict, note: str) -> None:
    """Append to the system turn, after any cache breakpoint."""
    content = message["content"]
    if isinstance(content, str):
        message["content"] = content + note
    else:
        content[-1]["text"] += note


def bounded_history(messages: list[dict], model: str, budget: int | None) -> tuple[list[dict], int]:
    """Trim old turns, then completed tool rounds in a long build.

    Keep the user's task and the last two complete tool rounds. Full history
    stays on disk; the system prompt carries the build's latest NOTES.md.
    """
    result = copy.deepcopy(messages)
    removed = 0

    def count():
        try:
            return litellm.token_counter(model=model, messages=result)
        except Exception:
            return len(json.dumps(result).encode())  # conservative for unknown tokenizers

    while budget and count() > budget - 4096:
        starts = [i for i, m in enumerate(result) if m.get("_turn_start", m["role"] == "user")]
        if len(starts) < 2:
            rounds = [i for i, m in enumerate(result) if m.get("tool_calls")]
            if len(rounds) <= 2:
                raise ValueError("The latest turn exceeds the context budget. Increase it or start a new chat.")
            begin, end = rounds[0], rounds[1]
            removed += end - begin
            result[begin:end] = []
            note = "\nEarlier completed tool rounds were omitted to fit context. Consult the current NOTES.md and saved output reports; do not assume omitted checks passed."
            if note not in _system_tail(result[0]):
                _append_system(result[0], note)
            continue
        end = starts[1]
        removed += end - 1
        result = [result[0], *result[end:]]
        # Do not leave results whose calls were removed (e.g. image feedback).
        while len(result) > 1 and result[1]["role"] == "tool":
            result.pop(1)
    for message in result:
        message.pop("_turn_start", None)
    return result, removed
