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
| `BLUELINK_PASSWORD` | (required) | Your app password. Or put it in a file and set `BLUELINK_PASSWORD_FILE` to its path. |
| `BLUELINK_PIN` | | Your 4-digit app PIN. Europe and Canada need it for remote commands. Reads work without it. Or use `BLUELINK_PIN_FILE`. |
| `BLUELINK_REGION` | `europe` | Which regional service to use. See [Choosing a region](#choosing-a-region). |
| `BLUELINK_BRAND` | `hyundai` | Which car maker's service to use: `hyundai` (Bluelink), `kia` (Kia Connect, formerly UVO) or `genesis` (Genesis Connect) |
| `BLUELINK_LANGUAGE` | `en` | Language for the service's responses (Europe only), such as `en`, `de`, `fr`, `nl` |
| `BLUELINK_GEOCODE` | `false` | Look up a street address for the car's location with OpenStreetMap. This sends the car's full coordinates to OpenStreetMap's Nominatim service. Needs `BLUELINK_ALLOW_LOCATION=true`; the server refuses to start otherwise. |

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
| `BLUELINK_ALLOW_LOCATION` | `false` | Register `get_vehicle_location`, which tells the assistant where the car is. Off by default, because the location reveals where you live, work and park. Other tools never return the location. |
| `BLUELINK_LOCATION_PRECISION` | `4` | How many decimal places of latitude and longitude `get_vehicle_location` returns, from `0` to `15`. `4` is about 10 m, enough to find the car; `3` is about 100 m, `2` about 1 km. The street address from `BLUELINK_GEOCODE` isn't rounded. |
| `BLUELINK_ALLOW_UNLOCK` | `false` | Also register `unlock_vehicle`. Needs `BLUELINK_ALLOW_WRITES=true`; the server refuses to start otherwise. Each unlock also needs your confirmation; see [Unlocking](usage.md#unlocking). |

Tools that aren't allowed are **not registered at all**, so the assistant can't see them, let alone call them. The [tool reference](tools.md) shows which setting each tool needs.

## Data freshness

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_CACHE_SECONDS` | `60` | How long a status read from Bluelink is reused before asking again. This saves API calls when the assistant asks for status and location one after the other. |
| `BLUELINK_MIN_REFRESH_MINUTES` | `10` | Minimum time between `refresh_vehicle_status` calls for each car. Waking the car uses its 12V battery and counts against the daily API limit. At least `1`. |
| `BLUELINK_MIN_COMMAND_SECONDS` | `60` | Minimum time before the same command (such as `lock_vehicle`) can be sent to the same car again. A failed attempt counts too. `0` turns it off. |

See [Cached and live data](usage.md#cached-and-live-data) for what these mean in practice.

## Token cache

| Variable | Default | Description |
|---|---|---|
| `BLUELINK_TOKEN_FILE` | `$XDG_STATE_HOME/bluelink-mcp/token.json` (usually `~/.local/state/bluelink-mcp/token.json`); `/data/token.json` in the Docker image | Where to cache the Bluelink login. Set it to an empty value to turn caching off. |
| `BLUELINK_LOGIN_RETRY_MINUTES` | `5` | After Bluelink rejects a login (or asks for a one-time passcode), how long to wait before trying again. Network errors don't start the wait. Until then, tools return an error without contacting Bluelink. `0` turns the wait off. |

Bluelink can temporarily block accounts that log in too often, so the server saves its session and reuses it until it expires, then refreshes it. The file is written with mode `0600`. It contains session tokens (but not your password or PIN), which are enough to use your Bluelink account until they expire, so protect it like a password. A cache written for a different username, or one that can't be read, is ignored.

The server always uses the password and PIN you configured, so after changing your password you only need to update `BLUELINK_PASSWORD` and restart. A cache written by an older version that still holds the password and PIN is rewritten without them. If a login is rejected, the server deletes the cache and waits `BLUELINK_LOGIN_RETRY_MINUTES` before logging in again, so a wrong password can't lock the account by retrying on every request.

## MCP transport

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio`; `http` in the Docker image | `stdio` or `http` (Streamable HTTP) |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. Logs go to stderr. `debug` also shows the Bluelink library's logging, which includes tokens, the VIN and the location; see [Troubleshooting](troubleshooting.md). |
| `MCP_HTTP_HOST` | `127.0.0.1`; `0.0.0.0` in the Docker image | Address to listen on |
| `MCP_HTTP_PORT` | `8080` | Port to listen on |
| `MCP_AUTH_TOKEN` | | Bearer token clients must send. **Required** for `http`, and at least 16 characters; the server refuses to start otherwise. Generate one with `openssl rand -hex 32`. Or use `MCP_AUTH_TOKEN_FILE`. |
| `MCP_ALLOWED_HOSTS` | | Optional comma-separated allow-list of `Host` header values, for DNS rebinding protection, such as `bluelink.example.com,192.168.1.20:8080`. An entry without a port matches any port. |
| `MCP_ALLOWED_ORIGINS` | | Optional comma-separated list of extra `Origin` values browsers may call from, such as `https://claude.ai`. Needs `MCP_ALLOWED_HOSTS`. |

The HTTP server serves the MCP endpoint at `/mcp` (`POST` only, with a JSON body of up to 4 MiB) and an unauthenticated health check at `/health`. `bluelink-mcp healthcheck` exits 0 when `/health` answers, for Docker's `HEALTHCHECK`.

`BLUELINK_PASSWORD`, `BLUELINK_PIN` and `MCP_AUTH_TOKEN` can each be read from a file instead, such as a [Docker secret](https://docs.docker.com/compose/how-tos/use-secrets/): set `BLUELINK_PASSWORD_FILE`, `BLUELINK_PIN_FILE` or `MCP_AUTH_TOKEN_FILE` to its path. Set the variable or its `_FILE` version, not both.

With `MCP_ALLOWED_HOSTS` set, requests whose `Host` header isn't on the list are rejected with `421`. Requests that carry an `Origin` header (browser-based clients do; command-line and desktop clients usually don't) are rejected with `403` unless the origin is on an allowed host or listed in `MCP_ALLOWED_ORIGINS`. For origins, a host entry without a port allows only the default port (`https://bluelink.example.com`); list `host:port` for others. An empty or `null` origin is always rejected. A port wildcard such as `localhost:*` or `http://localhost:*` is allowed in both lists.

## Example configs

**Read-only, UK Hyundai, Docker (the defaults):**

```sh
BLUELINK_USERNAME=you@example.com
BLUELINK_PASSWORD=your-password
BLUELINK_PIN=1234
MCP_AUTH_TOKEN=3f9c...   # openssl rand -hex 32
```

**Commands on, US Hyundai, with location and addresses:**

```sh
BLUELINK_USERNAME=you@example.com
BLUELINK_PASSWORD=your-password
BLUELINK_PIN=1234
BLUELINK_REGION=usa
BLUELINK_ALLOW_LOCATION=true
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
