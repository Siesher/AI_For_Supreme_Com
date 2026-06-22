"""Tests for Qwen3.5 XML tool call fallback parser."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

from llm_client import _parse_xml_tool_calls


class TestParseXmlToolCalls:
    """Verify _parse_xml_tool_calls handles all known Qwen3.5 XML variants."""

    def test_single_tool_call_with_params(self) -> None:
        text = '<function=set_strategy><parameter=strategy>rush</parameter><parameter=reasoning>enemy is weak</parameter></function>'
        result = _parse_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "set_strategy"
        assert result[0]["args"]["strategy"] == "rush"
        assert result[0]["args"]["reasoning"] == "enemy is weak"

    def test_tool_call_wrapped_in_tool_call_tags(self) -> None:
        text = '<tool_call>\n<function=attack><parameter=unit_type>land</parameter><parameter=force_size>full</parameter></function>\n</tool_call>'
        result = _parse_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "attack"
        assert result[0]["args"]["unit_type"] == "land"
        assert result[0]["args"]["force_size"] == "full"

    def test_no_params(self) -> None:
        text = "<function=get_enemy_army></function>"
        result = _parse_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "get_enemy_army"
        assert result[0]["args"] == {}

    def test_multiple_tool_calls(self) -> None:
        text = (
            "<function=get_enemy_army></function>\n"
            "<function=get_threat_at><parameter=position>own_base</parameter></function>"
        )
        result = _parse_xml_tool_calls(text)
        assert len(result) == 2
        assert result[0]["name"] == "get_enemy_army"
        assert result[1]["name"] == "get_threat_at"
        assert result[1]["args"]["position"] == "own_base"

    def test_integer_param_cast(self) -> None:
        text = "<function=defend><parameter=radius>80</parameter></function>"
        result = _parse_xml_tool_calls(text)
        assert result[0]["args"]["radius"] == 80
        assert isinstance(result[0]["args"]["radius"], int)

    def test_boolean_param_cast(self) -> None:
        text = "<function=noop><parameter=reasoning>all good</parameter></function>"
        result = _parse_xml_tool_calls(text)
        assert result[0]["args"]["reasoning"] == "all good"

    def test_true_false_cast(self) -> None:
        # Edge case: boolean string values
        text = "<function=noop><parameter=reasoning>true</parameter></function>"
        result = _parse_xml_tool_calls(text)
        assert result[0]["args"]["reasoning"] is True

    def test_no_xml_returns_empty(self) -> None:
        text = "I think we should attack the enemy base with full force."
        result = _parse_xml_tool_calls(text)
        assert result == []

    def test_whitespace_in_params(self) -> None:
        text = (
            "<function=set_strategy>\n"
            "  <parameter=strategy>turtle</parameter>\n"
            "  <parameter=reasoning>need defense</parameter>\n"
            "</function>"
        )
        result = _parse_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["args"]["strategy"] == "turtle"
        assert result[0]["args"]["reasoning"] == "need defense"

    def test_mixed_text_and_xml(self) -> None:
        text = "Based on the situation, I will attack.\n<function=attack><parameter=unit_type>all</parameter></function>\nThis should work."
        result = _parse_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "attack"

    def test_chat_tool_with_special_chars(self) -> None:
        text = '<function=chat><parameter=message>Атакуем вместе!</parameter></function>'
        result = _parse_xml_tool_calls(text)
        assert len(result) == 1
        assert result[0]["name"] == "chat"
        assert result[0]["args"]["message"] == "Атакуем вместе!"

    def test_scout_direction(self) -> None:
        text = "<function=scout><parameter=direction>enemy_base</parameter></function>"
        result = _parse_xml_tool_calls(text)
        assert result[0]["name"] == "scout"
        assert result[0]["args"]["direction"] == "enemy_base"
