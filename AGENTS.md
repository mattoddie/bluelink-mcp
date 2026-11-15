# AGENTS.md

Notes for AI coding agents working on bluelink-mcp, an MCP server for Hyundai Bluelink and Kia Connect. [CONTRIBUTING.md](CONTRIBUTING.md) is the full contributor guide and [docs/development.md](docs/development.md) explains the architecture. Read them before larger changes.

## Commands

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run python scripts/gen_tool_docs.py           # regenerate docs/tools.md after changing tools (--check in CI)
uv run python scripts/mcpb.py manifest --check   # the bundle manifest matches the code
uv run python scripts/registry.py check          # server.json matches the manifest
```

## Layout

- `src/bluelink_mcp/`: the server (`server.py`) and the car API client (`client.py`)
- `tests/`: the tests
- `docs/`: the user docs. `website/` (the site at bluelink-mcp.mattoddie.dev) copies them in at build time, so edit `docs/`, not the website.

## Rules

- The server is read-only by default. Every tool that changes something needs the right access level: see "Adding or changing tools" in CONTRIBUTING.md.
- Don't add dependencies without asking.
- Never put real credentials, VINs, locations or other personal data in code, tests or fixtures. Use obviously fake values.
- Add a line under **Unreleased** in CHANGELOG.md for anything users will notice. Tool names, arguments, prompts and configuration variables are the public API, so mark changes to them **Breaking:**.
- Commit messages and pull request titles use Conventional Commits. Pull requests are squash-merged.
