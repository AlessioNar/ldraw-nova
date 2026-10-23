"""LiteLLM request normalization and bounded history shared by chat and probes."""
import copy
import hashlib
import json
from collections import OrderedDict

import litellm

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
    if llm_config.unsupported(entry):
        raise ValueError(llm_config.UNSUPPORTED_MESSAGE)
    params = llm_config.resolve_params(entry)
    model = params["model"]
    if model.startswith("openai/gpt-") and not params.get("api_base"):
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
    elif options.get("effort") and style == "mistral":
        # LiteLLM drops reasoning_effort for Mistral, so send it as a raw body field.
        params.pop("reasoning_effort", None)
        params["extra_body"] = {**params.get("extra_body", {}), "reasoning_effort": options["effort"]}
    elif options.get("effort") and style == "reasoning_effort":
        params["reasoning_effort"] = options["effort"]
    elif options.get("effort"):
        # The provider has no effort field ; sending one 400s.
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


_TEXT_KEYS = {"type", "format", "index", "id", "text"}


def coalesce_reasoning_details(details: list) -> list:
    """Merge runs of streamed `reasoning.text` deltas into one block per run.

    Only consecutive plain-text blocks that agree on type, format, index and id
    (and carry no other field, e.g. a signature) are joined, by concatenating the
    text in received order. Encrypted, summary and any other block, and any block
    with an unexpected shape, is kept as is and ends the run; nothing is reordered.
    """
    out: list = []
    run = None  # the last output block while it is still a mergeable text delta
    for detail in details:
        mergeable = (isinstance(detail, dict) and detail.get("type") == "reasoning.text"
                     and set(detail) <= _TEXT_KEYS and isinstance(detail.get("text"), str))
        if mergeable and run is not None and all(run.get(k) == detail.get(k) for k in ("type", "format", "index", "id")) \
                and set(run) == set(detail):
            run["text"] += detail["text"]
            continue
        if mergeable:
            detail = dict(detail)
            run = detail
        else:
            run = None
        out.append(detail)
    return out


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


_TOKEN_CACHE: "OrderedDict[tuple, int]" = OrderedDict()
_TOKEN_CACHE_MAX = 8192
_REPLY_PRIMING = 3  # litellm.token_counter adds this once per call, not per message


def reasoning_text(message: dict) -> str:
    """Text of the OpenRouter reasoning blocks the message carries.

    litellm.token_counter skips list-valued keys it does not know, so the
    reasoning that is re-sent every step would otherwise be invisible to the budget.
    """
    details = message.get("reasoning_details") or message.get("_reasoning_details")
    if not isinstance(details, list):
        return ""
    return "".join(d[key] for d in details if isinstance(d, dict)
                   for key in ("text", "summary") if isinstance(d.get(key), str))


def _digest(*parts: str) -> bytes:
    h = hashlib.blake2b(digest_size=16)
    for part in parts:
        h.update(part.encode("utf-8", "surrogatepass"))
        h.update(b"\0")
    return h.digest()


def _message_tokens(model: str, message: dict) -> int:
    """Tokens of one message as litellm counts it in a list, plus its reasoning text.

    Counts are additive per message, so they are cached by content: only the
    messages that are new since the previous step are tokenized. The reasoning
    text is joined and hashed on its own because it can be megabytes of tiny blocks.
    """
    text = reasoning_text(message)
    rest = {k: v for k, v in message.items() if k not in ("reasoning_details", "_reasoning_details")}
    key = (model, _digest(json.dumps(rest, default=str), text))
    cached = _TOKEN_CACHE.get(key)
    if cached is not None:
        _TOKEN_CACHE.move_to_end(key)
        return cached
    tokens = litellm.token_counter(model=model, messages=[rest]) - _REPLY_PRIMING
    if text:
        tokens += litellm.token_counter(model=model, text=text)
    _TOKEN_CACHE[key] = tokens
    if len(_TOKEN_CACHE) > _TOKEN_CACHE_MAX:
        _TOKEN_CACHE.popitem(last=False)
    return tokens


def _strip_turn_start(messages: list[dict]) -> list[dict]:
    """Copy for sending: own message dicts (and system blocks), shared payloads."""
    out = []
    for m in messages:
        m = {k: v for k, v in m.items() if k != "_turn_start"}
        if isinstance(m.get("content"), list) and m.get("role") == "system":
            m["content"] = [dict(b) for b in m["content"]]
        out.append(m)
    return out


def bounded_history(messages: list[dict], model: str, budget: int | None) -> tuple[list[dict], int]:
    """Trim old turns, then completed tool rounds in a long build.

    Keep the user's task and the last two complete tool rounds. Full history
    stays on disk; the system prompt carries the build's latest NOTES.md.
    Message token counts are cached across steps and include reasoning blocks;
    the history is never deep-copied (only the system turn, which gets the trim note).
    """
    if not budget:
        return _strip_turn_start(messages), 0
    limit = budget - 4096
    try:
        sizes = [_message_tokens(model, m) for m in messages]
        total = _REPLY_PRIMING + sum(sizes)
    except Exception:
        sizes = None
        total = len(json.dumps(messages).encode())  # conservative for unknown tokenizers
    if total <= limit:
        return _strip_turn_start(messages), 0
    # Only the system turn is edited below; payloads such as reasoning blocks are shared.
    result = [dict(m) for m in messages]
    result[0] = copy.deepcopy(messages[0])
    removed = 0

    def count():
        if sizes is not None:
            return _REPLY_PRIMING + sum(sizes)
        return len(json.dumps(result).encode())

    while count() > limit:
        starts = [i for i, m in enumerate(result) if m.get("_turn_start", m["role"] == "user")]
        if len(starts) < 2:
            rounds = [i for i, m in enumerate(result) if m.get("tool_calls")]
            if len(rounds) <= 2:
                raise ValueError("The latest turn exceeds the context budget. Increase it or start a new chat.")
            begin, end = rounds[0], rounds[1]
            removed += end - begin
            result[begin:end] = []
            if sizes is not None:
                sizes[begin:end] = []
            note = "\nEarlier completed tool rounds were omitted to fit context. Consult the current NOTES.md and saved output reports; do not assume omitted checks passed."
            if note not in _system_tail(result[0]):
                _append_system(result[0], note)
                if sizes is not None:
                    sizes[0] = _message_tokens(model, result[0])
            continue
        end = starts[1]
        removed += end - 1
        result = [result[0], *result[end:]]
        if sizes is not None:
            sizes = [sizes[0], *sizes[end:]]
        # Do not leave results whose calls were removed (e.g. image feedback).
        while len(result) > 1 and result[1]["role"] == "tool":
            result.pop(1)
            if sizes is not None:
                sizes.pop(1)
    for message in result:
        message.pop("_turn_start", None)
    return result, removed
