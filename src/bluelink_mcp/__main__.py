"""Entry point: `bluelink-mcp`, `bluelink-mcp healthcheck` or `python -m bluelink_mcp`."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .client import check_supported
from .config import Config, ConfigError, log_level
from .server import build_server

# The largest request body accepted on /mcp.
MAX_BODY_BYTES = 4 * 1024 * 1024

_BEARER = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)
_PORT = re.compile(r":(\d+|\*)$")


def _open_path(scope: Scope) -> bool:
    """Whether the request skips the checks: anything that isn't HTTP, and /health."""
    return scope["type"] != "http" or scope["path"] == "/health"


async def _rpc_error(
    scope: Scope, receive: Receive, send: Send, status: int, message: str, **headers
) -> None:
    body = {"jsonrpc": "2.0", "error": {"code": -32000, "message": message}, "id": None}
    await JSONResponse(body, status_code=status, headers=headers or None)(scope, receive, send)


def _header(scope: Scope, name: bytes) -> str | None:
    """The first value of a header, or None when it is absent (an empty value is kept)."""
    for key, value in scope["headers"]:
        if key == name:
            return value.decode("latin-1")
    return None


class BearerAuth:
    """Require `Authorization: Bearer <token>` (any case of "Bearer") on everything except /health."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.expected = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _open_path(scope):
            match = _BEARER.match(_header(scope, b"authorization") or "")
            presented = match.group(1).strip().encode("latin-1") if match else b""
            if not hmac.compare_digest(presented, self.expected):
                await _rpc_error(scope, receive, send, 401, "Unauthorized", **{"WWW-Authenticate": "Bearer"})
                return
        await self.app(scope, receive, send)


def _split_host(host: str) -> tuple[str, str | None]:
    """Split `host[:port]` (IPv6 in brackets) into the lower-case host name and the port."""
    host = host.strip().lower()
    match = _PORT.search(host)
    if match and (not host.startswith("[") or host[: match.start()].endswith("]")):
        return host[: match.start()], match.group(1)
    return host, None


def host_allowed(host: str | None, allowed: tuple[str, ...]) -> bool:
    """Whether a Host header matches the list. An entry without a port (or with `:*`) matches any port."""
    if not host:
        return False
    name, port = _split_host(host)
    for entry in allowed:
        entry_name, entry_port = _split_host(entry)
        if entry_name == name and (entry_port in (None, "*") or entry_port == port):
            return True
    return False


def origin_allowed(origin: str, allowed_hosts: tuple[str, ...], allowed_origins: tuple[str, ...]) -> bool:
    """Whether an Origin header may call the server.

    It must be an http(s) origin whose host is in MCP_ALLOWED_HOSTS or that is listed in
    MCP_ALLOWED_ORIGINS. For hosts, an entry without a port matches only the scheme's default
    port (`host:port` or `host:*` allows others). `null`, empty and malformed origins are refused.
    """
    try:
        url = urlsplit(origin.strip())
        port = url.port
    except ValueError:
        return False
    if url.scheme not in ("http", "https") or not url.hostname or url.path or url.query or url.username:
        return False
    hostname = url.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default = 443 if url.scheme == "https" else 80
    effective = str(port if port is not None else default)
    for entry in allowed_hosts:
        entry_name, entry_port = _split_host(entry)
        if entry_name == hostname and (
            entry_port == "*" or entry_port == effective or (entry_port is None and effective == str(default))
        ):
            return True
    normal = f"{url.scheme}://{hostname}" + ("" if effective == str(default) else f":{effective}")
    for entry in allowed_origins:
        entry = entry.strip().lower().rstrip("/")
        if entry == normal or (entry.endswith(":*") and entry[:-2] == f"{url.scheme}://{hostname}"):
            return True
    return False


class HostOriginCheck:
    """DNS rebinding protection: check Host and Origin against MCP_ALLOWED_HOSTS/ORIGINS."""

    def __init__(self, app: ASGIApp, allowed_hosts: tuple[str, ...], allowed_origins: tuple[str, ...]):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _open_path(scope):
            host = _header(scope, b"host")
            if not host_allowed(host, self.allowed_hosts):
                await _rpc_error(scope, receive, send, 421, f"Host {host!r} is not allowed")
                return
            # Browsers send Origin on cross-site requests; other clients usually don't.
            origin = _header(scope, b"origin")
            if origin is not None and not origin_allowed(origin, self.allowed_hosts, self.allowed_origins):
                await _rpc_error(scope, receive, send, 403, f"Origin {origin!r} is not allowed")
                return
        await self.app(scope, receive, send)


class PostJSONOnly:
    """/mcp takes only POST requests with a JSON body of at most MAX_BODY_BYTES.

    The server is stateless, so there is no server-initiated stream to GET and no session to DELETE.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _open_path(scope) or scope["path"] != "/mcp":
            await self.app(scope, receive, send)
            return
        if scope["method"] != "POST":
            await _rpc_error(scope, receive, send, 405, "Method not allowed", Allow="POST")
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await _rpc_error(scope, receive, send, 413, "Request body too large")
                return
            if not message.get("more_body", False):
                break
        if body.strip():
            try:
                json.loads(body)
            except ValueError:
                await _rpc_error(scope, receive, send, 400, "Invalid JSON body")
                return

        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def transport_security(config: Config) -> TransportSecuritySettings | None:
    """The SDK's own Host/Origin check, which HostOriginCheck replaces when MCP_ALLOWED_HOSTS is set.

    Without MCP_ALLOWED_HOSTS, None keeps the SDK's default: when listening on localhost, it
    accepts only localhost Host headers.
    """
    if not config.allowed_hosts:
        return None
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def http_app(config: Config, app: ASGIApp) -> ASGIApp:
    """Wrap the MCP app in the checks, outermost first: Host/Origin, bearer token, method and body."""
    app = BearerAuth(PostJSONOnly(app), config.auth_token)
    if config.allowed_hosts:
        app = HostOriginCheck(app, config.allowed_hosts, config.allowed_origins)
    return app


def healthcheck_url(env: dict[str, str]) -> str | None:
    """The /health URL to check, or None in stdio mode (nothing to check)."""
    if env.get("MCP_TRANSPORT", "stdio").strip().lower() != "http":
        return None
    host = env.get("MCP_HTTP_HOST", "").strip()
    port = env.get("MCP_HTTP_PORT", "").strip() or "8080"
    # A wildcard address isn't something to connect to; the server answers on loopback too.
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/health"


def healthcheck() -> int:
    """Exit status for Docker's HEALTHCHECK: 0 when /health answers 200."""
    url = healthcheck_url(dict(os.environ))
    if url is None:
        return 0
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - local URL built above
            return 0 if response.status == 200 else 1
    except (OSError, urllib.error.URLError) as ex:
        print(f"bluelink-mcp healthcheck: {url}: {ex}", file=sys.stderr)
        return 1


def main() -> None:
    if sys.argv[1:] == ["healthcheck"]:
        sys.exit(healthcheck())
    if sys.argv[1:]:
        sys.exit("usage: bluelink-mcp [healthcheck]")

    try:
        level, library_level = log_level(dict(os.environ))
    except ConfigError as ex:
        sys.exit(f"bluelink-mcp: {ex}")
    # stdout carries the protocol in stdio mode, so logs go to stderr.
    logging.basicConfig(level=level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("hyundai_kia_connect_api").setLevel(library_level)
    try:
        config = Config.from_env()
        check_supported(config)
    except ConfigError as ex:
        sys.exit(f"bluelink-mcp: {ex}")

    logging.getLogger("bluelink_mcp").info(
        "bluelink-mcp %s (%s, writes %s, unlock %s, location %s, audit log %s)",
        __version__,
        config.transport,
        "on" if config.allow_writes else "off",
        "on" if config.allow_unlock else "off",
        "on" if config.allow_location else "off",
        config.audit_log or "off",
    )
    mcp = build_server(config)
    if config.transport == "stdio":
        mcp.run("stdio")
        return

    if not config.auth_token:
        sys.exit("bluelink-mcp: set MCP_AUTH_TOKEN (or MCP_AUTH_TOKEN_FILE) when using the http transport")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = mcp.streamable_http_app(
        transport_security=transport_security(config), host=config.http_host, stateless_http=True
    )

    import uvicorn

    uvicorn.run(http_app(config, app), host=config.http_host, port=config.http_port)


if __name__ == "__main__":
    main()
