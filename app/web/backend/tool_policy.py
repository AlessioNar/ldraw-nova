"""Which tools a chat mode / permission setting exposes to the model."""
from __future__ import annotations

import editor_tools
import inventories
import subagents
from tools import TOOL_SCHEMAS

READ_TOOLS = {"list_files", "read_file", "view_image", "report_progress", "my_parts", "buildable_models"}
PARTS_TOOLS = {"my_parts", "buildable_models"}            # only when the chat has a parts steering mode



def available_tools(options: dict, chat_id: str | None = None) -> list[dict]:
    if options.get("mode") == "chat":
        return []
    tools = TOOL_SCHEMAS if inventories.parts_options(options)[0] != "off" else \
        [t for t in TOOL_SCHEMAS if t["function"]["name"] not in PARTS_TOOLS]
    if not subagents.enabled(options) or options.get("permissions") != "full":      # workers run tools without asking
        tools = [t for t in tools if t["function"]["name"] != "delegate"]
    if options.get("mode") == "plan" or options.get("permissions") == "read_only":
        return [t for t in tools if t["function"]["name"] in READ_TOOLS]
    # editor tools: agent mode only; editor_open always (with replace it swaps in a rebuilt plan), state/edit once the chat has a plan
    hidden = set() if editor_tools.has_plan(chat_id) else editor_tools.EDIT_TOOLS
    return [t for t in tools if t["function"]["name"] not in hidden]


def worker_tools(contract_tools, role_defaults, role: str, options: dict, chat_id: str | None = None) -> tuple[list[str], str | None]:
    """A worker's tool names and, if the chat's policy forbids the delegation, a refusal message.

    Contract tools intersected with the role default, then bounded by the chat: mode plan or permissions
    read_only leaves READ_TOOLS only and refuses a builder; the parts tools need a parts steering mode.
    Workers never get run_shell, publish_model or delegate (the contract validator already rejects them).
    """
    read_only = options.get("mode") == "plan" or options.get("permissions") == "read_only"
    if read_only and role == "builder":
        return [], "builder delegation refused: this chat is read-only (plan mode or read-only permissions), a builder would write files"
    tools = [t for t in contract_tools if t in role_defaults]
    if inventories.parts_options(options)[0] == "off":
        tools = [t for t in tools if t not in PARTS_TOOLS]
    if read_only:
        tools = [t for t in tools if t in READ_TOOLS]
    return tools, None
