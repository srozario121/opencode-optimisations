#!/usr/bin/env python3
"""Local tool-call REPAIR proxy for Gemma 4 served via mlx_lm.server.

WHY THIS EXISTS  —  TEMPORARY WORKAROUND, NOT A PERMANENT COMPONENT
==================================================================
mlx-lm 0.31.3's Gemma 4 tool parser has two failure modes that break
opencode's edit/shell loop:

  * #1096 — native tool calls left as raw text in `message.content` with an
            empty `tool_calls` array (fixed in 0.31.3, handled here as belt-and-
            braces in case of regressions / older pins).
  * #1125 — `ValueError: No function provided.` raised *inside* mlx_lm.server
            when the model's output doesn't match the call regex, surfacing to
            opencode as an HTTP 500 that kills the turn. STILL UNFIXED in 0.31.3.

The fix for #1125 (return [] instead of raising) is tracked in:

    >>> https://github.com/ml-explore/mlx-lm/pull/1142 <<<

⚠️  REMINDER: once PR #1142 is merged and `MLX_LM_VERSION` in scripts/mlx.sh is
    bumped past the release that contains it, THIS PROXY IS NO LONGER NEEDED.
    Disable it (`MLX_PROXY=0`) and delete this file. The startup banner and the
    `proxy-up` reminder both point back here.

WHAT IT DOES
============
Sits between opencode and mlx_lm.server (a thin OpenAI-compatible shim). For
mlx-lm 0.31.3 streams Gemma 4 tool calls correctly, and the #1125 ValueError
only surfaces as a fatal HTTP 500 on the NON-streaming path. So the proxy splits
by request type:

  * **Streaming requests pass through transparently** (line-buffered), preserving
    the native token-by-token stream. Buffering these was the cause of the
    apparent "hang" — opencode (which streams) saw nothing until the whole
    response was generated. The server's own tool-call parsing is used as-is.
  * **Non-streaming tool requests are buffered and repaired**: re-parse
    `<|tool_call>…<tool_call|>` text in `content` into structured `tool_calls`
    (#1096); retry on the #1125 `ValueError` (a fresh sample may parse — only
    helps at temperature > 0), and on exhaustion return a graceful empty turn so
    the session survives instead of crashing on a 500.

Everything else (non-tool chats, /v1/models, …) passes through transparently.

DISABLE
=======
  * MLX_PROXY=0          (in scripts/mlx.sh) — don't run the proxy at all;
                          opencode talks straight to mlx_lm.server.
  * MLX_PROXY_REPAIR=0   — keep the proxy in the path but pass through unmodified
                          (pure transparent forward; useful for A/B debugging).

Stdlib only — no third-party deps (keeps it outside the mlx-lm/uvx environment).
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- config (from env; wired by scripts/mlx.sh) ------------------------------
LISTEN_HOST = os.environ.get("MLX_PROXY_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("MLX_PROXY_LISTEN_PORT", "8080"))
UPSTREAM = os.environ.get("MLX_PROXY_UPSTREAM", "http://127.0.0.1:8081").rstrip("/")
REPAIR = os.environ.get("MLX_PROXY_REPAIR", "1") != "0"
RETRIES = int(os.environ.get("MLX_PROXY_RETRIES", "2"))

# Out-of-band tracing: emit one OTLP span per chat request carrying the SYSTEM
# PROMPT. opencode's otel plugin can't see the system prompt (its plugin API
# only exposes user message parts), so the proxy — the one component that sees
# the full request body — surfaces it to the same Jaeger. Best-effort: disabled
# by default, wired on by scripts/mlx.sh when MLX_OTEL is on. Never affects the
# proxy's core function. See docs/opencode-local.md.
OTEL = os.environ.get("MLX_PROXY_OTEL", "0") != "0"
OTEL_ENDPOINT = os.environ.get("MLX_PROXY_OTEL_ENDPOINT", "http://127.0.0.1:4318").rstrip("/")
OTEL_SERVICE = os.environ.get("MLX_PROXY_OTEL_SERVICE", "mlx-proxy")
# Cap per-attribute string length so a huge system prompt can't bloat a span.
OTEL_MAX_ATTR = int(os.environ.get("MLX_PROXY_OTEL_MAX_ATTR", "131072"))
# Which opencode agent this proxy instance fronts: "main" (the coding loop) or
# "small_model" (the tiny title-slot model on its own port). Stamped onto every
# span so the otherwise-invisible title/small-model call is identifiable in
# Jaeger (TODO item 12, task D — opencode emits no span for it). Wired by
# scripts/mlx.sh (`_start_proxy`'s role arg).
ROLE = os.environ.get("MLX_PROXY_ROLE", "main")

PR_URL = "https://github.com/ml-explore/mlx-lm/pull/1142"

# Hop-by-hop headers must not be forwarded; Content-Length is recomputed.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

TOOL_START = "<|tool_call>"
TOOL_END = "<tool_call|>"
STR_DELIM = '<|"|>'  # Gemma 4's string delimiter inside tool-call args


# --- gemma4 arg conversion (ported from mlx_lm/tool_parsers/gemma4.py) --------
def _args_to_json(text: str) -> str:
    """Convert Gemma 4 call args (unquoted keys, <|"|> string delims) to JSON."""
    strings: list[str] = []

    def _capture(m: "re.Match[str]") -> str:
        strings.append(m.group(1))
        return f"\x00{len(strings) - 1}\x00"

    text = re.sub(r'<\|"\|>(.*?)<\|"\|>', _capture, text, flags=re.DOTALL)
    # Quote bare keys. Unlike upstream's fixed-width lookbehind, tolerate
    # whitespace around the key/colon (`{ key : ...`, `, key: ...`).
    text = re.sub(r"([{,])\s*([A-Za-z_]\w*)\s*:", r'\1"\2":', text)
    for i, s in enumerate(strings):
        text = text.replace(f"\x00{i}\x00", json.dumps(s))
    return text


_NAME_RE = re.compile(r"[\w-]+")


def _find_calls(text: str) -> list[dict]:
    """Find every `call:name{...}` with brace/string-literal-aware scanning.

    Stdlib `re` can't do the recursive balanced-brace match the upstream parser
    uses (it relies on the `regex` module), so we scan braces manually.
    """
    calls: list[dict] = []
    i, n = 0, len(text)
    while True:
        j = text.find("call:", i)
        if j < 0:
            break
        k = j + len("call:")
        m = _NAME_RE.match(text, k)
        if not m:
            i = k
            continue
        name = m.group(0)
        k = m.end()
        if k >= n or text[k] != "{":
            i = k
            continue
        depth, p = 0, k
        while p < n:
            if text.startswith(STR_DELIM, p):
                q = text.find(STR_DELIM, p + len(STR_DELIM))
                if q < 0:
                    p = n
                    break
                p = q + len(STR_DELIM)
                continue
            c = text[p]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    p += 1
                    break
            p += 1
        if depth != 0:
            i = k
            continue
        argstr = text[k:p]  # includes the surrounding { }
        try:
            args = json.loads(_args_to_json(argstr))
        except Exception:
            i = p
            continue
        calls.append({"name": name, "arguments": args})
        i = p
    return calls


def _extract_from_content(content):
    """Repair #1096: pull tool calls out of `content`, return (clean, calls)."""
    if not isinstance(content, str) or "call:" not in content:
        return content, []
    calls = _find_calls(content)
    if not calls:
        return content, []
    clean = content.replace(TOOL_START, "").replace(TOOL_END, "")
    idx = clean.find("call:")
    if idx >= 0:
        clean = clean[:idx]
    clean = clean.strip()
    return (clean or None), calls


def _to_openai_tool_calls(calls: list[dict]) -> list[dict]:
    return [
        {
            "id": f"call_{n}",
            "type": "function",
            "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
        }
        for n, c in enumerate(calls)
    ]


def _repair(resp: dict) -> dict:
    """Mutate a non-streamed chat.completion dict in place; return it."""
    for ch in resp.get("choices", []):
        msg = ch.get("message") or {}
        if msg.get("tool_calls"):
            continue
        clean, calls = _extract_from_content(msg.get("content"))
        if calls:
            msg["content"] = clean
            msg["tool_calls"] = _to_openai_tool_calls(calls)
            ch["finish_reason"] = "tool_calls"
        ch["message"] = msg
    return resp


def _upstream(path, method, headers, body):
    req = urllib.request.Request(UPSTREAM + path, data=body, method=method)
    for k, v in headers.items():
        if k.lower() not in HOP_BY_HOP:
            req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=600)


# --- out-of-band tracing: surface the system prompt to Jaeger ----------------
def _otel_ids(traceparent, session_id):
    """Pick the trace this span joins, in priority order. Returns
    (trace_id_hex32, span_id_hex16, parent_span_id_or_None).

    1. Inbound W3C ``traceparent`` — true correlation with the sender's trace
       (used if opencode ever propagates one, or another caller does).
    2. The opencode session id — opencode always sends ``x-session-id`` to this
       provider, but never a traceparent, so we derive a *stable* trace id from
       it: every request in one session then groups into a single Jaeger trace.
       This is what makes spans fall under individual sessions.
    3. Nothing to correlate on — a standalone trace per request.
    """
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 3 and len(parts[1]) == 32 and len(parts[2]) == 16:
            return parts[1], os.urandom(8).hex(), parts[2]
    if session_id:
        trace_id = hashlib.sha256(session_id.encode()).hexdigest()[:32]
        return trace_id, os.urandom(8).hex(), None
    return os.urandom(16).hex(), os.urandom(8).hex(), None


def _attr(key, value):
    """Build one OTLP KeyValue, JSON-encoding/truncating non-scalar values."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": value}}
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(s) > OTEL_MAX_ATTR:
        s = s[:OTEL_MAX_ATTR] + "…[truncated]"
    return {"key": key, "value": {"stringValue": s}}


def _emit_span(start_ns, end_ns, req_obj, traceparent, session_id):
    """POST one OTLP/HTTP span carrying the system prompt. Never raises."""
    try:
        messages = req_obj.get("messages") or []
        system = "\n\n".join(
            m["content"]
            for m in messages
            if isinstance(m, dict)
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
        )
        trace_id, span_id, parent = _otel_ids(traceparent, session_id)
        # TODO item 12, task D — tag the agent/role so the title/small-model call
        # (which opencode emits no span for) is obvious + grouped in Jaeger. ROLE
        # comes from the proxy instance (the small-model proxy sets it to
        # "small_model"); the title heuristic is a content-based backstop for when
        # the main proxy happens to serve a title call (e.g. MLX_SMALL=0).
        is_title = ROLE == "small_model" or (
            "title" in system.lower() and len(system) < 4000 and not req_obj.get("tools")
        )
        attrs = [
            _attr("gen_ai.agent.role", ROLE),
            _attr("gen_ai.agent.is_title_call", bool(is_title)),
            _attr("gen_ai.system.message", system),
            _attr("llm.system_prompt", system),
            _attr("gen_ai.request.model", req_obj.get("model", "")),
            _attr("llm.input_messages", messages),
            _attr("gen_ai.request.message_count", len(messages)),
            _attr("llm.is_streaming", bool(req_obj.get("stream"))),
            _attr("llm.tools.count", len(req_obj.get("tools") or [])),
        ]
        if session_id:
            attrs.append(_attr("session.id", session_id))
        span = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": "mlx.chat.completions",
            "kind": 2,  # SERVER
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(end_ns),
            "attributes": attrs,
            "status": {"code": 1},  # OK
        }
        if parent:
            span["parentSpanId"] = parent
        payload = json.dumps(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": [_attr("service.name", OTEL_SERVICE)]},
                        "scopeSpans": [{"scope": {"name": "mlx-proxy"}, "spans": [span]}],
                    }
                ]
            }
        ).encode()
        req = urllib.request.Request(
            OTEL_ENDPOINT + "/v1/traces",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass  # tracing is best-effort — it must never disturb the proxy


def _emit_async(start_ns, req_obj, traceparent, session_id):
    """Fire-and-forget the span on a daemon thread so it can't block a turn."""
    threading.Thread(
        target=_emit_span,
        args=(start_ns, time.time_ns(), req_obj, traceparent, session_id),
        daemon=True,
    ).start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # quiet; we log our own lines to stderr
        pass

    # -- entry points ---------------------------------------------------------
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    # -- core -----------------------------------------------------------------
    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _handle(self, method: str):
        start_ns = time.time_ns()
        body = self._read_body()
        path = self.path
        is_chat = method == "POST" and path.rstrip("/").endswith("/chat/completions")

        # Parse chat bodies for both repair and tracing; tracing needs the
        # system message even on requests the repair path leaves untouched.
        req_obj = None
        if is_chat:
            try:
                req_obj = json.loads(body or b"{}")
            except Exception:
                req_obj = None

        try:
            return self._dispatch(method, path, body, req_obj)
        finally:
            # Surface the system prompt (invisible to opencode's otel plugin) to
            # Jaeger — best-effort, on a daemon thread, after the turn is served.
            # opencode sends the session id as x-session-id (x-session-affinity
            # is its older alias); we group spans by it so they fall under
            # individual sessions. See docs/opencode-local.md.
            if OTEL and isinstance(req_obj, dict):
                _emit_async(
                    start_ns,
                    req_obj,
                    self.headers.get("traceparent"),
                    self.headers.get("x-session-id")
                    or self.headers.get("x-session-affinity"),
                )

    def _dispatch(self, method, path, body, req_obj):
        # Only intercept tool-enabled chat completions; everything else flows
        # through. With REPAIR off the proxy is a pure transparent forwarder.
        if not (REPAIR and isinstance(req_obj, dict) and req_obj.get("tools")):
            return self._passthrough(method, path, body)

        # mlx-lm 0.31.3 streams Gemma 4 tool calls correctly, token by token. The
        # #1125 ValueError only surfaces as a fatal HTTP 500 on the NON-streaming
        # path. So stream requests pass through transparently (preserving the
        # native incremental stream — buffering them here was the cause of the
        # apparent "hang": opencode saw nothing until full generation completed).
        # Only the non-streaming path is buffered and repaired.
        if req_obj.get("stream"):
            return self._passthrough(method, path, body)

        up_obj = dict(req_obj)
        up_obj["stream"] = False
        up_body = json.dumps(up_obj).encode()

        data = None
        for attempt in range(RETRIES + 1):
            try:
                r = _upstream(path, "POST", self.headers, up_body)
                data = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors="replace")
                if e.code == 500 and "No function provided" in txt:
                    sys.stderr.write(
                        f"[mlx-proxy] #1125 ValueError (attempt {attempt + 1}/"
                        f"{RETRIES + 1}); retrying. {PR_URL}\n"
                    )
                    sys.stderr.flush()
                    continue
                obj = json.loads(txt) if txt.strip().startswith("{") else {"error": txt}
                return self._send_json(e.code, obj)
            except Exception as e:
                return self._send_json(502, {"error": f"mlx-proxy upstream error: {e}"})

        if data is None:
            sys.stderr.write(
                f"[mlx-proxy] #1125 unrecovered after {RETRIES + 1} attempts; "
                f"returning empty turn so the session survives. {PR_URL}\n"
            )
            sys.stderr.flush()
            data = {
                "id": "chatcmpl-proxy-fallback",
                "object": "chat.completion",
                "model": req_obj.get("model", "local"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "stop",
                    }
                ],
            }
        else:
            data = _repair(data)

        return self._send_json(200, data)

    # -- responders -----------------------------------------------------------
    def _passthrough(self, method, path, body):
        try:
            r = _upstream(path, method, self.headers, body or None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        except Exception as e:
            return self._send_json(502, {"error": f"mlx-proxy upstream error: {e}"})

        self.send_response(r.status)
        for k, v in r.headers.items():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")  # length unknown; signal end by close
        self.close_connection = True
        self.end_headers()
        # Read line by line, not in fixed blocks: SSE is newline-delimited and a
        # block read would stall until the buffer fills, defeating streaming.
        while True:
            line = r.readline()
            if not line:
                break
            try:
                self.wfile.write(line)
                self.wfile.flush()
            except BrokenPipeError:
                break

    def _send_json(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    banner = (
        "=" * 74 + "\n"
        "[mlx-proxy] Gemma 4 tool-call REPAIR proxy — TEMPORARY WORKAROUND.\n"
        "[mlx-proxy] Reason: mlx-lm 0.31.3 parser bugs (#1096 handled, #1125 OPEN).\n"
        f"[mlx-proxy] Remove once fixed & MLX_LM_VERSION bumped: {PR_URL}\n"
        f"[mlx-proxy] listen=http://{LISTEN_HOST}:{LISTEN_PORT}  "
        f"upstream={UPSTREAM}  repair={'on' if REPAIR else 'OFF (passthrough)'}\n"
        f"[mlx-proxy] role={ROLE}  system-prompt tracing: "
        f"{'on -> ' + OTEL_ENDPOINT + ' (service ' + OTEL_SERVICE + ')' if OTEL else 'off'}\n"
        + "=" * 74 + "\n"
    )
    sys.stderr.write(banner)
    sys.stderr.flush()
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
