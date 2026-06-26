# server/xml_tool_parser.py
#
# Free-text tool-call fallback parser. Shared by llm_client (Ollama-native),
# openai_client (OpenAI /v1) and hf_llm_client. When a small quantized model
# bypasses native tool-calling and emits the call as plain assistant text, this
# module recovers the intended call(s) instead of dropping them.
#
# Two public entry points:
#   _parse_xml_tool_calls(text)      -> the legacy Qwen3.5 <function=...> form
#                                       (kept stable; re-exported and tested).
#   parse_fallback_tool_calls(text)  -> the comprehensive recovery parser:
#                                       name(...) calls, name\n{json}, bare/flat
#                                       JSON objects & arrays, ```fences```,
#                                       <tool_call>{json}</tool_call> leaks and
#                                       the <function=...> form, with best-effort
#                                       repair of truncated JSON. Returns the
#                                       recovered calls or [] (the noop fallback
#                                       is the caller's policy, not ours).

from __future__ import annotations

import ast
import json
import re
from typing import Any, Iterator, Optional

from tools import ALL_TOOL_NAMES  # authoritative tool-name set

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
            args[key] = _cast_scalar(pm.group("value").strip())
        results.append({"name": name, "args": args})
    return results


# ---------------------------------------------------------------------------
# Comprehensive free-text recovery
# ---------------------------------------------------------------------------

# Tool names longest-first so the alternation prefers e.g. get_threat_at over a
# hypothetical shorter prefix. Names are [a-z_]+ so they are regex-safe.
_NAME_ALT = "|".join(sorted(ALL_TOOL_NAMES, key=len, reverse=True))

# Line-anchored Python-style call: optional self./tools./functions. prefix, a
# tool name, then '('. MULTILINE so "^" matches each line start; this is what
# stops "we could attack(), but..." (mid-prose) from firing.
_PYCALL_RE = re.compile(
    r"^[ \t]*(?:self\.|functions?\.|tools?\.)?(?P<name>"
    + _NAME_ALT
    + r")[ \t]*\((?P<rest>.*)$",
    re.MULTILINE,
)

# Noise stripped before scanning.
_THINK_CLOSED_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL)  # unclosed <think> to EOF
_TOOLCALL_TAG_RE = re.compile(r"</?tool_call>")
_FENCE_LINE_RE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")

# Keys a model improvises to name the tool / hold its args.
_NAME_KEYS = ("name", "tool", "function", "action")
_ARGS_KEYS = ("arguments", "args", "parameters", "params")


def _cast_scalar(value: Any) -> Any:
    """Best-effort scalar coercion for string values (ints, bools, quoted strings)."""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
        return s
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _balance_json(s: str) -> str:
    """Close any open string/brackets so a truncated JSON blob can be parsed."""
    stack: list[str] = []
    in_str = False
    esc = False
    quote = '"'
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
    out = s
    if in_str:
        out += quote
    while stack:
        out += stack.pop()
    return out


def _loads_lenient(blob: str) -> Optional[Any]:
    """Parse a JSON-ish blob, repairing truncation and Python-dict single quotes."""
    blob = blob.strip()
    if not blob:
        return None
    candidates = [blob]
    repaired = _balance_json(blob)
    if repaired != blob:
        candidates.append(repaired)
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:  # noqa: BLE001 — fall through to literal_eval
            pass
        try:
            val = ast.literal_eval(cand)
            if isinstance(val, (dict, list)):
                return val
        except Exception:  # noqa: BLE001 — not a Python literal either
            pass
    return None


def _scan_balanced(text: str, start: int) -> tuple[str, int]:
    """Return (substring, end_index) for the bracket group opening at ``start``.

    Stops when nesting returns to zero; on truncation returns to end of text.
    """
    stack: list[str] = []
    in_str = False
    esc = False
    quote = '"'
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        else:
            if ch in ("'", '"'):
                in_str = True
                quote = ch
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
                if not stack:
                    return text[start : i + 1], i + 1
        i += 1
    return text[start:], n


def _calls_from_json_value(val: Any) -> list[dict[str, Any]]:
    """Normalize a parsed JSON dict/list into tool calls (alias + flat-arg aware)."""
    calls: list[dict[str, Any]] = []
    items = val if isinstance(val, list) else [val]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = None
        for k in _NAME_KEYS:
            v = item.get(k)
            if isinstance(v, str) and v in ALL_TOOL_NAMES:
                name = v
                break
        if name is None:
            continue
        args: Optional[dict[str, Any]] = None
        for k in _ARGS_KEYS:
            if k not in item:
                continue
            v = item[k]
            if isinstance(v, dict):
                args = dict(v)
                break
            if isinstance(v, str):
                parsed = _loads_lenient(v)
                if isinstance(parsed, dict):
                    args = parsed
                    break
        if args is None:
            # Flat object: the params sit alongside the name key.
            args = {
                kk: vv
                for kk, vv in item.items()
                if kk not in _NAME_KEYS and kk not in _ARGS_KEYS
            }
        calls.append({"name": name, "args": args})
    return calls


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split on ``sep`` only at bracket/quote depth zero."""
    parts: list[str] = []
    depth = 0
    in_str = False
    esc = False
    quote = '"'
    buf: list[str] = []
    for ch in s:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _take_call_args(rest: str) -> str:
    """From the text after '(', capture up to the matching ')' (or EOL on truncation)."""
    depth = 0
    in_str = False
    esc = False
    quote = '"'
    out: list[str] = []
    for ch in rest:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            out.append(ch)
        elif ch in "([{":
            depth += 1
            out.append(ch)
        elif ch in ")]}":
            if depth == 0:
                break  # the ')' that closes the call
            depth -= 1
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out).strip()


def _parse_call_args(argstr: str) -> dict[str, Any]:
    """Parse the inside of a call: kwargs (key=value) or a single JSON object."""
    argstr = argstr.strip()
    if not argstr:
        return {}
    if argstr.startswith("{"):
        val = _loads_lenient(argstr)
        if isinstance(val, dict):
            return val
    args: dict[str, Any] = {}
    for piece in _split_top_level(argstr, ","):
        if "=" not in piece:
            continue
        key, _, raw = piece.partition("=")
        key = key.strip()
        if key:
            args[key] = _cast_scalar(raw.strip())
    return args


def _strip_noise(text: str) -> str:
    """Remove <think> leaks, <tool_call> tags and code-fence markers."""
    t = _THINK_CLOSED_RE.sub(" ", text)
    t = _THINK_OPEN_RE.sub(" ", t)
    t = _TOOLCALL_TAG_RE.sub("\n", t)
    return "\n".join("" if _FENCE_LINE_RE.match(ln) else ln for ln in t.split("\n"))


def _iter_name_newline(text: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """A tool name alone on a line, followed by a JSON args object."""
    offset = 0
    for line in text.split("\n"):
        if line.strip() in ALL_TOOL_NAMES:
            rest_start = offset + len(line) + 1
            rest = text[rest_start:]
            j = re.match(r"\s*", rest).end()  # type: ignore[union-attr]
            if j < len(rest) and rest[j] == "{":
                blob, _ = _scan_balanced(rest, j)
                val = _loads_lenient(blob)
                if isinstance(val, dict):
                    yield offset, {"name": line.strip(), "args": val}
        offset += len(line) + 1


def _iter_pycalls(text: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Line-anchored ``name(...)`` calls (kwargs or a JSON object)."""
    for m in _PYCALL_RE.finditer(text):
        argstr = _take_call_args(m.group("rest"))
        yield (
            m.start("name"),
            {"name": m.group("name"), "args": _parse_call_args(argstr)},
        )


def _iter_json_calls(text: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Self-describing JSON objects/arrays anywhere in the text."""
    i = 0
    n = len(text)
    while i < n:
        if text[i] in "{[":
            blob, end = _scan_balanced(text, i)
            val = _loads_lenient(blob)
            if val is not None:
                for call in _calls_from_json_value(val):
                    yield i, call
            i = max(end, i + 1)
        else:
            i += 1


def _iter_function_xml(text: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """The legacy <function=name><parameter=...>...</function> form, position-aware."""
    for m in _XML_FUNCTION_RE.finditer(text):
        args: dict[str, Any] = {}
        for pm in _XML_PARAM_RE.finditer(m.group("params")):
            args[pm.group("key")] = _cast_scalar(pm.group("value").strip())
        yield m.start(), {"name": m.group("name"), "args": args}


def parse_fallback_tool_calls(text: Optional[str]) -> list[dict[str, Any]]:
    """Recover tool calls from a model's free-text output.

    Returns the recovered calls in source order, de-duplicated, filtered to known
    tools. Returns [] when nothing parses — the caller decides whether that means
    a noop. See module docstring for the recovery contract.
    """
    if not text:
        return []
    cleaned = _strip_noise(text)

    candidates: list[tuple[int, dict[str, Any]]] = []
    candidates.extend(_iter_name_newline(cleaned))
    candidates.extend(_iter_pycalls(cleaned))
    candidates.extend(_iter_json_calls(cleaned))
    candidates.extend(_iter_function_xml(cleaned))
    candidates.sort(key=lambda pc: pc[0])

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, call in candidates:
        if call["name"] not in ALL_TOOL_NAMES:
            continue
        key = (
            call["name"]
            + "|"
            + json.dumps(call["args"], sort_keys=True, ensure_ascii=False)
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(call)
    return out
