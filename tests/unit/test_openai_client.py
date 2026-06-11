"""Tests for the OpenAI-compatible inference client."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

import httpx
from openai_client import OpenAIClient, _to_openai_messages, _parse_openai_tool_calls


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _client():
    return OpenAIClient(
        {
            "llm": {
                "engine": "koboldcpp",
                "base_url": "http://localhost:5001/v1",
                "model_deep": "kobold",
                "model_fast": "kobold",
                "max_tokens": 64,
            },
            "bridge": {"timeout_s": 5},
        }
    )


def _run(coro):
    return asyncio.run(coro)


def test_parse_openai_tool_calls_json_string_args():
    raw = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "set_strategy",
                "arguments": '{"strategy": "rush", "reasoning": "weak enemy"}',
            },
        }
    ]
    out = _parse_openai_tool_calls(raw)
    assert out == [
        {
            "name": "set_strategy",
            "args": {"strategy": "rush", "reasoning": "weak enemy"},
        }
    ]


def test_parse_openai_tool_calls_dict_args():
    # lenient servers may already return a dict
    raw = [{"function": {"name": "defend", "arguments": {"radius": 80}}}]
    out = _parse_openai_tool_calls(raw)
    assert out == [{"name": "defend", "args": {"radius": 80}}]


def test_parse_openai_tool_calls_bad_json_becomes_empty_args():
    raw = [{"function": {"name": "scout", "arguments": "{not json"}}]
    out = _parse_openai_tool_calls(raw)
    assert out == [{"name": "scout", "args": {}}]


def test_to_openai_messages_converts_assistant_and_tool():
    msgs = [
        {"role": "system", "content": "/no_think"},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_enemy_army", "arguments": {}}}],
        },
        {"role": "tool", "content": '{"land": 5}'},
    ]
    out = _to_openai_messages(msgs)
    assert out[0]["role"] == "system" and out[1]["role"] == "user"
    asst = out[2]
    assert asst["tool_calls"][0]["type"] == "function"
    assert asst["tool_calls"][0]["function"]["name"] == "get_enemy_army"
    assert isinstance(
        asst["tool_calls"][0]["function"]["arguments"], str
    )  # JSON string
    call_id = asst["tool_calls"][0]["id"]
    assert (
        out[3]["role"] == "tool" and out[3]["tool_call_id"] == call_id
    )  # ids match in order


def test_query_returns_tool_calls(monkeypatch):
    client = _client()
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "attack",
                                "arguments": '{"unit_type": "land"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    async def fake_post(url, json=None):
        assert url == "http://localhost:5001/v1/chat/completions"
        assert json["model"] == "kobold"
        assert "tools" in json
        return _FakeResp(payload)

    monkeypatch.setattr(client._http_client, "post", fake_post)
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result["tool_calls"] == [{"name": "attack", "args": {"unit_type": "land"}}]
    assert result["_model_used"] == "kobold"
    assert "_latency_ms" in result


def test_query_xml_fallback(monkeypatch):
    client = _client()
    payload = {
        "choices": [
            {
                "message": {
                    "content": "<function=set_strategy><parameter=strategy>turtle</parameter></function>",
                    "tool_calls": [],
                }
            }
        ]
    }

    async def fake_post(url, json=None):
        return _FakeResp(payload)

    monkeypatch.setattr(client._http_client, "post", fake_post)
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result["tool_calls"] == [
        {"name": "set_strategy", "args": {"strategy": "turtle"}}
    ]


def test_query_plain_text_becomes_noop(monkeypatch):
    client = _client()
    payload = {
        "choices": [{"message": {"content": "I will just wait.", "tool_calls": []}}]
    }

    async def fake_post(url, json=None):
        return _FakeResp(payload)

    monkeypatch.setattr(client._http_client, "post", fake_post)
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result["tool_calls"][0]["name"] == "noop"


def test_query_timeout_returns_none(monkeypatch):
    client = _client()

    async def fake_post(url, json=None):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(client._http_client, "post", fake_post)
    # deep == fast == "kobold", so the retry also times out -> None
    result = _run(client.query([{"role": "user", "content": "go"}]))
    _run(client.close())
    assert result is None
