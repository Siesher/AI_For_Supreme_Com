# Tool definitions for LLM agent — server/tools.py
#
# Each tool maps to a game action the LLM can invoke.
# Ollama /api/chat passes these as the `tools` parameter.
# Also provides validation and deduplication utilities.

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Names of observation tools (read-only, resolved via pipe round-trip)
OBSERVATION_TOOL_NAMES: set[str] = {
    "get_enemy_army",
    "get_threat_at",
    "get_mass_points",
    "get_my_factories",
    "get_map_control",
}

OBSERVATION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_enemy_army",
            "description": (
                "Query the current enemy army composition. Returns unit counts by type. "
                "Use before deciding whether to attack or defend."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_threat_at",
            "description": (
                "Get threat level at a specific map position. "
                "Use to check safety of an area before expanding or attacking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "position": {
                        "type": "string",
                        "enum": [
                            "own_base",
                            "enemy_base",
                            "north",
                            "south",
                            "east",
                            "west",
                            "center",
                        ],
                        "description": "Map position to check threat at",
                    },
                },
                "required": ["position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mass_points",
            "description": (
                "Get the status of mass extraction points on the map. "
                "Returns how many are controlled, uncontrolled, or contested."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_factories",
            "description": (
                "Get the status of your factories — count by tech level, which are idle vs building."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_map_control",
            "description": (
                "Get overall map control: percentage of map you control and zone breakdown."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

GAME_TOOLS: list[dict] = OBSERVATION_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "attack",
            "description": (
                "Send idle combat units from the army pool to attack the enemy. "
                "Use when you have enough military units and see an opportunity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_type": {
                        "type": "string",
                        "enum": ["land", "air", "all"],
                        "description": "Type of units to send",
                    },
                    "force_size": {
                        "type": "string",
                        "enum": ["small", "medium", "full"],
                        "description": "small=5-10, medium=10-20, full=all idle units",
                    },
                },
                "required": ["unit_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "defend",
            "description": (
                "Rally idle combat units to patrol and defend the base area. "
                "Use when enemy forces are approaching or base is under threat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "radius": {
                        "type": "integer",
                        "description": "Defense perimeter radius (50=tight, 80=normal, 120=wide)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scout",
            "description": (
                "Send a fast unit to scout an area of the map. "
                "Use to gain intel on enemy positions or find uncontrolled mass points."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["north", "south", "east", "west", "enemy_base"],
                        "description": "Direction to scout",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_strategy",
            "description": (
                "Set overall strategic posture. Affects base AI priorities for building and platoon formation. "
                "land_rush=early land aggression, balanced=default, turtle=heavy defense, tech_up=invest in T2/T3, air=focus air."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "enum": ["land_rush", "balanced", "turtle", "tech_up", "air"],
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation why (max 100 chars)",
                    },
                },
                "required": ["strategy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_units",
            "description": (
                "Request factories to prioritize building specific unit types. "
                "Use to shift army composition toward land, air, or navy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "priority": {
                        "type": "string",
                        "enum": ["land", "air", "navy", "engineers", "anti_air"],
                        "description": "What to prioritize building",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "normal", "high"],
                        "description": "How urgently to shift production",
                    },
                },
                "required": ["priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reclaim",
            "description": (
                "Send idle engineers to reclaim mass from wreckage on the battlefield. "
                "Use when mass income is low and there are wrecks available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "enum": ["near_base", "battlefield", "expansion"],
                        "description": "Where to send engineers to reclaim",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chat",
            "description": (
                "Send a chat message to allies or all players. "
                "Use to communicate strategy, respond to player questions, or taunt opponents. "
                "Keep messages short and natural. Use Russian if the player writes in Russian."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Chat message text (max 100 characters)",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "noop",
            "description": (
                "Do nothing this cycle. Use when the current situation is fine and no action is needed. "
                "The base AI continues operating autonomously."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation why no action needed",
                    },
                },
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Validation & deduplication
# ---------------------------------------------------------------------------

# Build lookup: tool_name → {properties, required, enums}
_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {}


def _build_tool_schemas() -> None:
    """Parse GAME_TOOLS into a fast lookup for validation."""
    for tool_def in GAME_TOOLS:
        fn = tool_def.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters", {})
        properties = params.get("properties", {})
        required = set(params.get("required", []))
        enums: dict[str, list[str]] = {}
        for pname, pschema in properties.items():
            if "enum" in pschema:
                enums[pname] = pschema["enum"]
        _TOOL_SCHEMAS[name] = {
            "properties": set(properties.keys()),
            "required": required,
            "enums": enums,
        }


_build_tool_schemas()

ALL_TOOL_NAMES: set[str] = set(_TOOL_SCHEMAS.keys())


def validate_tool_call(tc: dict[str, Any]) -> tuple[bool, str]:
    """Validate a single tool call dict {"name": ..., "args": ...}.

    Returns (is_valid, error_message).
    """
    name = tc.get("name", "")
    args = tc.get("args", {})

    if name not in _TOOL_SCHEMAS:
        return False, f"unknown tool: {name}"

    schema = _TOOL_SCHEMAS[name]

    # Check required params
    for req in schema["required"]:
        if req not in args:
            return False, f"{name}: missing required param '{req}'"

    # Check enum values
    for param_name, allowed in schema["enums"].items():
        if param_name in args and str(args[param_name]) not in allowed:
            return False, (
                f"{name}: invalid value '{args[param_name]}' for '{param_name}', "
                f"expected one of {allowed}"
            )

    return True, ""


def validate_and_filter(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate tool calls and drop invalid ones. Log warnings for dropped calls."""
    valid: list[dict[str, Any]] = []
    for tc in tool_calls:
        ok, err = validate_tool_call(tc)
        if ok:
            valid.append(tc)
        else:
            log.warning("Dropping invalid tool call: %s", err)
    return valid


def deduplicate(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate tool calls within a single iteration.

    Two calls are duplicates if they have the same name and args.
    Keeps the first occurrence.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for tc in tool_calls:
        args = tc.get("args", {})
        key = tc["name"] + ":" + ",".join(f"{k}={v}" for k, v in sorted(args.items()))
        if key not in seen:
            seen.add(key)
            unique.append(tc)
        else:
            log.info("Deduplicated tool call: %s", key)
    return unique
