# Compatibility

bluelink-mcp talks to the car makers' services through [`hyundai_kia_connect_api`](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api), the library behind the Home Assistant Kia/Hyundai integration. What works depends on that library, your region and your car.

- [Brands](#brands)
- [Regions and brands](#regions-and-brands)
- [Features by region](#features-by-region)
- [Vehicles](#vehicles)
- [Known gaps](#known-gaps)

## Brands

| Brand (`BLUELINK_BRAND`) | App and service | Regions |
|---|---|---|
| Hyundai (`hyundai`, the default) | Bluelink | All except New Zealand |
| Kia (`kia`) | Kia Connect, formerly UVO | All except Brazil (China is untested) |
| Genesis (`genesis`) | Genesis Connect | Europe, USA, Canada and Australia |

The project, its settings and these docs say "Bluelink", but they mean the Kia and Genesis services too. A Kia works the same way: set `BLUELINK_BRAND=kia` and use your Kia Connect email, password and PIN.

## Regions and brands

| Region (`BLUELINK_REGION`) | Brands | Notes |
|---|---|---|
| Europe (`europe`), including the UK | Hyundai, Kia, Genesis | The classic Bluelink / Kia Connect / Genesis Connect Europe service |
| Europe CCI (`europe-cci`) | Hyundai, Kia | Hyundai's and Kia's newer EU backend. Genesis isn't supported. |
| USA (`usa`) | Hyundai, Kia, Genesis | |
| Canada (`canada`) | Hyundai, Kia, Genesis | Needs the PIN for commands |
| Australia (`australia`) | Hyundai, Kia, Genesis | |
| New Zealand (`nz`) | Kia only | |
| India (`india`) | Hyundai, Kia | |
| China (`china`) | Hyundai | Rewritten in 2026; Kia is untested |
| Brazil (`brazil`) | Hyundai | |

The library is maintained by volunteers, and coverage changes between releases. If your combination doesn't work, check the [library's issues](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/issues).

## Features by region

Status, location, live refresh and the basic commands (lock, climate, charging) work in every region. Some extras don't:

- **Trips** (`get_trips`) work in Europe and a few other regions.
- **Daily energy use** (`get_energy_usage`) is reported by EVs and plug-in hybrids, mainly in Europe.
- **Charge limits** are for EVs. Most plug-in hybrids don't support them.

When a region or car doesn't support something, the tool returns the library's error, or an empty result, and the assistant tells you.

## Vehicles

Any car that works in your region's Bluelink, Kia Connect or Genesis Connect app should work. The car's equipment decides which fields appear in the status: a petrol car has no EV section, and a car without tyre pressure sensors has no `tyres`. Fields the car doesn't report are left out rather than shown as empty.

Plug-in hybrids, such as the Santa Fe, Tucson and Kia Sorento/Sportage PHEVs, report both EV and fuel data. Climate runs from the high-voltage battery. Charge limits generally aren't supported.

### Tested

| Vehicle | Year | Region | Notes |
|---|---|---|---|
| | | | Please [send a compatibility report](https://github.com/mattoddie/bluelink-mcp/issues/new?template=compatibility.yml) |

## Known gaps

- **Charging schedules and departure times** are shown in the status but can't be changed yet.
- **Windows, charge port, valet mode and navigation** aren't exposed, although the library supports some of them on some cars.
- **One-time passcodes**: if the service asks for an OTP at login (some US Kia accounts), the server can't complete it. Log in through the app once, then try again.
- The Bluelink, Kia Connect and Genesis Connect APIs are unofficial and undocumented. Hyundai and Kia can change them at any time, and fixes usually arrive in the library first. Keep the image up to date.
