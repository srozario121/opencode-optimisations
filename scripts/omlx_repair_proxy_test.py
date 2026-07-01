"""Unit tests for the omlx tool-call repair proxy (item 30 Cat-1).

Covers the pure repair functions: eos-strip, text/native tool-call parsing,
arg parsing, no-think injection, and SSE re-emit. The HTTP handler is not
exercised here (it needs a live upstream); these guard the repair regexes that
recover omlx's gemma4 output defects.
"""
import json

import omlx_repair_proxy as p


def test_strip_specials_removes_gemma_turn_tokens() -> None:
    assert p._strip_specials("done<eos>") == "done"
    assert p._strip_specials("a<end_of_turn>b<eos>") == "ab"
    assert p._strip_specials("clean") == "clean"
    assert p._strip_specials(None) is None


def test_parse_kwargs_quote_styles() -> None:
    assert p._parse_kwargs('pattern="x", include="y"') == {"pattern": "x", "include": "y"}
    assert p._parse_kwargs("pattern='x' include='y'") == {"pattern": "x", "include": "y"}
    # Gemma native string delimiter, colon-separated
    assert p._parse_kwargs('pattern:<|"|>coupon<|"|>,include:<|"|>store.py<|"|>') == {
        "pattern": "coupon",
        "include": "store.py",
    }


def test_find_native_tool_calls() -> None:
    content = 'before <|tool_call>call:grep{pattern:<|"|>coupon<|"|>}<tool_call|> after'
    calls = p._find_native_tool_calls(content)
    assert calls == [("grep", {"pattern": "coupon"})]


def test_find_text_tool_calls_codestyle_and_kwargs() -> None:
    names = ["grep", "read"]
    assert p._find_text_tool_calls('print(grep(pattern="x", include="y"))', names) == [
        ("grep", {"pattern": "x", "include": "y"})
    ]
    assert p._find_text_tool_calls('grep pattern="x" include="y"', names) == [
        ("grep", {"pattern": "x", "include": "y"})
    ]


def test_find_text_tool_calls_native_takes_precedence() -> None:
    # Native spelling present alongside code-ish noise -> native wins, no tool_names needed.
    content = '<|tool_call>call:read{filePath:<|"|>store.py<|"|>}<tool_call|>'
    assert p._find_text_tool_calls(content, []) == [("read", {"filePath": "store.py"})]


def test_tool_names_extracts_function_names() -> None:
    req = {"tools": [
        {"function": {"name": "grep"}},
        {"function": {"name": "read"}},
        {"type": "function"},  # malformed, skipped
    ]}
    assert p._tool_names(req) == ["grep", "read"]


def test_repair_strips_eos_from_plain_content() -> None:
    data = {"choices": [{"message": {"content": "the answer<eos>"}, "finish_reason": "stop"}]}
    out, repaired = p._repair(data, [])
    assert repaired is True
    assert out["choices"][0]["message"]["content"] == "the answer"


def test_repair_structures_codestyle_tool_call() -> None:
    data = {"choices": [{
        "message": {"content": 'print(grep(pattern="coupon", include="store.py"))'},
        "finish_reason": "stop",
    }]}
    out, repaired = p._repair(data, ["grep", "read"])
    assert repaired is True
    msg = out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert len(msg["tool_calls"]) == 1
    fn = msg["tool_calls"][0]["function"]
    assert fn["name"] == "grep"
    assert json.loads(fn["arguments"]) == {"pattern": "coupon", "include": "store.py"}


def test_repair_structures_native_tool_call() -> None:
    data = {"choices": [{
        "message": {"content": '<|tool_call>call:read{filePath:<|"|>store.py<|"|>}<tool_call|>'},
        "finish_reason": "stop",
    }]}
    out, _ = p._repair(data, ["read"])
    msg = out["choices"][0]["message"]
    assert msg["tool_calls"][0]["function"]["name"] == "read"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"filePath": "store.py"}


def test_repair_leaves_structured_tool_calls_untouched() -> None:
    tcs = [{"id": "x", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]
    data = {"choices": [
        {"message": {"content": "", "tool_calls": tcs}, "finish_reason": "tool_calls"},
    ]}
    out, repaired = p._repair(data, ["grep"])
    assert repaired is False
    assert out["choices"][0]["message"]["tool_calls"] is tcs


def test_repair_disabled_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(p, "REPAIR", False)
    data = {"choices": [{"message": {"content": "x<eos>"}, "finish_reason": "stop"}]}
    out, repaired = p._repair(data, [])
    assert repaired is False
    assert out["choices"][0]["message"]["content"] == "x<eos>"


def test_strip_chunk_eos_strips_delta_content() -> None:
    chunk = {"choices": [{"delta": {"content": "answer<eos>"}}]}
    assert p._strip_chunk_eos(chunk) is True
    assert chunk["choices"][0]["delta"]["content"] == "answer"


def test_strip_chunk_eos_ignores_non_content_deltas() -> None:
    # role/tool_calls deltas have no string content — must be left alone.
    chunk = {"choices": [{"delta": {"role": "assistant"}}]}
    assert p._strip_chunk_eos(chunk) is False
    chunk2 = {"choices": [{"delta": {"content": "clean text"}}]}
    assert p._strip_chunk_eos(chunk2) is False


def test_inject_no_think_sets_flag_and_preserves_existing() -> None:
    req = {"chat_template_kwargs": {"foo": 1}}
    p._inject_no_think(req)
    assert req["chat_template_kwargs"] == {"foo": 1, "enable_thinking": False}
    req2: dict = {}
    p._inject_no_think(req2)
    assert req2["chat_template_kwargs"] == {"enable_thinking": False}


def test_stamp_default_temp_fills_missing(monkeypatch) -> None:
    monkeypatch.setattr(p, "DEFAULT_TEMP", 0.0)
    req: dict = {"messages": []}
    assert p._stamp_default_temp(req) is True
    assert req["temperature"] == 0.0


def test_stamp_default_temp_respects_explicit(monkeypatch) -> None:
    monkeypatch.setattr(p, "DEFAULT_TEMP", 0.0)
    req = {"temperature": 0.7}
    assert p._stamp_default_temp(req) is False
    assert req["temperature"] == 0.7


def test_stamp_default_temp_disabled(monkeypatch) -> None:
    monkeypatch.setattr(p, "DEFAULT_TEMP", None)
    req: dict = {}
    assert p._stamp_default_temp(req) is False
    assert "temperature" not in req


def test_sse_from_response_emits_role_content_and_done() -> None:
    data = {
        "id": "c1", "model": "local",
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
    }
    body = p._sse_from_response(data).decode()
    assert body.startswith("data: ")
    assert '"role": "assistant"' in body
    assert '"content": "hello"' in body
    assert body.rstrip().endswith("data: [DONE]")


def test_upstream_retries_refused_then_succeeds(monkeypatch) -> None:
    # item 31: a refused upstream during a server reload is retried (connect only)
    # until it comes up, rather than 502-ing opencode's first call.
    import urllib.error

    calls = {"n": 0}
    sentinel = object()

    def fake_urlopen(req, timeout=600):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        return sentinel

    monkeypatch.setattr(p.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(p.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(p, "UPSTREAM_READY_S", 90.0)
    out = p._upstream("/v1/chat/completions", "POST", {}, b"{}")
    assert out is sentinel
    assert calls["n"] == 3


def test_upstream_does_not_retry_http_error(monkeypatch) -> None:
    # An HTTPError (reason is a str, not OSError) is a real upstream response —
    # propagate immediately, never retry.
    import urllib.error

    calls = {"n": 0}

    def fake_urlopen(req, timeout=600):  # noqa: ARG001
        calls["n"] += 1
        raise urllib.error.HTTPError("http://x", 500, "boom", {}, None)

    monkeypatch.setattr(p.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(p.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(p, "UPSTREAM_READY_S", 90.0)
    try:
        p._upstream("/v1/chat/completions", "POST", {}, b"{}")
    except urllib.error.HTTPError:
        pass
    else:  # pragma: no cover
        raise AssertionError("HTTPError should propagate")
    assert calls["n"] == 1


def test_upstream_gives_up_when_retry_disabled(monkeypatch) -> None:
    # UPSTREAM_READY_S=0 disables the readiness retry entirely.
    import urllib.error

    calls = {"n": 0}

    def fake_urlopen(req, timeout=600):  # noqa: ARG001
        calls["n"] += 1
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(p.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(p.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(p, "UPSTREAM_READY_S", 0.0)
    try:
        p._upstream("/v1/chat/completions", "POST", {}, b"{}")
    except urllib.error.URLError:
        pass
    else:  # pragma: no cover
        raise AssertionError("refused connect should propagate when retry disabled")
    assert calls["n"] == 1


def test_sse_from_response_emits_tool_calls() -> None:
    data = {
        "id": "c1", "model": "local",
        "choices": [{
            "message": {"content": None, "tool_calls": [
                {"id": "t0", "type": "function",
                 "function": {"name": "grep", "arguments": '{"pattern":"x"}'}},
            ]},
            "finish_reason": "tool_calls",
        }],
    }
    body = p._sse_from_response(data).decode()
    assert '"tool_calls"' in body
    assert '"name": "grep"' in body
    assert '"finish_reason": "tool_calls"' in body
