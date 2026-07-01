#!/usr/bin/env python3
"""item 29/30 — omlx tool-call repair proxy (programmatic hook).

omlx's gemma4 output adapter has defects vs mlx-lm + its repair proxy:
  1. `<eos>`/`<end_of_turn>` leak into assistant text as literal characters
     (gemma4 output parser is built with `stop_token_ids=set()` and decodes
     specials to text).  ~58% of agentic turns corrupted (item 29).
  2. The model intermittently emits tool calls as TEXT — code-style
     `print(grep(pattern="x", include="y"))`, kwargs `grep pattern="x" include="y"`,
     or the native Gemma spelling `<|tool_call>call:grep{...}<tool_call|>` — which
     omlx leaves in `content` instead of structuring as `tool_calls`.

This proxy sits between opencode and omlx and repairs these, mirroring the role of
`mlx_repair_proxy.py` for mlx-lm.  Because opencode STREAMS and omlx leaks these
mid-stream, the proxy forces a NON-streaming upstream call, repairs the full
response, then re-emits it to opencode as SSE (single-delta) when streaming was
requested.  Everything non-tool flows through transparently.

Note: this cannot, on its own, fix the model *narrating* tool use
(`[grep is used to find ...]`) instead of emitting a call.  That is an input-side /
weak-model behaviour; item 30 addresses it with (a) the optional
OMLX_PROXY_NO_THINK input-side lever (mirrors mlx-lm's NO_THINK — strip the Gemma
thinking phase on tool turns) and (b) additive prompt steering
(`harness_micro_configs/omlx-toolsteer.json`), not here.

Env (mirrors `mlx_repair_proxy.py` naming where shared):
  OMLX_PROXY_UPSTREAM     upstream omlx base url (default http://127.0.0.1:8088)
  OMLX_PROXY_PORT         front listen port opencode talks to (default 8080)
  OMLX_PROXY_LISTEN_HOST  front listen host (default 127.0.0.1)
  OMLX_PROXY_REPAIR       "0" => pure transparent forwarder (default on)
  OMLX_PROXY_NO_THINK     "1" => inject chat_template_kwargs.enable_thinking=False
                          on tool turns (item 30 Cat-2 input-side lever)
  OMLX_PROXY_DEFAULT_TEMP serving-temperature PARITY with mlx-lm. mlx_lm.server
                          defaults missing-temperature requests to 0.0 (greedy);
                          omlx defaults to its settings.json value (1.0), so the
                          item-29/30 omlx-vs-mlx-lm T2 comparison was confounded
                          — omlx ran stochastic, mlx-lm greedy. This stamps the
                          given temperature onto any chat request that omits one
                          (default "0" => greedy parity; set "" to disable).
                          Root cause of omlx's narration leak + run-to-run
                          variance (item 30 Cat-2).
  OMLX_PROXY_CAPTURE      dir to dump each tool request+response for diagnosis
                          (off by default; item 30 prompt-divergence probe)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("OMLX_PROXY_UPSTREAM", "http://127.0.0.1:8088").rstrip("/")
PORT = int(os.environ.get("OMLX_PROXY_PORT", "8080"))
LISTEN_HOST = os.environ.get("OMLX_PROXY_LISTEN_HOST", "127.0.0.1")
REPAIR = os.environ.get("OMLX_PROXY_REPAIR", "1") != "0"
NO_THINK = os.environ.get("OMLX_PROXY_NO_THINK", "0") != "0"
CAPTURE_DIR = os.environ.get("OMLX_PROXY_CAPTURE", "")
# Streaming-repair (item 30 T3 fix): when the client requests a stream, pass
# omlx's SSE THROUGH incrementally (so opencode gets partial output rather than
# waiting for the full turn — long T3 generations otherwise time out at step=0),
# stripping <eos>/<end_of_turn> from each content delta. Text-tool-call repair is
# NOT applied on the stream path, but with temp-parity (greedy) omlx emits native
# structured tool_calls, so it isn't needed. "0" => fall back to the buffer-and-
# repair path (force non-streaming).
STREAM_REPAIR = os.environ.get("OMLX_PROXY_STREAM_REPAIR", "1") != "0"
# Upstream-readiness retry (item 31 server-reload race): when omlx is being
# (re)started under the proxy, opencode's first call can reach the proxy before
# the upstream :8088 is accepting connections, and a one-shot urlopen would 502
# — which the harness saw as "0 requests for the whole episode" (the upstream
# never recovered within the turn). Instead, retry the *connect* on a refused
# upstream for a bounded window so the first real request lands once omlx is up.
# Only the connection attempt is retried (the body is re-sendable and no
# response bytes have been read yet); "0" disables it.
UPSTREAM_READY_S = float(os.environ.get("OMLX_PROXY_UPSTREAM_READY_S", "90"))
# Serving-temperature parity with mlx-lm (default greedy). "" disables stamping.
_DEFAULT_TEMP_RAW = os.environ.get("OMLX_PROXY_DEFAULT_TEMP", "0")
try:
    DEFAULT_TEMP: "float | None" = float(_DEFAULT_TEMP_RAW) if _DEFAULT_TEMP_RAW != "" else None
except ValueError:
    DEFAULT_TEMP = None

_EOS_RE = re.compile(r"<eos>|<end_of_turn>")
# Native Gemma tool-call spelling, should it ever leak as text rather than be
# structured by omlx's gemma4 output parser. Args use Gemma's `<|"|>` string
# delimiter: call:grep{pattern:<|"|>x<|"|>,include:<|"|>y<|"|>}
_NATIVE_RE = re.compile(r"<\|tool_call>\s*call:\s*(\w+)\s*\{(.*?)\}\s*<tool_call\|>", re.DOTALL)
_STR_DELIM = '<|"|>'

# Hop-by-hop headers must not be forwarded; Content-Length is recomputed.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _strip_specials(s):
    return _EOS_RE.sub("", s) if isinstance(s, str) else s


def _parse_kwargs(argstr: str) -> dict:
    """Parse `pattern="x", include="y"` or `pattern="x" include="y"` -> dict.

    Also accepts Gemma's `<|"|>`-delimited strings and bare key:val pairs.
    """
    out: dict = {}
    # Gemma native string delimiter: key:<|"|>value<|"|>
    for m in re.finditer(r'(\w+)\s*[:=]\s*<\|"\|>(.*?)<\|"\|>', argstr, re.DOTALL):
        out.setdefault(m.group(1), m.group(2))
    # double-quoted
    for m in re.finditer(r'(\w+)\s*[:=]\s*"((?:[^"\\]|\\.)*)"', argstr):
        out.setdefault(m.group(1), m.group(2))
    # single-quoted
    for m in re.finditer(r"(\w+)\s*[:=]\s*'((?:[^'\\]|\\.)*)'", argstr):
        out.setdefault(m.group(1), m.group(2))
    return out


def _find_native_tool_calls(content: str) -> list:
    """Detect native Gemma `<|tool_call>call:NAME{...}<tool_call|>` text."""
    calls = []
    for m in _NATIVE_RE.finditer(content or ""):
        kw = _parse_kwargs(m.group(2))
        calls.append((m.group(1), kw))
    return calls


def _find_text_tool_calls(content: str, tool_names: list) -> list:
    """Detect tool calls the model wrote as TEXT. Returns [(name, args_dict)].

    Order of precedence: native Gemma spelling > code-style name(...) > kwargs.
    """
    if not content:
        return []
    native = _find_native_tool_calls(content)
    if native:
        return native
    if not tool_names:
        return []
    calls = []
    names = "|".join(re.escape(n) for n in tool_names)
    # code-style: name( ... )  (also matches inside print( name(...) ))
    for m in re.finditer(rf"\b({names})\s*\(([^()]*)\)", content):
        kw = _parse_kwargs(m.group(2))
        if kw:
            calls.append((m.group(1), kw))
    # kwargs-style: name kw="..." kw="..."  (only if code-style found nothing)
    if not calls:
        for m in re.finditer(rf'\b({names})\b((?:\s+\w+\s*=\s*"[^"]*")+)', content):
            kw = _parse_kwargs(m.group(2))
            if kw:
                calls.append((m.group(1), kw))
    return calls


def _tool_names(req_obj: dict) -> list:
    out = []
    for t in (req_obj.get("tools") or []):
        fn = (t or {}).get("function") or {}
        if fn.get("name"):
            out.append(fn["name"])
    return out


def _strip_call_text(content: "str | None", calls: list) -> "str | None":
    """Remove leaked call text from content so it isn't shown as prose."""
    cleaned = content or ""
    # Native spellings: remove the whole `<|tool_call>...<tool_call|>` span.
    cleaned = _NATIVE_RE.sub("", cleaned)
    for name, _a in calls:
        # code/kwargs spellings: drop `[print(]name ...)` up to end of line.
        cleaned = re.sub(rf"(?:print\()?\b{re.escape(name)}\b[^\n]*\)?", "", cleaned)
    return cleaned.strip() or None


def _repair(data: dict, tool_names: list) -> tuple:
    """Repair an omlx chat.completion dict in place. Returns (data, repaired?)."""
    if not REPAIR:
        return data, False
    repaired = False
    for ch in data.get("choices") or []:
        msg = ch.get("message") or {}
        content = msg.get("content")
        # 1. strip leaked specials from content
        if isinstance(content, str) and _EOS_RE.search(content):
            content = _strip_specials(content)
            msg["content"] = content
            repaired = True
        # 2. if omlx already structured tool_calls, leave them (keep content clean)
        if msg.get("tool_calls"):
            continue
        # 3. parse text-style tool calls out of content
        calls = _find_text_tool_calls(content or "", tool_names)
        if calls:
            tcs = []
            for i, (name, args) in enumerate(calls):
                tcs.append({
                    "id": f"call_omlxfix_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                })
            msg["tool_calls"] = tcs
            msg["content"] = _strip_call_text(content, calls)
            ch["finish_reason"] = "tool_calls"
            repaired = True
    return data, repaired


def _strip_chunk_eos(chunk: dict) -> bool:
    """Strip leaked <eos>/<end_of_turn> from a streaming chunk's delta.content.
    Returns True if it changed anything. (<eos>/<end_of_turn> are single special
    tokens, so each lands in one delta — no cross-delta buffering needed.)"""
    changed = False
    for ch in chunk.get("choices") or []:
        d = ch.get("delta") or {}
        c = d.get("content")
        if isinstance(c, str) and _EOS_RE.search(c):
            d["content"] = _strip_specials(c)
            changed = True
    return changed


def _inject_no_think(req_obj: dict) -> None:
    """item 30 Cat-2 input-side lever: strip the Gemma thinking phase on tool
    turns by forcing chat_template_kwargs.enable_thinking=False. Mirrors
    mlx_repair_proxy's NO_THINK. omlx forwards chat_template_kwargs to
    tokenizer.apply_chat_template (engine/vlm.py)."""
    kw = dict(req_obj.get("chat_template_kwargs") or {})
    kw.setdefault("enable_thinking", False)
    req_obj["chat_template_kwargs"] = kw


def _stamp_default_temp(req_obj: dict) -> bool:
    """Stamp DEFAULT_TEMP onto a chat request that omits a temperature, for
    serving parity with mlx_lm.server (which defaults missing temps to 0.0).
    Returns True if it mutated the request. An explicit request temperature
    (incl. 0) always wins."""
    if DEFAULT_TEMP is None:
        return False
    if req_obj.get("temperature") is not None:
        return False
    req_obj["temperature"] = DEFAULT_TEMP
    return True


def _capture(cap_id: str, suffix: str, data) -> None:
    """Best-effort dump of a captured request/response. Never raises into the
    request path; no-op if capture is off."""
    if not (CAPTURE_DIR and cap_id):
        return
    try:
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        path = os.path.join(CAPTURE_DIR, f"{cap_id}.{suffix}")
        with open(path, "w") as f:
            f.write(data if isinstance(data, str)
                    else json.dumps(data, indent=2, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — capture must never break the proxy
        pass


def _upstream(path, method, headers, body):
    req = urllib.request.Request(UPSTREAM + path, data=body, method=method)
    for k, v in headers.items():
        if k.lower() in HOP_BY_HOP:
            continue
        req.add_header(k, v)
    deadline = time.monotonic() + UPSTREAM_READY_S
    delay = 0.25
    while True:
        try:
            return urllib.request.urlopen(req, timeout=600)
        except urllib.error.URLError as e:
            # A refused/unavailable upstream during a (re)start raises URLError
            # wrapping an OSError before any response bytes — safe to retry the
            # connect. Anything else (or past the window) propagates unchanged.
            reason = getattr(e, "reason", None)
            if (UPSTREAM_READY_S > 0 and isinstance(reason, OSError)
                    and time.monotonic() < deadline):
                time.sleep(delay)
                delay = min(delay * 2, 2.0)
                continue
            raise


def _sse_from_response(data: dict) -> bytes:
    """Synthesize an OpenAI streaming SSE body from a full chat.completion."""
    cid = data.get("id", "chatcmpl-omlxproxy")
    model = data.get("model", "local")
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    finish = ch.get("finish_reason") or "stop"
    chunks = []

    def chunk(delta, fr=None):
        return "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": fr}],
        }) + "\n\n"

    chunks.append(chunk({"role": "assistant"}))
    if msg.get("content"):
        chunks.append(chunk({"content": msg["content"]}))
    for i, tc in enumerate(msg.get("tool_calls") or []):
        chunks.append(chunk({"tool_calls": [{
            "index": i, "id": tc["id"], "type": "function",
            "function": {"name": tc["function"]["name"],
                         "arguments": tc["function"]["arguments"]},
        }]}))
    chunks.append(chunk({}, fr=finish))
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks).encode()


class Handler(BaseHTTPRequestHandler):
    _cap_seq = 0

    def log_message(self, *_a):
        pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _safe_write(self, payload: bytes) -> bool:
        """Write a body, swallowing client-disconnect errors (opencode may
        cancel a turn mid-flight; the forced non-streaming wait makes that
        common). Returns False if the client went away."""
        try:
            self.wfile.write(payload)
            self.wfile.flush()  # flush per write so SSE streams in real time
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _handle(self, method):
        path = self.path
        body = self._read_body()
        req_obj = None
        if body:
            try:
                req_obj = json.loads(body)
            except Exception:
                req_obj = None

        is_chat = path.rstrip("/").endswith("/chat/completions")
        # Temperature parity with mlx-lm applies to EVERY chat request (tool or
        # not), so stamp + re-serialize before the tool-branch decision.
        if is_chat and isinstance(req_obj, dict) and _stamp_default_temp(req_obj):
            body = json.dumps(req_obj).encode()

        has_tools = isinstance(req_obj, dict) and req_obj.get("tools")
        if not (REPAIR and is_chat and has_tools):
            return self._passthrough(method, path, body)

        want_stream = bool(req_obj.get("stream"))
        if NO_THINK:
            _inject_no_think(req_obj)

        cap_id = ""
        if CAPTURE_DIR:
            Handler._cap_seq += 1
            cap_id = f"{Handler._cap_seq:04d}"
            _capture(cap_id, "request.json", req_obj)

        # Streaming-repair path (item 30 T3 fix): forward omlx's SSE incrementally
        # with per-delta eos-strip, so opencode isn't blocked on the full turn.
        if want_stream and STREAM_REPAIR:
            return self._handle_streaming(path, req_obj)

        up_obj = dict(req_obj)
        up_obj["stream"] = False  # force non-streaming so we can repair the full turn
        try:
            r = _upstream(path, "POST", dict(self.headers), json.dumps(up_obj).encode())
            data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            obj = json.loads(txt) if txt.strip().startswith("{") else {"error": txt}
            return self._send_json(e.code, obj)
        except Exception as e:
            return self._send_json(502, {"error": f"omlx-proxy upstream error: {e}"})

        if cap_id:
            _capture(cap_id, "response_raw.json", data)
        data, _ = _repair(data, _tool_names(req_obj))
        if cap_id:
            _capture(cap_id, "response_repaired.json", data)

        if want_stream:
            payload = _sse_from_response(data)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._safe_write(payload)
        else:
            self._send_json(200, data)

    def _handle_streaming(self, path, req_obj):
        """Forward omlx's SSE to the client incrementally, stripping <eos> from
        each `data:` chunk's delta.content. Mirrors mlx_repair_proxy's streaming
        passthrough (forward upstream status+headers verbatim, write RAW lines via
        readline so the SSE framing is byte-exact; close-delimited) — only the
        `data:` lines are rewritten. Avoids the buffer-the-whole-turn stall that
        timed long T3 generations out at step=0."""
        up_obj = dict(req_obj)
        up_obj["stream"] = True
        try:
            r = _upstream(path, "POST", dict(self.headers), json.dumps(up_obj).encode())
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            obj = json.loads(txt) if txt.strip().startswith("{") else {"error": txt}
            return self._send_json(e.code, obj)
        except Exception as e:
            return self._send_json(502, {"error": f"omlx-proxy upstream error: {e}"})

        self.close_connection = True
        try:
            self.send_response(r.status)
            for k, v in r.headers.items():
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")  # length unknown; end by close
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return
        # Read line by line (SSE is newline-delimited); rewrite only `data:` lines.
        while True:
            line = r.readline()
            if not line:
                break
            payload = line.strip()
            if payload.startswith(b"data:") and payload != b"data: [DONE]":
                try:
                    chunk = json.loads(payload[5:].strip())
                    if _strip_chunk_eos(chunk):
                        line = ("data: " + json.dumps(chunk) + "\n").encode()
                except Exception:
                    pass  # not JSON / parse error → forward verbatim
            if not self._safe_write(line):
                break

    def _passthrough(self, method, path, body):
        try:
            r = _upstream(path, method, dict(self.headers), body or None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self._safe_write(payload)
            return
        except Exception as e:
            return self._send_json(502, {"error": f"omlx-proxy upstream error: {e}"})
        payload = r.read()
        self.send_response(r.status)
        self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self._safe_write(payload)

    def _send_json(self, code, obj):
        payload = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return
        self._safe_write(payload)

    def handle_one_request(self):
        # Swallow client-disconnect noise that would otherwise dump a traceback
        # to stderr for every cancelled turn (the forced non-streaming wait makes
        # opencode cancellations land mid-write).
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def main():
    repair_state = "on" if REPAIR else "OFF (passthrough)"
    extras = []
    if DEFAULT_TEMP is not None:
        extras.append(f"temp-parity={DEFAULT_TEMP}")
    extras.append("stream-repair" if STREAM_REPAIR else "buffer-repair")
    if NO_THINK:
        extras.append("no-think")
    if CAPTURE_DIR:
        extras.append(f"capture={CAPTURE_DIR}")
    extra = (" + " + " + ".join(extras)) if extras else ""
    srv = ThreadingHTTPServer((LISTEN_HOST, PORT), Handler)
    sys.stderr.write(
        f"[omlx-proxy] :{PORT} -> {UPSTREAM} "
        f"(repair={repair_state}: eos-strip + text-tool-calls{extra})\n"
    )
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
