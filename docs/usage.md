# Using bluelink-mcp

Ask about your car in plain language. The assistant picks the tools; you don't need to name them.

- [Example questions](#example-questions)
- [Cached and live data](#cached-and-live-data)
- [How commands work](#how-commands-work)
- [Climate options](#climate-options)
- [Charging](#charging)
- [Several cars](#several-cars)

## Example questions

**Status**

- *"Is the car locked?"*
- *"How much charge and fuel do I have? What's the total range?"*
- *"Is it plugged in, and when will it be full?"*
- *"Any warnings? Tyres, washer fluid, 12V battery?"*
- *"Did I leave a window open?"*

**Where and how far**

- *"Where's the car?"*
- *"How many miles did I drive last week, and what was my longest trip?"*
- *"Summarise my driving for September."*
- *"How much energy went on climate versus driving this week?"*

**Commands** (need `BLUELINK_ALLOW_WRITES=true`)

- *"Warm the car to 22 degrees with the windscreen defrost on for 15 minutes."*
- *"Heat the steering wheel and the driver's seat too."*
- *"Lock the car."*
- *"Start charging now."* / *"Stop charging."*
- *"Flash the lights, I can't find it in the car park."*

## Cached and live data

Your car doesn't stay online all the time. It uploads its state to Bluelink when you switch it off, while it charges, after a remote command, and every so often while parked. bluelink-mcp reads from Bluelink in two ways:

| | `get_vehicle_status` (and location, energy use) | `refresh_vehicle_status` |
|---|---|---|
| Source | The state the car last uploaded to Bluelink | The car itself, woken up over the mobile network |
| Speed | A second or two | 10 to 60 seconds |
| Cost | Nothing to the car | A little 12V battery, and a call from the daily API limit |
| Limits | Reused for `BLUELINK_CACHE_SECONDS` (60 s) | At most once per `BLUELINK_MIN_REFRESH_MINUTES` (10 min) per car |

Every status includes `last_updated_at`, so the assistant can tell you how old the data is and decide whether a refresh is worth it. The server's instructions tell it to prefer cached data.

> [!WARNING]
> Waking a car too often can flatten its 12V battery, especially in cold weather or if the car hasn't been driven for a while. Bluelink also limits API calls per day; Europe allows roughly 200. Keep `BLUELINK_MIN_REFRESH_MINUTES` at 10 or more.

## How commands work

A command goes from Bluelink to the car over the mobile network, and the car reports back. That usually takes 20 to 60 seconds, longer if the signal is poor.

By default (`wait: true`) the tool waits up to about 60 seconds for the car's answer and returns one of these statuses. Other tools keep working while it waits.

| Status | Meaning |
|---|---|
| `success` | The car confirmed the command. |
| `failed` | The car refused, usually because a condition wasn't met. For example, climate won't start with a door open or the battery too low, and charging won't start unless it's plugged in. |
| `timeout` | Bluelink gave up waiting for the car. The command may or may not have happened. Check with `get_vehicle_status` in a minute. |
| `pending` | The car hadn't answered after about 60 seconds, or you passed `wait: false`. Check later with `get_command_status` and the `action_id`. |
| `sent` | Some regions don't return an `action_id`, so there's nothing to track. |

After a command, the next status read goes back to Bluelink instead of using the cache.

## Climate options

`start_climate` takes:

- `temperature`: 14 to 30 °C, rounded to the nearest 0.5. Always in °C, in every region. Bluelink USA takes whole °F from 62 to 82, so in the USA the temperature is converted and kept within that range (21 °C is sent as 70 °F), and the result shows `temperature_sent`. In Canada, cars older than model year 2020 start at 16 °C.
- `duration_minutes`: 1 to 30. Default 10.
- `defrost`: front windscreen defrost.
- `heated_rear_window_and_mirrors` and `heated_steering_wheel`.
- Seat levels for each seat, such as `low_heat`, `high_heat` or `medium_cool`, on cars with heated or ventilated seats. Older Bluelink cars ignore seat settings.

Which options a car honours depends on its equipment and Bluelink generation. On plug-in hybrids and EVs, climate runs from the high-voltage battery, so it works with the car off. Many plug-in hybrids won't start climate below a minimum charge. On petrol and diesel cars with remote start, it runs the engine.

## Charging

- `start_charging` starts charging now. The car must be plugged in, and this overrides a charging schedule until it's unplugged.
- `stop_charging` stops it.
- `set_charge_limits` sets separate AC and DC limits in 10% steps from 50 to 100%. Most plug-in hybrids don't support limits and will report `failed`.

The status's `schedule` section shows the departure times and off-peak window set in the app.

## Several cars

If your account has more than one car, pass `vehicle` with its name (as shown in the app), VIN or ID. Part of the name works too, as long as only one car matches, except for `unlock_vehicle`, which needs the exact name, VIN or ID when there are several cars. With one car you can leave it out. Ask *"Which cars do I have?"* to see them.
