"""Which tools a chat mode / permission setting exposes to the model."""
from __future__ import annotations

import inventories
import subagents
from tools import TOOL_SCHEMAS

READ_TOOLS = {"list_files", "read_file", "view_image", "report_progress", "my_parts", "buildable_models"}
PARTS_TOOLS = {"my_parts", "buildable_models"}            # only when the chat has a parts steering mode



def available_tools(options: dict) -> list[dict]:
    if options.get("mode") == "chat":
        return []
    tools = TOOL_SCHEMAS if inventories.parts_options(options)[0] != "off" else \
        [t for t in TOOL_SCHEMAS if t["function"]["name"] not in PARTS_TOOLS]
    if not subagents.enabled() or options.get("permissions") != "full":      # workers run tools without asking
        tools = [t for t in tools if t["function"]["name"] != "delegate_task"]
    if options.get("mode") == "plan" or options.get("permissions") == "read_only":
        return [t for t in tools if t["function"]["name"] in READ_TOOLS]
    return tools
