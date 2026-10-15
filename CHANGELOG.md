# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For bluelink-mcp, the "public API" is the set of tool names, their arguments and the configuration variables. Renaming or removing any of these is a breaking change.

## [Unreleased]

### Added

- MCP server for Hyundai Bluelink (and Kia Connect and Genesis Connect), built on `hyundai_kia_connect_api`, with 15 tools: vehicle status, live refresh, location, trips, daily energy use, command status, lock and unlock, climate, start/stop charging, charge limits and hazard lights.
- Three access levels: read-only by default, `BLUELINK_ALLOW_WRITES` for commands and `BLUELINK_ALLOW_UNLOCK` for unlocking.
- Regions Europe (classic and CCI), USA, Canada, Australia, New Zealand, India, China and Brazil, and the brands Hyundai, Kia and Genesis.
- A cached login token (`BLUELINK_TOKEN_FILE`) to avoid repeated logins, a short status cache (`BLUELINK_CACHE_SECONDS`) and a per-car rate limit on waking the car (`BLUELINK_MIN_REFRESH_MINUTES`).
- Commands that wait for the car to confirm, or return an action ID to check later.
- Streamable HTTP (stateless) and stdio transports, with bearer-token auth and a `Host` header allow-list for HTTP.
- Multi-arch Docker image (`linux/amd64`, `linux/arm64`) published to GHCR on each release, with SBOM and provenance attestation.
- Documentation and website, including a tool reference generated from the code.

[Unreleased]: https://github.com/mattoddie/bluelink-mcp/commits/main
