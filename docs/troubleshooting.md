# Troubleshooting

Start with the logs: `docker compose logs bluelink-mcp`, or your MCP client's server log for stdio (in Claude Code, `claude --debug`). Logs go to stderr and never include your password or tokens.

- [The server won't start](#the-server-wont-start)
- [Login fails](#login-fails)
- [Status looks old or wrong](#status-looks-old-or-wrong)
- [Commands fail or time out](#commands-fail-or-time-out)
- [Client connection problems](#client-connection-problems)

## The server won't start

| Message | Fix |
|---|---|
| `BLUELINK_USERNAME and BLUELINK_PASSWORD must be set` | Set both. With Docker, check `.env` is next to `compose.yaml` and has no quotes around values. |
| `BLUELINK_REGION must be one of ...` | Use one of the names in [Configuration](configuration.md#choosing-a-region), such as `europe` or `usa`. |
| `BLUELINK_ALLOW_UNLOCK needs BLUELINK_ALLOW_WRITES=true too` | Turn on writes as well, or turn unlock off. |
| `set MCP_AUTH_TOKEN when using the http transport` | Generate one with `openssl rand -hex 32` and add it to `.env`. |

## Login fails

The tool returns *"Could not log in to Bluelink: ..."*.

1. **Check the app.** Log out of the official app and back in with the same email and password. If the app asks you to accept new terms, verify your email or set a new password, do that, then try again.
2. **Check the region.** It must match where the account was created. In Europe, if `europe` keeps failing, try `europe-cci`, which is the backend some newer Hyundais use.
3. **Too many attempts.** Bluelink temporarily blocks accounts after repeated logins, and the app may then fail too. Wait an hour, make sure the token cache is working (see below), and try again.
4. **Token cache.** If you changed your password, the cached session may be stale. Delete `token.json`: in Docker, `docker compose exec bluelink-mcp rm /data/token.json`; otherwise `rm ~/.local/state/bluelink-mcp/token.json`.
5. **One-time passcode.** *"Bluelink asked for a one-time passcode"* means the account needs an OTP, which the server can't enter. Log in once in the app on a trusted device, then try again.

If logins worked before and stopped for everyone at the same time, Hyundai has probably changed its API. Update to the latest image. If that doesn't help, check the [library's issues](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/issues).

## Status looks old or wrong

- **Check `last_updated_at`.** That's when the car last uploaded its state. If the car has been parked for days, it may not have reported in.
- **Refresh.** Ask the assistant to refresh from the car. If it says to wait, that's the [rate limit](usage.md#cached-and-live-data) protecting the 12V battery.
- **Missing sections.** Fields the car doesn't report are left out. A car without tyre pressure sensors has no `tyres`, for example.
- **Range units.** Units come from your Bluelink account settings (km or miles).

## Commands fail or time out

| Result | What to check |
|---|---|
| `failed` | The car refused. Climate: a door, the bonnet or the boot is open, or the battery is too low. Charging: the cable isn't connected, or a schedule or charge limit is in the way. |
| `timeout` | The car didn't answer within about 90 seconds, often because of poor mobile signal in a car park. The command may still have happened, so check the status in a minute before retrying. |
| PIN errors | Set `BLUELINK_PIN` to the PIN you use in the app. |
| *"Unknown tool"* or the tool isn't listed | Command tools are only registered with `BLUELINK_ALLOW_WRITES=true`, and unlock with `BLUELINK_ALLOW_UNLOCK=true`. Restart the server and reconnect the client after changing settings. |

## Client connection problems

| Symptom | Fix |
|---|---|
| `401 unauthorized` | The client isn't sending `Authorization: Bearer <MCP_AUTH_TOKEN>`, or the token doesn't match. |
| `421` or *"Invalid Host header"* | The request's `Host` isn't in `MCP_ALLOWED_HOSTS`. Add it, including the port if it isn't 80 or 443. |
| Connection refused | Check the container is running and healthy: `docker compose ps`, and `curl http://<docker-host>:8080/health` should print `ok`. |
| stdio server exits immediately | Run the same command in a terminal to see the error. For Docker, make sure you pass `-i` and not `-t`. |
