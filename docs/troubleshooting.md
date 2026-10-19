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
| `BLUELINK_BRAND=... is not supported in BLUELINK_REGION=...` | The library has no service for that brand in that region. Check the [compatibility table](compatibility.md). |
| `MCP_ALLOWED_ORIGINS needs MCP_ALLOWED_HOSTS too` | Origins are only checked together with hosts. Set `MCP_ALLOWED_HOSTS` as well. |
| `set MCP_AUTH_TOKEN when using the http transport` | Generate one with `openssl rand -hex 32` and add it to `.env`. |

## Login fails

The tool returns *"Could not log in to Bluelink: ..."*, with a short reason and the error type, such as `(AuthenticationError)`. For the full error, run the server with debug logging.

After Bluelink rejects a login or asks for a one-time passcode, the server waits `BLUELINK_LOGIN_RETRY_MINUTES` (5) before trying again, and until then tools answer *"Logging in to Bluelink failed ... min ago"*. This protects the account from being locked by repeated attempts. Restarting the server clears the wait. Network errors (*"Could not reach Bluelink to log in"*) don't start the wait, because Bluelink never saw the attempt.

1. **Check the app.** Log out of the official app and back in with the same email and password. If the app asks you to accept new terms, verify your email or set a new password, do that, then try again.
2. **Check the region.** It must match where the account was created. In Europe, if `europe` keeps failing, try `europe-cci`, which is the backend some newer Hyundais and Kias use. For a Kia, also check `BLUELINK_BRAND=kia`: a Kia Connect login won't work against Hyundai's service.
3. **Too many attempts.** Bluelink temporarily blocks accounts after repeated logins, and the app may then fail too. Wait an hour, make sure the token cache is working (see below), and try again.
4. **Changed password.** Update `BLUELINK_PASSWORD` (and `BLUELINK_PIN` if that changed) and restart the server. The cache always uses the configured password, and is deleted automatically when a login is rejected. To clear it by hand: in Docker, `docker compose exec bluelink-mcp rm /data/token.json`; otherwise `rm ~/.local/state/bluelink-mcp/token.json`.
5. **One-time passcode.** *"Bluelink asked for a one-time passcode"* means the account needs an OTP, which the server can't enter. Log in once in the app on a trusted device, then try again. The server then waits `BLUELINK_LOGIN_RETRY_MINUTES` before trying again, like after any rejected login.

If logins worked before and stopped for everyone at the same time, Hyundai or Kia has probably changed its API. Update to the latest image. If that doesn't help, check the [library's issues](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/issues).

## Status looks old or wrong

- **Check `last_updated_at`.** That's when the car last uploaded its state. If the car has been parked for days, it may not have reported in.
- **Refresh.** Ask the assistant to refresh from the car. If it says to wait, that's the [rate limit](usage.md#cached-and-live-data) protecting the 12V battery.
- **Missing sections.** Fields the car doesn't report are left out. A car without tyre pressure sensors has no `tyres`, for example.
- **Range units.** Units come from your account settings in the app (km or miles).

## Commands fail or time out

| Result | What to check |
|---|---|
| `failed` | The car refused. Climate: a door, the bonnet or the boot is open, or the battery is too low. Charging: the cable isn't connected, or a schedule or charge limit is in the way. |
| `timeout` | Bluelink gave up waiting for the car, often because of poor mobile signal in a car park. The command may still have happened, so check the status in a minute before retrying. |
| `pending` | The car hadn't answered within about 60 seconds. Ask the assistant to check the command later; it has the `action_id`. |
| PIN errors | Set `BLUELINK_PIN` to the PIN you use in the app. |
| *"No vehicle matches"* when unlocking | With several cars, `unlock_vehicle` needs the car's exact name, VIN or ID. |
| *"Unknown tool"* or the tool isn't listed | Command tools are only registered with `BLUELINK_ALLOW_WRITES=true`, and unlock with `BLUELINK_ALLOW_UNLOCK=true`. Restart the server and reconnect the client after changing settings. |

## Client connection problems

| Symptom | Fix |
|---|---|
| `401 unauthorized` | The client isn't sending `Authorization: Bearer <MCP_AUTH_TOKEN>`, or the token doesn't match. |
| `421` or *"Invalid Host header"* | The request's `Host` isn't in `MCP_ALLOWED_HOSTS`. Add it, including the port if it isn't 80 or 443. |
| `403` or *"Invalid Origin header"* | The client sends an `Origin` header that isn't in `MCP_ALLOWED_ORIGINS`. Add it, such as `https://claude.ai`. |
| Connection refused | Check the container is running and healthy: `docker compose ps`, and `curl http://<docker-host>:8080/health` should print `ok`. |
| stdio server exits immediately | Run the same command in a terminal to see the error. For Docker, make sure you pass `-i` and not `-t`. |
