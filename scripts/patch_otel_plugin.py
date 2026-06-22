#!/usr/bin/env python3
"""Vendor + patch the opencode OpenTelemetry plugin into a LOCAL copy.

Reads the cached ``@devtheops/opencode-plugin-otel`` bundle and writes a PATCHED
local copy that opencode loads directly (referenced by absolute path in
opencode.json), so an ``@latest`` re-fetch of the cache can never revert the
edits — the reason this replaced the in-place sed patch in scripts/mlx.sh.

Two patches, both TEMPORARY workarounds layered on a third-party minified
bundle. Each is anchored on readable identifiers (the bundle is bundled, not
name-mangled); a missing anchor exits non-zero so an upstream change is noticed
rather than silently skipped.

  1. Span flush — swap ``BatchSpanProcessor`` -> ``SimpleSpanProcessor`` so spans
     export the instant they end. opencode runs the plugin in its server process
     and tears it down without firing the batch-flush hooks, so batched spans are
     discarded and Jaeger stays empty. (tracks DEVtheOPS/opencode-plugin-otel)

  2. Per-session trace grouping — the plugin starts each session / llm / tool
     span from ``ctx.rootContext()``, and opencode's async flow doesn't keep the
     session span alive across turns, so every span becomes its own root with a
     fresh random trace id: one opencode session ends up scattered across many
     Jaeger traces. We seed every span with a DETERMINISTIC trace id derived from
     the session id (``sha256(sessionID)[:32]``) — the SAME derivation the mlx
     repair proxy uses for its system-prompt spans — so all of a session's spans
     (plugin + proxy) collapse into a single Jaeger trace per session. Only the
     trace id has to match for Jaeger to group; dangling parent refs are fine.
     The seam is the 4 ``ctx.rootContext()`` call sites; the session id is in
     scope at each as ``sessionID`` (session/message spans) or
     ``toolPart.sessionID`` (tool spans).

Usage: patch_otel_plugin.py <src-index.js> <dest-index.js>
"""
import re
import sys

# Inserted at module scope (uses __require + import_api3, both in scope there).
# Wrapped in try/catch: any failure degrades to the original context, so the
# plugin keeps working (just without grouping) rather than breaking opencode.
HELPER = (
    "function __seedSessionCtx(sessionID, baseCtx) {"
    " if (!sessionID) return baseCtx;"
    " try {"
    ' const __h = __require("crypto").createHash("sha256")'
    ".update(String(sessionID)).digest(\"hex\");"
    " return import_api3.trace.setSpanContext(baseCtx,"
    " { traceId: __h.slice(0, 32), spanId: __h.slice(32, 48),"
    " traceFlags: 1, isRemote: true });"
    " } catch (e) { return baseCtx; }"
    "}\n"
)


def patch(src: str) -> tuple[str, str]:
    if "__seedSessionCtx" in src:
        return src, "already patched"
    notes = []

    # 1. flush: batch -> simple (idempotent — the cache may already be flushed
    #    by an earlier in-place patch, in which case SimpleSpanProcessor is set)
    src, n = re.subn(
        r"BatchSpanProcessor\(traceExporter\)",
        "SimpleSpanProcessor(traceExporter)",
        src,
    )
    if n:
        notes.append(f"flush x{n}")
    elif "SimpleSpanProcessor(traceExporter)" in src:
        notes.append("flush already")
    else:
        sys.exit("patch_otel_plugin: anchor missing — *SpanProcessor(traceExporter)")

    # 2a. insert the deterministic-trace helper at module scope
    if "function handleSessionCreated" not in src:
        sys.exit("patch_otel_plugin: anchor missing — handleSessionCreated")
    src = src.replace(
        "function handleSessionCreated",
        HELPER + "function handleSessionCreated",
        1,
    )

    # 2b. tool-span sites use toolPart.sessionID
    src, nt = re.subn(
        r"(ctx\.sessionSpans\.get\(toolPart\.sessionID\);\s*const baseCtx = )"
        r"ctx\.rootContext\(\)",
        r"\1__seedSessionCtx(toolPart.sessionID, ctx.rootContext())",
        src,
    )
    # 2c. remaining session/message-span sites use a bare sessionID
    src, ns = re.subn(
        r"const baseCtx = ctx\.rootContext\(\)",
        r"const baseCtx = __seedSessionCtx(sessionID, ctx.rootContext())",
        src,
    )
    if nt + ns != 4:
        sys.exit(
            f"patch_otel_plugin: expected 4 ctx.rootContext() seams, "
            f"patched {nt} tool + {ns} session/message"
        )
    notes.append(f"seed tool x{nt}, session/msg x{ns}")
    return src, "; ".join(notes)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: patch_otel_plugin.py <src-index.js> <dest-index.js>")
    src_path, dest_path = sys.argv[1], sys.argv[2]
    with open(src_path) as fh:
        src = fh.read()
    out, note = patch(src)
    with open(dest_path, "w") as fh:
        fh.write(out)
    print(f"patch_otel_plugin: wrote {dest_path} ({note})")


if __name__ == "__main__":
    main()
