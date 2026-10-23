"""Which tools a chat mode / permission setting exposes to the model."""
from __future__ import annotations

import editor_tools
import inventories
from tools import TOOL_SCHEMAS

READ_TOOLS = {"list_files", "read_file", "view_image", "report_progress", "my_parts", "buildable_models"}
PARTS_TOOLS = {"my_parts", "buildable_models"}            # only when the chat has a parts steering mode



def available_tools(options: dict, chat_id: str | None = None) -> list[dict]:
    if options.get("mode") == "chat":
        return []
    tools = TOOL_SCHEMAS if inventories.parts_options(options)[0] != "off" else \
        [t for t in TOOL_SCHEMAS if t["function"]["name"] not in PARTS_TOOLS]
    if options.get("mode") == "plan" or options.get("permissions") == "read_only":
        return [t for t in tools if t["function"]["name"] in READ_TOOLS]
    # editor tools: agent mode only; state/edit once the chat has an editor plan, editor_open until then
    hidden = editor_tools.OPEN_TOOLS if editor_tools.has_plan(chat_id) else editor_tools.EDIT_TOOLS
    return [t for t in tools if t["function"]["name"] not in hidden]
