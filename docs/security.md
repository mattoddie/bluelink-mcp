# Security

bluelink-mcp can find, lock, unlock and pre-condition a real car. This page explains the risks and how the server limits them, so you can choose a setup you're comfortable with.

- [What's at stake](#whats-at-stake)
- [Layers of protection](#layers-of-protection)
- [Prompt injection](#prompt-injection)
- [Recommended setups](#recommended-setups)
- [Checklist](#checklist)

## What's at stake

| Asset | Risk if exposed |
|---|---|
| Your Bluelink or Kia Connect password and PIN | Full control of the car through the official app, from anywhere |
| The token cache (`token.json`) | A working Bluelink session until it expires (it doesn't hold your password or PIN) |
| The car's location | Where you live, work and park |
| Remote commands | Unlocking the car; draining the battery with climate; stopping a charge you needed |
| `MCP_AUTH_TOKEN` | Whatever tools the server has registered |

## Layers of protection

1. **Read-only by default.** Command tools aren't registered unless `BLUELINK_ALLOW_WRITES=true`. A tool that isn't registered can't be called, whatever the model is told.
2. **Unlock has its own switch.** `unlock_vehicle` also needs `BLUELINK_ALLOW_UNLOCK=true`. Locking, climate and charging are useful day to day, and unlocking rarely is, so most people should leave it off.
3. **Tool annotations.** Tools are marked read-only, or destructive for unlock, so clients that support annotations, such as Claude Code, ask before running commands unless you've allowed them.
4. **Bearer token for HTTP.** The server won't start in HTTP mode without `MCP_AUTH_TOKEN`, and the token is compared in constant time. Only `/health` is open.
5. **DNS rebinding protection.** Set `MCP_ALLOWED_HOSTS` to reject requests with unexpected `Host` headers, and `MCP_ALLOWED_ORIGINS` to list the browser origins allowed to call the server; other origins are rejected.
6. **Rate-limited wake-ups.** `refresh_vehicle_status` can't be called in a loop to drain the 12V battery.
7. **Secrets stay out of output.** Tools never return the password, PIN or tokens, and configuration is redacted when printed. Errors from Bluelink are reported as fixed messages with the error type, not the raw response; details go to the log at debug level. The token cache is written with mode `0600` and doesn't contain the password or PIN.
8. **No login loops.** After Bluelink rejects a login or asks for a one-time passcode, the server discards the token cache and doesn't try again for `BLUELINK_LOGIN_RETRY_MINUTES`, so a wrong or changed password can't get the account locked.
9. **Unlock picks the car exactly.** With several cars on the account, `unlock_vehicle` needs the exact name, VIN or ID; part of a name isn't enough.

## Prompt injection

An AI assistant can be tricked by text it reads, such as a web page, an email or a file, into calling tools you didn't ask for. bluelink-mcp's tools return only data from Bluelink, but your assistant may have other tools that read untrusted content in the same conversation.

To limit the impact:

- Keep commands off if you only want to ask questions.
- Keep unlock off.
- Don't auto-approve command tools in your client. Let it ask each time.
- Use a separate Bluelink or Kia Connect account shared from the app, so you can revoke its access without changing your own password.

## Recommended setups

| You want to | Settings |
|---|---|
| Ask about the car | Defaults. Nothing else needed. |
| Pre-heat and manage charging | `BLUELINK_ALLOW_WRITES=true` |
| Everything, including unlock | `BLUELINK_ALLOW_WRITES=true`, `BLUELINK_ALLOW_UNLOCK=true`, stdio or a VPN only, and approve each call |

## Checklist

- [ ] `.env` and `token.json` (which holds your session tokens) are readable only by you
- [ ] `MCP_AUTH_TOKEN` is long and random (`openssl rand -hex 32`)
- [ ] The HTTP port isn't reachable from the internet, or is behind TLS
- [ ] `BLUELINK_ALLOW_UNLOCK` is off unless you need it
- [ ] Your client asks before running command tools
- [ ] You know how to change your Bluelink or Kia Connect password if something leaks

To report a vulnerability in bluelink-mcp, see the [security policy](../SECURITY.md).
