# Configuration

bluelink-mcp is configured entirely with environment variables. With Docker Compose, put them in `.env` (start from [`example.env`](../example.env)). With uv, pass them from your MCP client's config.

- [Bluelink account](#bluelink-account)
- [Choosing a region](#choosing-a-region)
- [Access levels](#access-levels)
- [Data freshness](#data-freshness)
- [Token cache](#token-cache)
- [MCP transport](#mcp-transport)
- [Example configs](#example-configs)

## Bluelink account

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_USERNAME` | (required) | The email address you log in to the Bluelink app with |
| `BLUELINK_PASSWORD` | (required) | Your Bluelink password |
| `BLUELINK_PIN` | | Your 4-digit Bluelink PIN. Europe and Canada need it for remote commands. Reads work without it. |
| `BLUELINK_REGION` | `europe` | Which Bluelink service to use. See [Choosing a region](#choosing-a-region). |
| `BLUELINK_BRAND` | `hyundai` | `hyundai`, `kia` or `genesis` |
| `BLUELINK_LANGUAGE` | `en` | Language for Bluelink's responses (Europe only), such as `en`, `de`, `fr`, `nl` |
| `BLUELINK_GEOCODE` | `false` | Look up a street address for the car's location with OpenStreetMap. This sends the car's coordinates to OpenStreetMap's Nominatim service. |

## Choosing a region

| `BLUELINK_REGION` | Service |
|---|---|
| `europe` | Bluelink Europe, including the UK. Start here in Europe. |
| `europe-cci` | Hyundai's newer EU backend, used by the newer app on some recent cars. Try this if `europe` fails. |
| `usa` | Bluelink USA |
| `canada` | Bluelink Canada |
| `australia` | Bluelink Australia |
| `nz` | New Zealand (Kia only) |
| `india` | India |
| `china` | China |
| `brazil` | Brazil |

The region must match where your account was created, not where the car is now. See [Compatibility](compatibility.md) for what each region supports.

## Access levels

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_ALLOW_WRITES` | `false` | Register the command tools: lock, climate, charging, charge limits and hazard lights |
| `BLUELINK_ALLOW_UNLOCK` | `false` | Also register `unlock_vehicle`. Needs `BLUELINK_ALLOW_WRITES=true`; the server refuses to start otherwise. |

Tools that aren't allowed are **not registered at all**, so the assistant can't see them, let alone call them. The [tool reference](tools.md) shows which setting each tool needs.

## Data freshness

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_CACHE_SECONDS` | `60` | How long a status read from Bluelink is reused before asking again. This saves API calls when the assistant asks for status and location one after the other. |
| `BLUELINK_MIN_REFRESH_MINUTES` | `10` | Minimum time between `refresh_vehicle_status` calls for each car. Waking the car uses its 12V battery and counts against the daily API limit. |

See [Cached and live data](usage.md#cached-and-live-data) for what these mean in practice.

## Token cache

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_TOKEN_FILE` | `$XDG_STATE_HOME/bluelink-mcp/token.json` (usually `~/.local/state/bluelink-mcp/token.json`); `/data/token.json` in the Docker image | Where to cache the Bluelink login. Set it to an empty value to turn caching off. |

Bluelink can temporarily block accounts that log in too often, so the server saves its session and reuses it until it expires, then refreshes it. The file is written with mode `0600`. It contains session tokens and, because the library needs it to log in again, your password, so protect it like a password. A cache written for a different username is ignored.

## MCP transport

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio`; `http` in the Docker image | `stdio` or `http` (Streamable HTTP) |
| `MCP_HTTP_HOST` | `127.0.0.1`; `0.0.0.0` in the Docker image | Address to listen on |
| `MCP_HTTP_PORT` | `8080` | Port to listen on |
| `MCP_AUTH_TOKEN` | | Bearer token clients must send. **Required** for `http`; the server refuses to start without it. Generate one with `openssl rand -hex 32`. |
| `MCP_ALLOWED_HOSTS` | | Optional comma-separated allow-list of `Host` header values, for DNS rebinding protection, such as `bluelink.example.com,192.168.1.20:8080` |

The HTTP server serves the MCP endpoint at `/mcp` and an unauthenticated health check at `/health`.

## Example configs

**Read-only, UK Hyundai, Docker:**

```sh
BLUELINK_USERNAME=you@example.com
BLUELINK_PASSWORD=your-password
BLUELINK_PIN=1234
MCP_AUTH_TOKEN=3f9c...   # openssl rand -hex 32
```

**Commands on, US Hyundai, with addresses:**

```sh
BLUELINK_USERNAME=you@example.com
BLUELINK_PASSWORD=your-password
BLUELINK_PIN=1234
BLUELINK_REGION=usa
BLUELINK_GEOCODE=true
BLUELINK_ALLOW_WRITES=true
```

**Kia in Europe:**

```sh
BLUELINK_BRAND=kia
BLUELINK_REGION=europe
```
