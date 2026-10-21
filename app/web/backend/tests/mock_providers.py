"""Local stand-ins for provider HTTP APIs, to see the exact request bodies LiteLLM puts on the wire.

    MockAnthropic    POST /v1/messages          (JSON or SSE, as the Anthropic Messages API)
    MockChat         POST /v1/chat/completions  (JSON or SSE; OpenAI, OpenRouter and compatible gateways)

Both are scripted: each request pops the next reply from `script` (a dict, see
`reply`), and every request is recorded in `.requests`. MockAnthropic also checks
the body against the documented Messages API rules that matter for prompt caching
and answers 400 like the real API when they are broken, so a request the real
provider would refuse fails the turn here as well.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply(text: str = "", tool_calls: list | None = None, usage: dict | None = None, **extra) -> dict:
    """One scripted provider turn: `tool_calls` are (id, name, arguments-dict) triples,
    `usage` takes input/output/cache_read/cache_write; `extra` is provider specific
    (reasoning_details=[[delta list per chunk], ...] for MockChat)."""
    return {"text": text, "tool_calls": tool_calls or [], "usage": usage or {}, **extra}


class _Server:
    def __init__(self, script: list[dict] | None = None):
        self.script = list(script or [])
        self.requests: list[dict] = []        # {"path", "headers", "body"}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                try:
                    status, headers, payload = outer.respond(self.path, body, len(outer.requests) - 1)
                except Exception as exc:  # noqa: BLE001 - a broken script must be loud in the test
                    status, headers, payload = 500, {"Content-Type": "application/json"}, json.dumps(
                        {"error": {"message": f"mock failure: {exc!r}"}}).encode()
                self.send_response(status)
                for key, value in {**headers, "Content-Length": str(len(payload))}.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port

    def __enter__(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def bodies(self) -> list[dict]:
        return [r["body"] for r in self.requests]

    def next_reply(self, index: int) -> dict:
        if index >= len(self.script):
            raise IndexError(f"request {index + 1} but the script has {len(self.script)} replies")
        return self.script[index]

    def respond(self, path: str, body: dict, index: int) -> tuple[int, dict, bytes]:
        raise NotImplementedError


def _sse(events: list[tuple[str | None, dict]]) -> bytes:
    return "".join((f"event: {name}\n" if name else "") + f"data: {json.dumps(data)}\n\n" for name, data in events).encode()


def _json(status: int, data: dict) -> tuple[int, dict, bytes]:
    return status, {"Content-Type": "application/json"}, json.dumps(data).encode()


# --- Anthropic Messages API -------------------------------------------------

ANTHROPIC_MAX_BREAKPOINTS = 4
_CACHEABLE_BLOCKS = {"text", "image", "document", "tool_use", "tool_result", "server_tool_use", "web_search_tool_result"}


def anthropic_cache_markers(body: dict) -> list[str]:
    """Where `cache_control` appears in a Messages API body: 'system[0]', 'messages[3].content[0]', 'tools[1]'."""
    found = []
    system = body.get("system")
    for i, block in enumerate(system if isinstance(system, list) else []):
        if "cache_control" in block:
            found.append(f"system[{i}]")
    for i, tool in enumerate(body.get("tools") or []):
        if "cache_control" in tool:
            found.append(f"tools[{i}]")
    for i, message in enumerate(body.get("messages", [])):
        if "cache_control" in message:
            found.append(f"messages[{i}]")
        content = message.get("content")
        for j, block in enumerate(content if isinstance(content, list) else []):
            if isinstance(block, dict) and "cache_control" in block:
                found.append(f"messages[{i}].content[{j}]")
            inner = block.get("content") if isinstance(block, dict) and block.get("type") == "tool_result" else None
            for k, nested in enumerate(inner if isinstance(inner, list) else []):
                if isinstance(nested, dict) and "cache_control" in nested:
                    found.append(f"messages[{i}].content[{j}].content[{k}]")
    return found


def anthropic_problems(body: dict) -> list[str]:
    """Violations of the documented Messages API that a real request would be rejected for."""
    bad = []
    for key in ("model", "max_tokens", "messages"):
        if key not in body:
            bad.append(f"missing {key}")
    messages = body.get("messages") or []
    if not messages:
        bad.append("messages: at least one message is required")
    markers = anthropic_cache_markers(body)
    if len(markers) > ANTHROPIC_MAX_BREAKPOINTS:
        bad.append(f"A maximum of {ANTHROPIC_MAX_BREAKPOINTS} blocks with cache_control may be provided. Found {len(markers)}: {markers}")
    for where in markers:
        if where.startswith("messages") and "." not in where:
            bad.append(f"{where}.cache_control: Extra inputs are not permitted (cache_control belongs on content blocks)")

    def check_block(where, block):
        if not isinstance(block, dict) or "type" not in block:
            bad.append(f"{where}: block needs a type")
            return
        control = block.get("cache_control")
        if control is not None and control.get("type") != "ephemeral":
            bad.append(f"{where}.cache_control.type: must be 'ephemeral'")
        if control is not None and block["type"] not in _CACHEABLE_BLOCKS:
            bad.append(f"{where}: cache_control is not allowed on a {block['type']} block")
        if block["type"] == "text" and not block.get("text", "").strip():
            bad.append(f"{where}: text content blocks must be non-empty")     # also with cache_control

    system = body.get("system")
    for i, block in enumerate(system if isinstance(system, list) else []):
        check_block(f"system[{i}]", block)
    pending: set[str] = set()                      # tool_use ids the next user turn has to answer
    for i, message in enumerate(messages):
        role, content = message.get("role"), message.get("content")
        if role not in ("user", "assistant"):
            bad.append(f"messages[{i}].role: must be user or assistant, got {role!r}")
        if isinstance(content, str):
            if not content.strip():
                bad.append(f"messages[{i}]: content must be non-empty")
            blocks = []
        else:
            blocks = content or []
            if not blocks:
                bad.append(f"messages[{i}]: content must be non-empty")
        for j, block in enumerate(blocks):
            check_block(f"messages[{i}].content[{j}]", block)
        results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        if pending and {b.get("tool_use_id") for b in results} != pending:
            bad.append(f"messages[{i}]: tool_use ids {sorted(pending)} need matching tool_result blocks in the next user message")
        if results and not pending:
            bad.append(f"messages[{i}]: tool_result without a tool_use in the previous message")
        if results and any(b.get("type") != "tool_result" for b in blocks[:len(results)]):
            bad.append(f"messages[{i}]: tool_result blocks must come first in the content")
        pending = {b["id"] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"} if role == "assistant" else set()
    if messages and messages[0].get("role") != "user":
        bad.append("messages: first message must use the user role")
    return bad


def same_role_runs(body: dict) -> list[tuple[int, int]]:
    """(index, index+1) pairs of consecutive same-role messages. The real API merges them rather than refusing."""
    roles = [m.get("role") for m in body.get("messages", [])]
    return [(i, i + 1) for i in range(len(roles) - 1) if roles[i] == roles[i + 1]]


class MockAnthropic(_Server):
    def respond(self, path, body, index):
        if path != "/v1/messages":
            return _json(404, {"type": "error", "error": {"type": "not_found_error", "message": path}})
        problems = anthropic_problems(body)
        if problems:
            return _json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "; ".join(problems)}})
        scripted = self.next_reply(index)
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        for ours, theirs in (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
                             ("cache_write", "cache_creation_input_tokens")):
            usage[theirs] = scripted["usage"].get(ours, usage[theirs])
        content = ([{"type": "text", "text": scripted["text"]}] if scripted["text"] else []) + [
            {"type": "tool_use", "id": cid, "name": name, "input": args} for cid, name, args in scripted["tool_calls"]]
        stop = "tool_use" if scripted["tool_calls"] else "end_turn"
        message = {"id": f"msg_{index}", "type": "message", "role": "assistant", "model": body["model"],
                   "content": content, "stop_reason": stop, "stop_sequence": None, "usage": usage}
        if not body.get("stream"):
            return _json(200, message)
        events = [("message_start", {"type": "message_start", "message": {**message, "content": [], "stop_reason": None,
                                                                          "usage": {**usage, "output_tokens": 1}}})]
        for i, block in enumerate(content):
            if block["type"] == "text":
                events += [("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}),
                           *[("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": piece}})
                             for piece in _pieces(block["text"])]]
            else:
                events += [("content_block_start", {"type": "content_block_start", "index": i,
                                                    "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}),
                           *[("content_block_delta", {"type": "content_block_delta", "index": i,
                                                      "delta": {"type": "input_json_delta", "partial_json": piece}})
                             for piece in _pieces(json.dumps(block["input"]))]]
            events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                      "usage": {"output_tokens": usage["output_tokens"]}}),
                   ("message_stop", {"type": "message_stop"})]
        return 200, {"Content-Type": "text/event-stream"}, _sse(events)


def _pieces(text: str, size: int = 7) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


# --- OpenAI-compatible chat completions (OpenAI, OpenRouter, ...) -----------

class MockChat(_Server):
    """`reply(..., reasoning_details=[[block, ...], ...])` streams one delta per inner list."""

    def respond(self, path, body, index):
        if path != "/v1/chat/completions":
            return _json(404, {"error": {"message": path}})
        problems = [f"messages[{i}]: cache_control is not accepted here" for i, m in enumerate(body["messages"])
                    if "cache_control" in json.dumps(m)] if self.reject_cache_control else []
        if problems:
            return _json(400, {"error": {"message": "; ".join(problems)}})
        scripted = self.next_reply(index)
        u = scripted["usage"]
        usage = {"prompt_tokens": u.get("input", 10), "completion_tokens": u.get("output", 5),
                 "total_tokens": u.get("input", 10) + u.get("output", 5),
                 "prompt_tokens_details": {"cached_tokens": u.get("cache_read", 0), "cache_write_tokens": u.get("cache_write", 0)}}
        calls = [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                 for cid, name, args in scripted["tool_calls"]]
        finish = "tool_calls" if calls else "stop"
        base = {"id": f"chatcmpl-{index}", "created": 1, "model": body["model"]}
        if not body.get("stream"):
            message = {"role": "assistant", "content": scripted["text"] or None,
                       **({"tool_calls": calls} if calls else {})}
            if scripted.get("reasoning_details"):
                message["reasoning_details"] = [d for chunk in scripted["reasoning_details"] for d in chunk]
            return _json(200, {**base, "object": "chat.completion", "usage": usage,
                               "choices": [{"index": 0, "message": message, "finish_reason": finish}]})

        def chunk(delta, finish_reason=None, **top):
            return (None, {**base, "object": "chat.completion.chunk", **top,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})

        events = [chunk({"role": "assistant", "content": ""})]
        for details in scripted.get("reasoning_details") or []:
            events.append(chunk({"reasoning_details": details}))
        events += [chunk({"content": piece}) for piece in _pieces(scripted["text"])] if scripted["text"] else []
        for i, call in enumerate(calls):
            events.append(chunk({"tool_calls": [{"index": i, "id": call["id"], "type": "function",
                                                 "function": {"name": call["function"]["name"], "arguments": ""}}]}))
            events += [chunk({"tool_calls": [{"index": i, "function": {"arguments": piece}}]})
                       for piece in _pieces(call["function"]["arguments"])]
        events.append(chunk({}, finish))
        events.append((None, {**base, "object": "chat.completion.chunk", "choices": [], "usage": usage}))
        return 200, {"Content-Type": "text/event-stream"}, _sse(events) + b"data: [DONE]\n\n"

    reject_cache_control = False


# --- OpenAI Responses API (what LiteLLM uses for openai/gpt-6-* and friends) --

class MockResponses(_Server):
    def respond(self, path, body, index):
        if path != "/v1/responses":
            return _json(404, {"error": {"message": path}})
        scripted = self.next_reply(index)
        u = scripted["usage"]
        usage = {"input_tokens": u.get("input", 10), "output_tokens": u.get("output", 5),
                 "total_tokens": u.get("input", 10) + u.get("output", 5),
                 "input_tokens_details": {"cached_tokens": u.get("cache_read", 0)},
                 "output_tokens_details": {"reasoning_tokens": 0}}
        items = []
        if scripted["text"]:
            items.append({"type": "message", "id": f"msg_{index}", "status": "completed", "role": "assistant",
                          "content": [{"type": "output_text", "text": scripted["text"], "annotations": []}]})
        for cid, name, args in scripted["tool_calls"]:
            items.append({"type": "function_call", "id": f"fc_{cid}", "call_id": cid, "name": name,
                          "arguments": json.dumps(args), "status": "completed"})
        response = {"id": f"resp_{index}", "object": "response", "created_at": 1, "status": "completed",
                    "model": body["model"], "output": items, "usage": usage, "tools": [], "parallel_tool_calls": True}
        if not body.get("stream"):
            return _json(200, response)
        seq = iter(range(1000))
        ev = lambda name, **data: (name, {"type": name, "sequence_number": next(seq), **data})  # noqa: E731
        events = [ev("response.created", response={**response, "status": "in_progress", "output": [], "usage": None})]
        for i, item in enumerate(items):
            if item["type"] == "message":
                part = item["content"][0]
                events += [ev("response.output_item.added", output_index=i, item={**item, "status": "in_progress", "content": []}),
                           ev("response.content_part.added", item_id=item["id"], output_index=i, content_index=0,
                              part={**part, "text": ""}),
                           *[ev("response.output_text.delta", item_id=item["id"], output_index=i, content_index=0, delta=piece)
                             for piece in _pieces(part["text"])],
                           ev("response.output_text.done", item_id=item["id"], output_index=i, content_index=0, text=part["text"]),
                           ev("response.content_part.done", item_id=item["id"], output_index=i, content_index=0, part=part)]
            else:
                events += [ev("response.output_item.added", output_index=i, item={**item, "arguments": "", "status": "in_progress"}),
                           *[ev("response.function_call_arguments.delta", item_id=item["id"], output_index=i, delta=piece)
                             for piece in _pieces(item["arguments"])],
                           ev("response.function_call_arguments.done", item_id=item["id"], output_index=i, arguments=item["arguments"])]
            events.append(ev("response.output_item.done", output_index=i, item=item))
        events.append(ev("response.completed", response=response))
        return 200, {"Content-Type": "text/event-stream"}, _sse(events)


# --- running the real turn loop against a mock ------------------------------

def run_chat(store, entry_params: dict, text: str, options: dict | None = None, followups: list[str] = ()) -> tuple[str, list]:
    """Create a chat and run real turns (agent.start_turn -> turn.run_turn -> LiteLLM -> HTTP) for `text`
    and each follow-up message. Returns (chat_id, emitted events)."""
    import asyncio
    import agent
    import llm_config
    import run_state

    entry = llm_config.create({"model_name": "wire", "litellm_params": {"api_key": "sk-dummy", **entry_params},
                               "capabilities": {"tools": True, "vision": True}})
    chat_id = store.create_chat()["id"]
    events: list = []
    real_emit = run_state.Run.emit

    def emit(self, event, data):
        events.append((event, data))
        real_emit(self, event, data)

    async def go():
        run_state.Run.emit = emit
        try:
            for message in [text, *followups]:
                await agent.start_turn(store, chat_id, message, entry["id"], {"permissions": "full", **(options or {})})
                await run_state._runs[chat_id].task
        finally:
            run_state.Run.emit = real_emit

    asyncio.run(go())
    return chat_id, events
