# server/xml_tool_parser.py
#
# Qwen3.5 XML tool-call fallback parser. Shared by llm_client (Ollama-native)
# and openai_client (OpenAI /v1). Some Qwen3.5 builds emit tool calls as XML
# text instead of structured tool_calls; this recovers them.

from __future__ import annotations

import re
from typing import Any

# <function=name><parameter=key>value</parameter></function>  (optionally <tool_call>-wrapped)
_XML_FUNCTION_RE = re.compile(
    r"<function=(?P<name>[a-z_]+)>\s*(?P<params>(?:<parameter=[^>]+>[^<]*</parameter>\s*)*)</function>",
    re.DOTALL,
)
_XML_PARAM_RE = re.compile(
    r"<parameter=(?P<key>[a-z_]+)>(?P<value>[^<]*)</parameter>",
)


def _parse_xml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract tool calls from Qwen3.5 XML format fallback.

    Handles both bare <function=...> and wrapped <tool_call><function=...></tool_call>.
    """
    results: list[dict[str, Any]] = []
    for m in _XML_FUNCTION_RE.finditer(text):
        name = m.group("name")
        params_block = m.group("params")
        args: dict[str, Any] = {}
        for pm in _XML_PARAM_RE.finditer(params_block):
            key = pm.group("key")
            raw_value = pm.group("value").strip()
            if raw_value.isdigit():
                args[key] = int(raw_value)
            elif raw_value in ("true", "false"):
                args[key] = raw_value == "true"
            else:
                args[key] = raw_value
        results.append({"name": name, "args": args})
    return results
