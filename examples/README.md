# Examples

Optional, copy-and-adapt files for Tholz Fairland 0.3.0. Nothing in this folder is installed or enabled automatically.

Full guides (PT-BR): [`docs/dashboard.md`](../docs/dashboard.md) and [`docs/automations.md`](../docs/automations.md). English: [`docs/en/dashboard.md`](../docs/en/dashboard.md) and [`docs/en/automations.md`](../docs/en/automations.md).

## Files

| File | What it is | Enabled by default |
|---|---|---|
| `dashboard-native.yaml` | A native-card dashboard view (two views: Pool and Diagnostics). No custom cards. | No — paste it yourself |
| `automations.yaml` | Two notification-only automations (problem reported; device offline 15 min). | No — copy and enable yourself |
| `scripts.yaml` | One manual script that sets the target temperature (12–40 °C). It runs only when you run it. | No — copy and run yourself |

## Entity IDs

The examples assume the default name `Fairland`. The integration derives entity IDs from the name you choose during setup. If you used another name, replace the `fairland` prefix. Check the real IDs under **Developer tools → States**. If Home Assistant added a numeric suffix (for example `_2`), use the suffixed ID.

Entities referenced by the examples:

| Entity ID | Created by |
|---|---|
| `water_heater.fairland_pool` | Water heater ("<name> Pool") |
| `select.fairland_fan_mode` | Fan-mode select ("<name> Fan mode") |
| `sensor.fairland_inlet_temperature` | Inlet temperature ("<name> Inlet temperature") |
| `sensor.fairland_outlet_temperature` | Outlet temperature ("<name> Outlet temperature") |
| `sensor.fairland_ambient_temperature` | Ambient temperature ("<name> Ambient temperature") |
| `binary_sensor.fairland_heat_pump_running` | Heat pump running ("<name> Heat pump running") |
| `binary_sensor.fairland_online` | Online ("<name> Online") |
| `binary_sensor.fairland_problem` | Problem ("<name> Problem") |
| `sensor.fairland_error_code` | Error code ("<name> Error code") |
| `sensor.fairland_tempo_aquecendo_24h` | "<name> Tempo Aquecendo 24h" |
| `sensor.fairland_ciclo_de_aquecimento_24h` | "<name> Ciclo de Aquecimento 24h" |
| `sensor.fairland_agenda_1` … `sensor.fairland_agenda_5` | "<name> Agenda 1" … "Agenda 5" |
| `sensor.fairland_agendas_ativas` | "<name> Agendas Ativas" |
| `sensor.fairland_agenda_versao` | "<name> Agenda Versão" |
| `sensor.fairland_agenda_atualizada` | "<name> Agenda Atualizada" |

## Rules these examples follow

- No circulation-pump control.
- No schedule creation or editing (schedules are read-only in the integration).
- No energy or cost claims. The 24-hour values are manufacturer-reported.
- Temperature commands are limited to 12–40 °C.
- Automations only notify; the one command path is the manual script.

## Verification status

The YAML in this folder was parsed for syntax. It has **not yet been loaded into a running Home Assistant** for this release. Confirm it in your installation before relying on it.

## Water-heater card

The `thermostat` card used in `dashboard-native.yaml` is shown with a `water_heater` entity in the example. Compatibility of that combination was not verified against the Home Assistant documentation for this release; confirm it on your version.
