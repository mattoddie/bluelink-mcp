# Connecting MCP clients

bluelink-mcp supports both standard MCP transports:

| Transport | How it runs | Best for |
|---|---|---|
| **stdio** (the default outside Docker) | The client starts the server for each session, with `uv run` or `docker run -i`, or as the Claude Desktop extension. | A single machine, no network service. |
| **Streamable HTTP** (the default in the image) | One long-running container. Clients connect to `http://<host>:8080/mcp`. | Sharing one server between several clients and machines. |

The HTTP endpoint is **stateless**: each request is handled on its own, so you can restart the container at any time. Every request must send `Authorization: Bearer <token>`, where the token is your `MCP_AUTH_TOKEN`.

In the examples below, replace `<docker-host>` with the machine running the container and `<token>` with your `MCP_AUTH_TOKEN`. The server is called `bluelink` in every example.

- [Claude Code](#claude-code)
- [Claude Desktop](#claude-desktop)
- [VS Code (GitHub Copilot)](#vs-code-github-copilot)
- [Cursor](#cursor)
- [OpenAI Codex CLI](#openai-codex-cli)
- [Other clients](#other-clients)
- [stdio mode](#stdio-mode)

## Claude Code

Over HTTP:

```sh
claude mcp add --transport http bluelink http://<docker-host>:8080/mcp \
  --header "Authorization: Bearer <token>"
```

Or over stdio, from a clone of the repository:

```sh
claude mcp add bluelink \
  -e BLUELINK_USERNAME=you@example.com -e BLUELINK_PASSWORD='your-password' -e BLUELINK_PIN=1234 \
  -- uv run --directory /path/to/bluelink-mcp bluelink-mcp
```

Add `--scope user` to make it available in every project. Don't commit credentials: in a shared `.mcp.json`, reference environment variables instead:

```json
{
  "mcpServers": {
    "bluelink": {
      "type": "http",
      "url": "http://<docker-host>:8080/mcp",
      "headers": { "Authorization": "Bearer ${BLUELINK_MCP_TOKEN}" }
    }
  }
}
```

Run `claude mcp list` to check the connection, and `/mcp` inside Claude Code to see the tools.

## Claude Desktop

The easiest way is the **extension bundle**: a single `.mcpb` file that installs bluelink-mcp into Claude Desktop with a settings form. It needs no Docker, no service and no token, because Claude Desktop runs the server itself over stdio.

### Install the extension

1. Download `bluelink-mcp-<version>.mcpb` from the [latest release](https://github.com/mattoddie/bluelink-mcp/releases/latest). The same file works on macOS, Windows and Linux.
2. Double-click it, or drag it onto **Settings → Extensions** in Claude Desktop, and click **Install**.
3. Fill in the settings and turn the extension on. The tools appear under the tools (🔨) menu, where you can also allow or block each one.

The bundle uses Claude Desktop's uv runtime: on first start, Claude Desktop downloads a suitable Python and the exact dependency versions from the release's `uv.lock`, so the first start takes a minute and needs internet access. To update, install the bundle from a newer release the same way.

### Settings

| Setting | Variable | Notes |
|---|---|---|
| Email, Password | `BLUELINK_USERNAME`, `BLUELINK_PASSWORD` | Your app login. The password is marked sensitive, so Claude Desktop keeps it out of the plain settings. |
| PIN | `BLUELINK_PIN` | Optional. Europe and Canada need it for commands. |
| Region, Brand | `BLUELINK_REGION`, `BLUELINK_BRAND` | See [Choosing a region](configuration.md#choosing-a-region). Default `europe` and `hyundai`. |
| Allow commands | `BLUELINK_ALLOW_WRITES` | Lock, climate, charging, charge limits and hazard lights. Off by default. |
| Allow unlocking | `BLUELINK_ALLOW_UNLOCK` | Needs *Allow commands*. Each unlock also needs your confirmation; see [Unlocking](usage.md#unlocking). |
| Share location | `BLUELINK_ALLOW_LOCATION` | Registers `get_vehicle_location`. Off by default. |
| Audit log file | `BLUELINK_AUDIT_LOG` | Optional full path to a JSON Lines file (its folder must exist), or `stderr` for Claude Desktop's MCP log. See [Audit log](configuration.md#audit-log). |

Other settings keep their defaults, including the login token cache in `~/.local/state/bluelink-mcp/token.json`. The server's own log goes to Claude Desktop's MCP server logs.

### Without the extension

Claude Desktop also starts MCP servers listed in its config file over stdio. Open **Settings → Developer → Edit Config** and add one of these.

With uv:

```json
{
  "mcpServers": {
    "bluelink": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/bluelink-mcp", "bluelink-mcp"],
      "env": {
        "BLUELINK_USERNAME": "you@example.com",
        "BLUELINK_PASSWORD": "your-password",
        "BLUELINK_PIN": "1234"
      }
    }
  }
}
```

With Docker:

```json
{
  "mcpServers": {
    "bluelink": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "MCP_TRANSPORT=stdio",
        "-e", "BLUELINK_USERNAME", "-e", "BLUELINK_PASSWORD", "-e", "BLUELINK_PIN",
        "-v", "bluelink-data:/data",
        "ghcr.io/mattoddie/bluelink-mcp:latest"
      ],
      "env": {
        "BLUELINK_USERNAME": "you@example.com",
        "BLUELINK_PASSWORD": "your-password",
        "BLUELINK_PIN": "1234"
      }
    }
  }
}
```

The `bluelink-data` volume keeps the login token between sessions, so each new chat doesn't log in to Bluelink again. Restart Claude Desktop, and the tools appear under the tools (🔨) menu.

To turn on commands, add `"BLUELINK_ALLOW_WRITES": "true"` to `env` (and, for Docker, `"-e", "BLUELINK_ALLOW_WRITES"` to `args`).

## VS Code (GitHub Copilot)

Create `.vscode/mcp.json` in your workspace, or run **MCP: Add Server** from the command palette:

```json
{
  "inputs": [
    { "id": "bluelink-token", "type": "promptString", "description": "bluelink-mcp token", "password": true }
  ],
  "servers": {
    "bluelink": {
      "type": "http",
      "url": "http://<docker-host>:8080/mcp",
      "headers": { "Authorization": "Bearer ${input:bluelink-token}" }
    }
  }
}
```

VS Code asks for the token once and stores it securely. Use the tools from Copilot Chat in **Agent** mode.

## Cursor

Add to `~/.cursor/mcp.json` for all projects, or `.cursor/mcp.json` for one project:

```json
{
  "mcpServers": {
    "bluelink": {
      "url": "http://<docker-host>:8080/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## OpenAI Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.bluelink]
url = "http://<docker-host>:8080/mcp"
bearer_token_env_var = "BLUELINK_MCP_TOKEN"
```

Then `export BLUELINK_MCP_TOKEN=<token>` before running `codex`.

## Other clients

Any client that supports MCP **Streamable HTTP** works. Point it at `http://<docker-host>:8080/mcp` and send the bearer token header.

If your client only supports stdio, use [stdio mode](#stdio-mode), or keep the shared HTTP server and connect through a stdio-to-HTTP bridge such as [`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

```json
{
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://<docker-host>:8080/mcp", "--header", "Authorization: Bearer <token>"]
}
```

## stdio mode

In stdio mode the MCP client runs the server itself and talks to it over stdin/stdout. No port is opened, and no `MCP_AUTH_TOKEN` is needed because only the client can reach the server.

```sh
docker run -i --rm \
  -e MCP_TRANSPORT=stdio \
  -e BLUELINK_USERNAME=you@example.com \
  -e BLUELINK_PASSWORD='your-password' \
  -e BLUELINK_PIN=1234 \
  -v bluelink-data:/data \
  ghcr.io/mattoddie/bluelink-mcp:latest
```

Tips:

- **Keep secrets out of the client config.** Pass `-e NAME` without a value so Docker copies the variable from the client's environment, or use `--env-file /path/to/bluelink.env`.
- **Keep the token cache.** Mount a volume at `/data` (Docker) or leave `BLUELINK_TOKEN_FILE` at its default (uv). Without it, every session logs in from scratch, and frequent logins can get the account temporarily blocked.
- **Always pass `-i` and never `-t`.** A TTY corrupts the MCP message stream.
- **Pin a version** (for example `:1.2.3`) so a new `latest` doesn't surprise you. See [Deployment](deployment.md#image-tags).
