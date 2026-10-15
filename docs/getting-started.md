# Getting started

This guide takes you from nothing to asking your assistant about your car. It takes about five minutes.

- [What you need](#what-you-need)
- [1. Check your Bluelink account](#1-check-your-bluelink-account)
- [2. Run the server](#2-run-the-server)
- [3. Ask your first questions](#3-ask-your-first-questions)
- [4. Turn on commands (optional)](#4-turn-on-commands-optional)
- [Next steps](#next-steps)

## What you need

- A **Hyundai with Bluelink** (or a Kia or Genesis with Kia Connect / Genesis Connect) and an account that works in the official app. See [Compatibility](compatibility.md) for regions.
- Your account **email, password and PIN**. The PIN is the 4-digit code the app asks for before remote commands.
- An MCP client such as [Claude Code](https://claude.com/claude-code) or Claude Desktop.
- Either **[uv](https://docs.astral.sh/uv/)** (to run it directly) or **Docker** (to run it as a service).

## 1. Check your Bluelink account

Open the Bluelink app and make sure you can see your car's status and send a command, such as locking it. If the app works, the server will too. If you've just changed your password or the app wants you to accept new terms, do that first, because the server can't click through those screens.

> [!TIP]
> Some people create a second Bluelink account just for automation and share the car with it from the app. That keeps your main login out of config files, and you can revoke it on its own.

## 2. Run the server

Choose one of the two options.

### Option A: uv (stdio, simplest)

Your MCP client starts the server when it needs it. Nothing listens on the network.

```sh
git clone https://github.com/mattoddie/bluelink-mcp
cd bluelink-mcp
claude mcp add bluelink \
  -e BLUELINK_USERNAME=you@example.com \
  -e BLUELINK_PASSWORD='your-password' \
  -e BLUELINK_PIN=1234 \
  -- uv run --directory "$PWD" bluelink-mcp
```

The default region is Europe and the brand Hyundai. For other regions, add `-e BLUELINK_REGION=usa` (or `canada`, `australia` and so on). See [Configuration](configuration.md#choosing-a-region).

### Option B: Docker (Streamable HTTP)

One long-running container that several clients and machines can share.

```sh
mkdir bluelink-mcp && cd bluelink-mcp
curl -fsSLO https://raw.githubusercontent.com/mattoddie/bluelink-mcp/main/compose.yaml
curl -fsSL https://raw.githubusercontent.com/mattoddie/bluelink-mcp/main/example.env -o .env
# Edit .env: set BLUELINK_USERNAME, BLUELINK_PASSWORD, BLUELINK_PIN
# and a random MCP_AUTH_TOKEN (openssl rand -hex 32)
docker compose up -d
```

Then connect Claude Code:

```sh
claude mcp add --transport http bluelink http://<docker-host>:8080/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

For Claude Desktop, VS Code, Cursor and others, see [Connecting MCP clients](clients.md).

## 3. Ask your first questions

Start Claude Code and try:

- *"What's the car's status?"*
- *"How much electric range do I have, and is it plugged in?"*
- *"Where did I park?"*
- *"How far did I drive this month?"*

The first request logs in to Bluelink, which takes a few seconds. After that the login token is cached (see [Configuration](configuration.md#token-cache)), so later requests are quick and don't log in again.

Answers come from the state the car last sent to Bluelink. The assistant tells you when that was. If you need it to be current, ask it to *"refresh from the car"*. That wakes the car, so it's [rate limited](usage.md#cached-and-live-data).

## 4. Turn on commands (optional)

Out of the box the assistant can look but not touch. To let it lock the car, pre-heat the cabin and start or stop charging, set:

```sh
BLUELINK_ALLOW_WRITES=true
```

With uv, add `-e BLUELINK_ALLOW_WRITES=true` to the `claude mcp add` command. With Docker, add it to `.env` and run `docker compose up -d`. Then try *"Warm the car up to 21 degrees for 15 minutes"*.

Unlocking is separate and needs `BLUELINK_ALLOW_UNLOCK=true` as well. Read [Security](security.md) before turning it on.

## Next steps

- [Using bluelink-mcp](usage.md): what to ask, and how commands and refreshes work
- [Configuration](configuration.md): every setting
- [Troubleshooting](troubleshooting.md): if login fails
