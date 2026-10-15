# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For bluelink-mcp, the "public API" is the set of tool names, their arguments and the configuration variables. Renaming or removing any of these is a breaking change.

## [Unreleased]

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

[Unreleased]: https://github.com/mattoddie/bluelink-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/mattoddie/bluelink-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mattoddie/bluelink-mcp/releases/tag/v0.1.0
