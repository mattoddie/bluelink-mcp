# Development

This guide explains how bluelink-mcp is put together and how to change it. For how to contribute, see the [contributing guide](../CONTRIBUTING.md).

- [Setup](#setup)
- [Architecture](#architecture)
- [Adding a tool](#adding-a-tool)
- [Testing](#testing)
- [Trying it against a real car](#trying-it-against-a-real-car)
- [The website](#the-website)

## Setup

You need **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**. Docker is optional.

```sh
git clone https://github.com/<you>/bluelink-mcp.git
cd bluelink-mcp
uv sync
uv run pytest
```

Checks that CI runs:

```sh
uv run pytest                                  # tests
uv run ruff check . && uv run ruff format --check .
uv run python scripts/gen_tool_docs.py --check # docs/tools.md matches the code
docker build .                                 # optional: tests run inside the build too
```

## Architecture

```text
src/bluelink_mcp/
├── __main__.py   entry point: config, stdio or HTTP, bearer auth, /health
├── config.py     environment variables → a frozen Config
├── client.py     BluelinkClient: session, token cache, rate limits, commands
├── format.py     library objects → compact JSON for the model
└── server.py     the MCP tools
scripts/
└── gen_tool_docs.py   writes docs/tools.md from the tool definitions
tests/
└── test_server.py     tests against a fake VehicleManager
```

How a request flows:

1. The MCP SDK (`MCPServer`, from the official [`mcp`](https://pypi.org/project/mcp/) package) calls a tool function in `server.py`.
2. The tool calls `BluelinkClient` in a worker thread, because `hyundai_kia_connect_api` is synchronous.
3. `BluelinkClient.call()` takes a lock, makes sure there's a valid session (loading the cached token, logging in or refreshing as needed), runs the library call and saves the token if it changed.
4. The tool formats the result with `format.py`, which drops fields the car doesn't report.

Errors meant for the model are raised as `BluelinkError`, a subclass of the SDK's `ToolError`, so the model sees the message. Any other exception reaches the model only as a generic error, with the details in the server log.

## Adding a tool

1. **Check the library.** Find the `VehicleManager` method and see which regions implement it.
2. **Add the tool** in `server.py` with `@mcp.tool(title=..., annotations=...)`:
   - Use `READ` for reads, `WRITE` for commands and `SENSITIVE` for anything with security impact.
   - **Register command tools only inside the `allow_writes` section.** This gating is the project's main safety feature, so reviewers check it carefully.
   - Take `vehicle: VehicleArg = None`, and `wait: WaitArg = True` for commands.
   - Send commands through `client.command(...)`, which resolves the vehicle, clears the cache and waits for the result.
   - Describe every argument with `Field(description=...)`, and write the docstring for the model: what it does, side effects and units.
3. **Add it to a group** in `scripts/gen_tool_docs.py`. The script fails if a tool is in no group.
4. **Test it** in `tests/test_server.py`, adding the method to `FakeManager`.
5. **Regenerate the docs:** `uv run python scripts/gen_tool_docs.py`.
6. **Add a changelog entry** under **Unreleased**.

## Testing

The tests never touch Bluelink. `FakeManager` stands in for `VehicleManager`: it has one car with some fields set, records every call, and returns canned action IDs and statuses. `make()` builds a server with a temporary token file, and `call()` runs a tool through the real MCP tool machinery, so argument validation and error handling are tested too.

## Trying it against a real car

Use the MCP Inspector:

```sh
BLUELINK_USERNAME=... BLUELINK_PASSWORD=... BLUELINK_PIN=... \
  npx @modelcontextprotocol/inspector uv run bluelink-mcp
```

Go easy on `refresh_vehicle_status` and commands while developing. Every refresh wakes the car, and Bluelink limits API calls per day.

To see the library's requests, set Python logging to debug for `hyundai_kia_connect_api`. Never paste that output publicly: it contains tokens and your car's VIN and location.

## The website

The site at [bluelink-mcp.mattoddie.dev](https://bluelink-mcp.mattoddie.dev) is in `website/`. It's built with Astro and Starlight from the Markdown in `docs/`, so edit the docs here and the site follows. See [`website/README.md`](../website/README.md).
