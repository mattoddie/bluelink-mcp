"""Entry point: `bluelink-mcp` or `python -m bluelink_mcp`."""

from __future__ import annotations

import hmac
import logging
import sys

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .client import check_supported
from .config import Config, ConfigError
from .server import build_server


class BearerAuth:
    """Require `Authorization: Bearer <token>` on everything except /health."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/health":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def transport_security(config: Config) -> TransportSecuritySettings | None:
    """DNS rebinding protection, when MCP_ALLOWED_HOSTS is set."""
    if not config.allowed_hosts:
        return None
    # With protection on, a request with an Origin header must match an allowed origin.
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(config.allowed_hosts),
        allowed_origins=list(config.allowed_origins),
    )


def main() -> None:
    # stdout carries the protocol in stdio mode, so logs go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = Config.from_env()
        check_supported(config)
    except ConfigError as ex:
        sys.exit(f"bluelink-mcp: {ex}")

    logging.getLogger("bluelink_mcp").info(
        "bluelink-mcp %s (%s, writes %s, unlock %s)",
        __version__,
        config.transport,
        "on" if config.allow_writes else "off",
        "on" if config.allow_unlock else "off",
    )
    mcp = build_server(config)
    if config.transport == "stdio":
        mcp.run("stdio")
        return

    if not config.auth_token:
        sys.exit("bluelink-mcp: set MCP_AUTH_TOKEN when using the http transport")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = mcp.streamable_http_app(
        transport_security=transport_security(config), host=config.http_host, stateless_http=True
    )

    import uvicorn

    uvicorn.run(BearerAuth(app, config.auth_token), host=config.http_host, port=config.http_port)


if __name__ == "__main__":
    main()
