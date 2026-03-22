# T041: Config loader — server/config.py
#
# Loads config.json and validates against the BotConfiguration schema.
# Exposes load_config(path) -> BotConfig dataclass.

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Dataclasses matching BotConfiguration in data-model.md
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    model_deep:   str   = "qwen3.5:9b"
    model_fast:   str   = "qwen3.5:4b"
    base_url:     str   = "http://localhost:11434"
    temperature:  float = 0.3
    max_tokens:   int   = 256


@dataclass
class GameConfig:
    game_path:     str = r"C:\Program Files (x86)\Supreme Commander"
    faf_mods_path: str = r"%APPDATA%\FAForever\mods"
    faction:       str = "UEF"


@dataclass
class BotCfg:
    difficulty:          str  = "normal"
    playstyle:           str  = "balanced"
    chat_enabled:        bool = True
    chat_language:       str  = "auto"
    poll_interval_fast_s: int = 20
    poll_interval_deep_s: int = 60


@dataclass
class BridgeConfig:
    pipe_name:  str = r"\\.\pipe\supcom_llm_bridge"
    timeout_s:  int = 10


@dataclass
class DebugConfig:
    overlay_enabled: bool = True
    log_enabled:     bool = True
    log_path:        str  = r"%ProgramData%\FAForever\logs\llm_ai_decisions.log"


@dataclass
class BotConfig:
    llm:    LLMConfig   = field(default_factory=LLMConfig)
    game:   GameConfig  = field(default_factory=GameConfig)
    bot:    BotCfg      = field(default_factory=BotCfg)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    debug:  DebugConfig  = field(default_factory=DebugConfig)
    version: str         = "1.0.0"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_DIFFICULTIES = {"easy", "normal", "hard"}
_VALID_PLAYSTYLES   = {"rush", "balanced", "turtle", "air", "naval"}
_VALID_LANGUAGES    = {"auto", "ru", "en"}


def _validate(cfg: BotConfig) -> list[str]:
    errors: list[str] = []

    if cfg.bot.difficulty not in _VALID_DIFFICULTIES:
        errors.append(f"bot.difficulty must be one of {_VALID_DIFFICULTIES}")
    if cfg.bot.playstyle not in _VALID_PLAYSTYLES:
        errors.append(f"bot.playstyle must be one of {_VALID_PLAYSTYLES}")
    if cfg.bot.chat_language not in _VALID_LANGUAGES:
        errors.append(f"bot.chat_language must be one of {_VALID_LANGUAGES}")
    if not (1 <= cfg.bot.poll_interval_fast_s <= 300):
        errors.append("bot.poll_interval_fast_s must be between 1 and 300")
    if not (1 <= cfg.bot.poll_interval_deep_s <= 600):
        errors.append("bot.poll_interval_deep_s must be between 1 and 600")
    if not (0.0 <= cfg.llm.temperature <= 2.0):
        errors.append("llm.temperature must be between 0.0 and 2.0")
    if not (64 <= cfg.llm.max_tokens <= 2048):
        errors.append("llm.max_tokens must be between 64 and 2048")
    if not (1 <= cfg.bridge.timeout_s <= 120):
        errors.append("bridge.timeout_s must be between 1 and 120")

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> BotConfig:
    """
    Load and validate config.json.
    Raises ValueError if required fields are invalid.
    Returns BotConfig with defaults for any optional missing fields.
    """
    config_path = Path(os.path.expandvars(str(path)))
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open(encoding="utf-8") as f:
        raw = json.load(f)

    # Build dataclass hierarchy
    llm_raw    = raw.get("llm", {})
    game_raw   = raw.get("game", {})
    bot_raw    = raw.get("bot", {})
    bridge_raw = raw.get("bridge", {})
    debug_raw  = raw.get("debug", {})

    cfg = BotConfig(
        llm=LLMConfig(
            model_deep  = llm_raw.get("model_deep",  "qwen3.5:9b"),
            model_fast  = llm_raw.get("model_fast",  "qwen3.5:4b"),
            base_url    = llm_raw.get("base_url",    "http://localhost:11434"),
            temperature = float(llm_raw.get("temperature", 0.3)),
            max_tokens  = int(llm_raw.get("max_tokens", 256)),
        ),
        game=GameConfig(
            game_path     = game_raw.get("game_path",     r"C:\Program Files (x86)\Supreme Commander"),
            faf_mods_path = game_raw.get("faf_mods_path", r"%APPDATA%\FAForever\mods"),
            faction       = game_raw.get("faction",       "UEF"),
        ),
        bot=BotCfg(
            difficulty           = bot_raw.get("difficulty",          "normal"),
            playstyle            = bot_raw.get("playstyle",           "balanced"),
            chat_enabled         = bool(bot_raw.get("chat_enabled",   True)),
            chat_language        = bot_raw.get("chat_language",       "auto"),
            poll_interval_fast_s = int(bot_raw.get("poll_interval_fast_s", 20)),
            poll_interval_deep_s = int(bot_raw.get("poll_interval_deep_s", 60)),
        ),
        bridge=BridgeConfig(
            pipe_name = bridge_raw.get("pipe_name", r"\\.\pipe\supcom_llm_bridge"),
            timeout_s = int(bridge_raw.get("timeout_s", 10)),
        ),
        debug=DebugConfig(
            overlay_enabled = bool(debug_raw.get("overlay_enabled", True)),
            log_enabled     = bool(debug_raw.get("log_enabled",     True)),
            log_path        = debug_raw.get("log_path", r"%ProgramData%\FAForever\logs\llm_ai_decisions.log"),
        ),
        version=raw.get("version", "1.0.0"),
    )

    errors = _validate(cfg)
    if errors:
        raise ValueError("Config validation errors:\n" + "\n".join(f"  - {e}" for e in errors))

    return cfg
