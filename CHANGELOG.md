# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For bluelink-mcp, the "public API" is the set of tool names, their arguments and the configuration variables. Renaming or removing any of these is a breaking change.

## [Unreleased]

This release follows a security review. **It has breaking changes**; check the first section before upgrading.

### Breaking changes

- **Location is off by default.** `get_vehicle_location` is only registered with `BLUELINK_ALLOW_LOCATION=true`. Add it to keep asking where the car is.
- **`BLUELINK_GEOCODE=true` needs `BLUELINK_ALLOW_LOCATION=true`**, or the server refuses to start.
- **Coordinates are rounded** to `BLUELINK_LOCATION_PRECISION` decimal places, 4 by default (about 10 m). Set a higher value for more precision.
- **`unlock_vehicle` needs confirmation.** The server asks the user through MCP elicitation when the client supports it. Otherwise the call fails until the assistant passes the new `confirm` argument with the car's name or the last 6 characters of its VIN.
- **Commands are rate limited**: the same command can't be sent to the same car again within `BLUELINK_MIN_COMMAND_SECONDS` (default 60).
- **`MCP_AUTH_TOKEN` must be at least 16 characters** in HTTP mode, or the server refuses to start.
- **`BLUELINK_MIN_REFRESH_MINUTES` must be at least 1.**
- **`/mcp` accepts only `POST`** with a JSON body of up to 4 MiB (`405`, `400` or `413` otherwise).
- **With `MCP_ALLOWED_HOSTS` set**, an `Origin` header that is empty or `null` is now rejected, and origins on an allowed host (on its default port, or the port listed) are accepted without being listed in `MCP_ALLOWED_ORIGINS`.

### Added

- `BLUELINK_ALLOW_LOCATION` and `BLUELINK_LOCATION_PRECISION`.
- `BLUELINK_MIN_COMMAND_SECONDS` (default 60): minimum gap before the same command goes to the same car again. Failed attempts count, as they may still have reached the car.
- `BLUELINK_PASSWORD_FILE`, `BLUELINK_PIN_FILE` and `MCP_AUTH_TOKEN_FILE`, to read secrets from files such as Docker secrets.
- `LOG_LEVEL` (`debug`, `info`, `warning`, `error`; default `info`). The troubleshooting docs already told users to turn on debug logging, which wasn't possible.
- `bluelink-mcp healthcheck`, used by the image's `HEALTHCHECK`. It checks `MCP_HTTP_HOST` and `MCP_HTTP_PORT`, using loopback when the server listens on all addresses.
- CI audits every locked dependency with `pip-audit`.

### Changed

- `refresh_vehicle_status` is no longer annotated read-only, since it wakes the car, so clients ask before running it.
- The `Bearer` scheme in `Authorization` is matched case-insensitively, and a `401` response carries `WWW-Authenticate: Bearer`.
- `MCP_ALLOWED_HOSTS` entries without a port match any port in the `Host` header.
- The Docker base images are pinned by digest.

### Fixed

- After Bluelink asked for a one-time passcode, every tool call made a new full password login, which risks getting the account locked. The server now waits `BLUELINK_LOGIN_RETRY_MINUTES` before trying again, as after a rejected login.
- Only a rejected login (or a passcode request) starts the login wait. Network errors no longer lock the server out for five minutes, and get their own message.

### Security

- The token cache no longer stores the Bluelink password and PIN. Caches written by older versions are rewritten without them on the next request.
- The Bluelink library's logging, which includes whole responses with tokens, the VIN and the location (some at error level), is only shown with `LOG_LEVEL=debug`.
- `unlock_vehicle` is confirmed on the server side, commands are rate limited, and location is opt-in and rounded (see above).
- Host and Origin checks follow the same rules as the other mattoddie MCP servers, and reject empty, `null` and malformed origins.
- The security docs now call out car nicknames, OpenStreetMap addresses and Bluelink's warning texts as untrusted input.

## [0.1.3] - 2026-10-16

### Changed

- The README, docs, website and server instructions now say that bluelink-mcp supports Kia Connect (formerly UVO) as well as Hyundai Bluelink, and Genesis Connect in some regions. This already worked with `BLUELINK_BRAND=kia` or `genesis`; only the wording was Hyundai-only. The compatibility page has a new table of brands by region, and lists Genesis in Australia, which the library supports. Names, settings and tools are unchanged.

## [0.1.2] - 2026-10-15

### Added

- `MCP_ALLOWED_ORIGINS`, an `Origin` allow-list for HTTP. Before, setting `MCP_ALLOWED_HOSTS` made every request with an `Origin` header fail with 403.
- `BLUELINK_LOGIN_RETRY_MINUTES` (default 5): how long to wait after a failed login before trying again.

### Fixed

- In the USA, `start_climate` now sends the temperature in °F (62-82), as Bluelink USA expects. It was sent as °C, so 21 °C asked for 21 °F (Kia: the lowest setting). The tool still takes °C.
- Waiting for a command no longer blocks every other tool. The server polls for the car's answer without holding its lock, for up to about 60 s instead of 90 s, and returns `pending` with the `action_id` if the car hasn't answered.
- `fuel_range` in the vehicle status now includes its unit.
- A blank `vehicle` argument is treated as not given, instead of matching an arbitrary car.
- A corrupt or unexpected token cache is ignored with a warning instead of breaking every request, and an unsupported region and brand combination is reported at startup.
- Concurrent server processes no longer share a temporary file when writing the token cache.

### Security

- The token cache's copy of the password and PIN is always replaced with the configured ones. After a password change, the library's fallback login used the stale password from the cache on every request, which risked getting the account locked.
- After a failed login, the server deletes the token cache and doesn't try to log in again for `BLUELINK_LOGIN_RETRY_MINUTES`.
- A failed `refresh_vehicle_status` now counts against its rate limit, so failures can't wake the car repeatedly.
- Errors from the Bluelink library are reported as fixed messages with the error type, instead of passing through text that could include parts of Bluelink's responses. Details are logged at debug level.
- With several cars on the account, `unlock_vehicle` needs the exact name, VIN or ID; part of a name no longer matches.
- The documentation now says that the token cache contains the PIN as well as the password.

## [0.1.1] - 2026-10-15

### Changed

- The Docker image now runs on Python 3.14, and CI tests on Python 3.12, 3.13 and 3.14.

## [0.1.0] - 2026-10-15

### Added

- MCP server for Hyundai Bluelink (and Kia Connect and Genesis Connect), built on `hyundai_kia_connect_api`, with 15 tools: vehicle status, live refresh, location, trips, daily energy use, command status, lock and unlock, climate, start/stop charging, charge limits and hazard lights.
- Three access levels: read-only by default, `BLUELINK_ALLOW_WRITES` for commands and `BLUELINK_ALLOW_UNLOCK` for unlocking.
- Regions Europe (classic and CCI), USA, Canada, Australia, New Zealand, India, China and Brazil, and the brands Hyundai, Kia and Genesis.
- A cached login token (`BLUELINK_TOKEN_FILE`) to avoid repeated logins, a short status cache (`BLUELINK_CACHE_SECONDS`) and a per-car rate limit on waking the car (`BLUELINK_MIN_REFRESH_MINUTES`).
- Commands that wait for the car to confirm, or return an action ID to check later.
- Streamable HTTP (stateless) and stdio transports, with bearer-token auth and a `Host` header allow-list for HTTP.
- Multi-arch Docker image (`linux/amd64`, `linux/arm64`) published to GHCR on each release, with SBOM and provenance attestation.
- Documentation and website, including a tool reference generated from the code.

[Unreleased]: https://github.com/mattoddie/bluelink-mcp/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/mattoddie/bluelink-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/mattoddie/bluelink-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/mattoddie/bluelink-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mattoddie/bluelink-mcp/releases/tag/v0.1.0
