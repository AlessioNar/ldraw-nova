"""Which tools a chat mode / permission setting exposes to the model."""
from __future__ import annotations

from tools import TOOL_SCHEMAS

READ_TOOLS = {"list_files", "read_file", "view_image", "report_progress"}



def available_tools(options: dict) -> list[dict]:
    if options.get("mode") == "chat":
        return []
    if options.get("mode") == "plan" or options.get("permissions") == "read_only":
        return [t for t in TOOL_SCHEMAS if t["function"]["name"] in READ_TOOLS]
    return TOOL_SCHEMAS

