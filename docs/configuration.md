# Configuration

bluelink-mcp is configured entirely with environment variables. With Docker Compose, put them in `.env` (start from [`example.env`](../example.env)). With uv, pass them from your MCP client's config.

- [Your account](#your-account)
- [Choosing a region](#choosing-a-region)
- [Access levels](#access-levels)
- [Data freshness](#data-freshness)
- [Token cache](#token-cache)
- [MCP transport](#mcp-transport)
- [Example configs](#example-configs)

## Your account

The variables all start with `BLUELINK_`, whatever the brand. For a Kia, use your Kia Connect login and set `BLUELINK_BRAND=kia`.

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_USERNAME` | (required) | The email address you log in to the Bluelink, Kia Connect or Genesis Connect app with |
| `BLUELINK_PASSWORD` | (required) | Your app password |
| `BLUELINK_PIN` | | Your 4-digit app PIN. Europe and Canada need it for remote commands. Reads work without it. |
| `BLUELINK_REGION` | `europe` | Which regional service to use. See [Choosing a region](#choosing-a-region). |
| `BLUELINK_BRAND` | `hyundai` | Which car maker's service to use: `hyundai` (Bluelink), `kia` (Kia Connect, formerly UVO) or `genesis` (Genesis Connect) |
| `BLUELINK_LANGUAGE` | `en` | Language for the service's responses (Europe only), such as `en`, `de`, `fr`, `nl` |
| `BLUELINK_GEOCODE` | `false` | Look up a street address for the car's location with OpenStreetMap. This sends the car's coordinates to OpenStreetMap's Nominatim service. |

## Choosing a region

| `BLUELINK_REGION` | Service |
|---|---|
| `europe` | Bluelink, Kia Connect and Genesis Connect Europe, including the UK. Start here in Europe. |
| `europe-cci` | Hyundai's and Kia's newer EU backend, used by the newer apps on some recent cars. Try this if `europe` fails. |
| `usa` | USA |
| `canada` | Canada |
| `australia` | Australia |
| `nz` | New Zealand (Kia only) |
| `india` | India |
| `china` | China |
| `brazil` | Brazil (Hyundai only) |

The region must match where your account was created, not where the car is now. A brand the region doesn't support (such as `genesis` with `europe-cci`, `hyundai` with `nz` or `kia` with `brazil`) stops the server at startup. See [Compatibility](compatibility.md) for what each region supports.

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
| `BLUELINK_LOGIN_RETRY_MINUTES` | `5` | After a failed login, how long to wait before trying again. Until then, tools return an error without contacting Bluelink. `0` turns the wait off. |

Bluelink can temporarily block accounts that log in too often, so the server saves its session and reuses it until it expires, then refreshes it. The file is written with mode `0600`. It contains session tokens and, because the library needs them to log in again, your **password and PIN**, so protect it like a password. A cache written for a different username, or one that can't be read, is ignored.

The password and PIN in the cache are always replaced with the ones you configured, so after changing your password you only need to update `BLUELINK_PASSWORD` and restart. If a login is rejected, the server deletes the cache and waits `BLUELINK_LOGIN_RETRY_MINUTES` before logging in again, so a wrong password can't lock the account by retrying on every request.

## MCP transport

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio`; `http` in the Docker image | `stdio` or `http` (Streamable HTTP) |
| `MCP_HTTP_HOST` | `127.0.0.1`; `0.0.0.0` in the Docker image | Address to listen on |
| `MCP_HTTP_PORT` | `8080` | Port to listen on |
| `MCP_AUTH_TOKEN` | | Bearer token clients must send. **Required** for `http`; the server refuses to start without it. Generate one with `openssl rand -hex 32`. |
| `MCP_ALLOWED_HOSTS` | | Optional comma-separated allow-list of `Host` header values, for DNS rebinding protection, such as `bluelink.example.com,192.168.1.20:8080` |
| `MCP_ALLOWED_ORIGINS` | | Optional comma-separated allow-list of `Origin` header values, such as `https://claude.ai`. Needs `MCP_ALLOWED_HOSTS`. |

The HTTP server serves the MCP endpoint at `/mcp` and an unauthenticated health check at `/health`.

With `MCP_ALLOWED_HOSTS` set, requests that carry an `Origin` header (browser-based clients do; command-line and desktop clients usually don't) are rejected with `403` unless the origin is in `MCP_ALLOWED_ORIGINS`. A port wildcard such as `http://localhost:*` is allowed in both lists.

## Example configs

**Read-only, UK Hyundai, Docker (the defaults):**

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

**Kia in the UK or Europe, with commands:**

```sh
BLUELINK_USERNAME=you@example.com   # your Kia Connect login
BLUELINK_PASSWORD=your-password
BLUELINK_PIN=1234
BLUELINK_BRAND=kia
BLUELINK_REGION=europe              # or europe-cci if login fails
BLUELINK_ALLOW_WRITES=true
```

**Kia in the USA:**

```sh
BLUELINK_USERNAME=you@example.com
BLUELINK_PASSWORD=your-password
BLUELINK_PIN=1234
BLUELINK_BRAND=kia
BLUELINK_REGION=usa
```
