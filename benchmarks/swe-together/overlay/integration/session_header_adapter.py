#!/usr/bin/env python3
"""Thin streaming adapter that stamps a logical Memory session on CC traffic."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

from ours_v3_controller import OursV3Controller


HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

# Claude Code 2.1.108 emits a per-request random ``cch`` value in the first
# system line.  That value is Anthropic billing telemetry, not an instruction,
# but DeepSeek's Anthropic-compatible endpoint includes it in the prompt-prefix
# cache key.  Stabilise only this one field at the SWE-Together compatibility
# boundary so repeated agent turns can reuse the provider cache without
# changing MemoryProxy's Skill injection or the benchmark conversation.
_BILLING_CCH = re.compile(
    r"(?m)(^x-anthropic-billing-header:[^\r\n]*?\bcch=)[^;\r\n]+"
)


def _stabilize_system_value(system: object) -> tuple[object, bool]:
    changed = False
    if isinstance(system, str):
        value, count = _BILLING_CCH.subn(r"\g<1>00000", system, count=1)
        return value, count > 0
    if isinstance(system, list):
        result: list[object] = []
        for block in system:
            if not changed and isinstance(block, dict) and isinstance(block.get("text"), str):
                text, count = _BILLING_CCH.subn(r"\g<1>00000", block["text"], count=1)
                if count:
                    block = {**block, "text": text}
                    changed = True
            result.append(block)
        return result, changed
    return system, False


def stabilize_deepseek_cache_prefix(body: bytes, content_type: str) -> tuple[bytes, bool]:
    """Return JSON with only Claude Code's volatile billing hash stabilised."""
    if "json" not in content_type.lower() or not body:
        return body, False
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, False
    if not isinstance(value, dict) or "system" not in value:
        return body, False
    system, changed = _stabilize_system_value(value["system"])
    if not changed:
        return body, False
    value["system"] = system
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), True


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not value.get("target") or not isinstance(value.get("headers"), dict):
        raise ValueError("adapter config requires target and headers")
    return value


async def run(config_path: Path, host: str, port: int) -> None:
    config = load_config(config_path)
    target = str(config["target"]).rstrip("/")
    static_headers = {str(k): str(v) for k, v in config["headers"].items()}
    timeout = ClientTimeout(total=None, connect=30, sock_read=None)
    client = ClientSession(timeout=timeout)
    controller = OursV3Controller(config, client) if config.get("variant") == "ours_v3" else None

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "target": target,
                                  "variant": config.get("variant", "native-baseline")})

    async def ours_status(_: web.Request) -> web.Response:
        if controller is None:
            raise web.HTTPNotFound()
        return web.json_response(controller.state)

    async def ours_flush(_: web.Request) -> web.Response:
        if controller is None:
            raise web.HTTPNotFound()
        return web.json_response(await controller.flush())

    request_count = 0
    stabilized_count = 0

    async def forward(request: web.Request) -> web.StreamResponse:
        nonlocal request_count, stabilized_count
        request_count += 1
        session_id = request.match_info["session_id"]
        tail = request.match_info["tail"]
        if not session_id or "|" in session_id:
            raise web.HTTPBadRequest(text="invalid session_id")
        # Emit a compact access line so session-adapter.stdout.log proves the
        # adapter is actually receiving traffic (diagnostic for the --repo-sessions
        # routing fix). Flush immediately so a hung downstream can't hide it.
        print(f"[adapter] #{request_count} session={session_id} {request.method} /{tail}",
              flush=True)
        headers = {
            key: value for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        headers.update(static_headers)
        headers["X-Session-Id"] = session_id
        body = await request.read()
        body, stabilized = stabilize_deepseek_cache_prefix(
            body, request.headers.get("content-type", "")
        )
        if stabilized:
            stabilized_count += 1
            print(
                f"[adapter-cache] stabilized_cch={stabilized_count} session={session_id}",
                flush=True,
            )
        if controller is not None and "json" in request.headers.get("content-type", "").lower():
            try:
                json_body = json.loads(body)
                if isinstance(json_body, dict):
                    json_body = await controller.process(session_id, json_body)
                    body = json.dumps(json_body, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        upstream = await client.request(
            request.method,
            f"{target}/{tail}",
            params=request.query,
            headers=headers,
            data=body,
        )
        response_headers = {
            key: value for key, value in upstream.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        response = web.StreamResponse(
            status=upstream.status,
            reason=upstream.reason,
            headers=response_headers,
        )
        await response.prepare(request)
        async for chunk in upstream.content.iter_any():
            await response.write(chunk)
        await response.write_eof()
        upstream.release()
        return response

    app = web.Application(client_max_size=64 * 1024**2)
    app.router.add_get("/health", health)
    app.router.add_get("/ours/status", ours_status)
    app.router.add_post("/ours/flush", ours_flush)
    app.router.add_route("*", "/session/{session_id}/{tail:.*}", forward)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    try:
        await asyncio.Event().wait()
    finally:
        await client.close()
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.config, args.host, args.port))


if __name__ == "__main__":
    main()
