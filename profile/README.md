<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/those-who-run/.github/main/assets/banner-dark.svg">
  <img src="https://raw.githubusercontent.com/those-who-run/.github/main/assets/banner-light.svg" alt="走り屋 Those Who Run" width="100%">
</picture>

# Those Who Run 走り屋

A self-hosted voice race engineer for Gran Turismo 7. It listens to the game's telemetry, speaks lap times, fuel, position and pit calls into a Discord voice channel (so they reach a PSVR2 headset), posts a results card after each race, and serves a pit wall web app for looking back at sessions.

## v1, in use today

- **[those-who-run](https://github.com/those-who-run/those-who-run)**: the engineer, the pit wall and its web app. It stays the engineer in use until v2 replaces it, and is in maintenance from 1.0.0.
- **[outings](https://github.com/those-who-run/outings)**: recorded drives the tests rely on, for v1 and v2.

## v2, being built

The same engineer, rebuilt as components that talk over NATS, each in its own repository. All of v2's issues, milestones and decisions are in `paddock`; a component's repository is created when its first issue starts.

| Repository | What |
|---|---|
| [paddock](https://github.com/those-who-run/paddock) | The issues, milestones, roadmap and decisions for every component, and the deployment base |
| `contracts` | The messages the components exchange |
| `salsa` | The GT7 telemetry decoder |
| `ecu` | Race state and the built-in rules |
| `logbook` | The store: laps and sessions |
| `radio` | Voice: callouts spoken in Discord |
| `pitwall` | The pit wall web app and its API |
| `dash` | The live dashboard |

Not affiliated with or endorsed by Sony Interactive Entertainment, Polyphony Digital or Discord.
