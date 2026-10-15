# Deployment

This page covers running bluelink-mcp as a long-running Docker service. For a single machine, [stdio mode](clients.md#stdio-mode) with uv or `docker run -i` is simpler and opens no port.

- [Docker Compose](#docker-compose)
- [The data volume](#the-data-volume)
- [Image tags](#image-tags)
- [Upgrading](#upgrading)
- [Reverse proxy with TLS](#reverse-proxy-with-tls)
- [Hardening](#hardening)
- [Running without Docker](#running-without-docker)

## Docker Compose

```sh
mkdir bluelink-mcp && cd bluelink-mcp
curl -fsSLO https://raw.githubusercontent.com/mattoddie/bluelink-mcp/main/compose.yaml
curl -fsSL https://raw.githubusercontent.com/mattoddie/bluelink-mcp/main/example.env -o .env
# Edit .env
docker compose up -d
docker compose logs -f
```

The [`compose.yaml`](../compose.yaml) runs the image from GHCR with `restart: unless-stopped`, publishes port 8080 and mounts a named volume at `/data`. To build from source instead, clone the repository and replace `image:` with `build: .`.

The image:

- runs as an unprivileged user (UID 1000)
- serves Streamable HTTP on port 8080, with `/mcp` for MCP and `/health` for health checks
- has a Docker `HEALTHCHECK` on `/health`
- is built for `linux/amd64` and `linux/arm64`, so it runs on a Raspberry Pi or NAS too

## The data volume

The volume at `/data` holds `token.json`, the cached Bluelink login. Keep it: without it, every restart logs in again, and frequent logins can get a Bluelink account temporarily blocked.

The file contains your session tokens and password. Don't back it up anywhere less safe than your `.env`.

## Image tags

| Tag | What it is |
|---|---|
| `X.Y.Z` | An exact release. Never changes. |
| `X.Y` | The latest patch of a minor version |
| `X` | The latest release of a major version (from `1.0.0`) |
| `latest` | The latest stable release |

Pin `X.Y.Z` or `X.Y` for predictable upgrades. Pre-releases (`1.2.0-rc.1`) are only published under their exact version.

## Upgrading

```sh
docker compose pull
docker compose up -d
```

Check the [changelog](../CHANGELOG.md) first. Because the HTTP endpoint is stateless, clients carry on after a restart without reconnecting.

## Reverse proxy with TLS

Bearer tokens and car commands shouldn't cross a network in plain text. If clients connect from other machines, put a TLS-terminating proxy in front and set `MCP_ALLOWED_HOSTS` to the public host name.

Caddy:

```caddyfile
bluelink.example.com {
    reverse_proxy bluelink-mcp:8080
}
```

nginx:

```nginx
location / {
    proxy_pass http://bluelink-mcp:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;          # MCP responses can stream
    proxy_read_timeout 180s;      # commands can wait ~90 s for the car
}
```

Then point clients at `https://bluelink.example.com/mcp`.

> [!CAUTION]
> Don't expose bluelink-mcp to the internet unless you have to. If you do, use TLS, a long random `MCP_AUTH_TOKEN` and, ideally, a VPN such as Tailscale or WireGuard instead of a public port.

## Hardening

- Keep `BLUELINK_ALLOW_UNLOCK` off unless you really need it. See [Security](security.md).
- Publish the port only on the interface you need, for example `127.0.0.1:8080:8080` behind a local proxy.
- Add `read_only: true` and `cap_drop: [ALL]` to the Compose service. The server writes only to `/data`.
- Keep `.env` readable only by you: `chmod 600 .env`.

## Running without Docker

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/mattoddie/bluelink-mcp && cd bluelink-mcp
uv sync --no-dev
MCP_TRANSPORT=http MCP_AUTH_TOKEN=... BLUELINK_USERNAME=... BLUELINK_PASSWORD=... uv run bluelink-mcp
```

Run it under systemd or another supervisor to keep it running.
