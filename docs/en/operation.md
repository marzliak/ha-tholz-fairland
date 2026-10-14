# Operation

> **Illustrative diagrams** of the architecture, entities and command lifecycle appear below. They are not screenshots.

![Illustrative architecture: Home Assistant, integration, manufacturer cloud and the heat pump](../images/architecture.png)

## How the integration works

- The integration is **cloud-only**. Home Assistant talks to the manufacturer's services, which communicate with the device.
- State is refreshed periodically (configurable interval). Entities read the latest value held in memory, so Home Assistant does not query the cloud once per entity.
- Commands follow a controlled cycle, described in [Command lifecycle](#command-lifecycle).

## Entities created

Names below use the prefix you chose during setup (shown here as `<name>`). The names are the ones the integration creates in this release: the heater, fan, temperature and indicator entities use **English**; the 24-hour metrics and schedules use **Portuguese**. Entity IDs derive from these names and receive a numeric suffix if an entity with the same name already exists. Examples in these guides use the default name `Fairland`, for example `water_heater.fairland_pool`.

![Illustrative entity map: which entities the integration creates and how they group](../images/entities.png)

| Entity (name) | Type | Access | Meaning |
|---|---|---|---|
| `<name> Pool` | Water heater | Read and write | Operation mode, target temperature and current temperature |
| `<name> Fan mode` | Select | Read and write | Fan mode: **Silent** or **Intelligent** |
| `<name> Ambient temperature` | Temperature sensor | Read | Reported ambient temperature |
| `<name> Inlet temperature` | Temperature sensor | Read | Water temperature at the device **inlet** |
| `<name> Outlet temperature` | Temperature sensor | Read | Water temperature at the device **outlet** |
| `<name> Heat pump running` | Binary sensor (running) | Read | Reported indicator that the device is operating |
| `<name> Online` | Binary sensor (connectivity) | Read | Connection reported by the cloud |
| `<name> Problem` | Binary sensor (problem) | Read | On when the error code is not zero |
| `<name> Error code` | Diagnostic sensor | Read | Raw error code reported by the device |
| `<name> Tempo Aquecendo 24h` | Duration sensor (seconds) | Read | Active time over the last 24 hours, per the cloud |
| `<name> Ciclo de Aquecimento 24h` | Sensor (%) | Read | Percentage of active time over the last 24 hours |
| `<name> Agenda 1` to `Agenda 5` | Sensor | Read | Schedule slot: `Ativa` or `Desativada`, with times |
| `<name> Agendas Ativas` | Sensor (count) | Read | How many schedule slots are active |
| `<name> Agenda Versão` | Diagnostic sensor | Read | Schedule version reported by the device |
| `<name> Agenda Atualizada` | Diagnostic sensor (timestamp) | Read | Last schedule update reported by the device |

Notes:

- Temperature sensors are created only if the device reports the value **when setup or reload runs**. If an entity is missing, see [Missing or unavailable entities](troubleshooting.md#missing-or-unavailable-entities).
- The schedule entities are also created at setup, when the device reports its schedule.
- **Online** and **Problem** exist only when the device reports those fields.
- Availability depends on the fields the device and cloud service report.

## Meaning of the temperature sensors

| Sensor | What it represents |
|---|---|
| Ambient temperature | Air temperature, as reported by the device |
| Inlet temperature | Water **entering** the device |
| Outlet temperature | Water **leaving** the device |

Important:

- The **current temperature** of the water heater entity (`<name> Pool`) is the **inlet** reading, not the outlet or ambient reading.
- In the example dashboard capture, the card labelled **Entrada / Piscina** shows the **inlet water** temperature. **It is not a separate pool probe.** If the pool water differs from the inlet reading, that is expected: the reading reflects the water passing through the device.
- The integration does not calibrate the sensors. Values are whatever the manufacturer reports.
- This candidate has not been checked against a physical probe layout. Confirm readings against the device or the manufacturer's app.

## Meaning of the indicators

| Indicator | On when | Limits |
|---|---|---|
| Heat pump running | The device reports its running flag | Shows the reported state. It is **not a power switch** and does not measure heating power. |
| Online | The cloud reports the device connected | If off, commands may fail. See [Troubleshooting](troubleshooting.md). |
| Problem | The error code is different from 0 | Does not interpret the code. Check the manufacturer's manual or app for meaning. |
| Error code | Always shows the reported number | Raw value, not translated. |

## 24-hour metrics

- **Tempo Aquecendo 24h** (seconds) and **Ciclo de Aquecimento 24h** (%) come from the manufacturer's metrics service.
- The percentage is calculated by the manufacturer's service; the integration does not compute it. Metrics are queried at most every **5 minutes**.
- They are **manufacturer-reported measures**, not energy measurement. The integration does **not** report energy consumption, kWh or cost.
- If the metrics service fails, the sensors may show no value until the next successful query.

## Schedules: read-only

- The schedules (`Agenda 1` to `Agenda 5`) are **read-only**. You can view them in Home Assistant but **cannot edit them** there.
- To change a schedule, use the manufacturer's app.
- State: `Ativa · HH:MM-HH:MM` or `Desativada · HH:MM-HH:MM`.
- Attributes per slot: `slot`, `active`, `turn_on`, `turn_off`, `repeat_mask`, `repeat_description`, `read_only: true`.
- Repeat mask: `127` means **every day**; `0` means **no day**. Other values appear as "Weekly mask N (days not yet mapped)" in the `repeat_description` attribute; check those days in the manufacturer's app.
- The integration does not create, change, enable or disable schedules.

## Commands

Three items are writable: **operation mode**, **target temperature** and **fan mode**. No other field is written.

| Command | Accepted values | Note |
|---|---|---|
| Operation mode | Off (`off`), Automatic (`automatic`), Heat (`heat`), Cool (`cold`) | Internal values. The project README calls the last mode "Cool"; see [FAQ](FAQ.md). |
| Target temperature | 12 to 40 °C | Out-of-range values are rejected before sending. |
| Fan mode | Silent, Intelligent | Two values only. |

Use the water heater card on the dashboard, the select entity, or Home Assistant's standard actions. **Each command changes the physical device.** Confirm the entity before using it.

### Command lifecycle

![Illustrative command lifecycle: baseline read, send, convergence, and return to baseline on failure](../images/command-lifecycle.png)

1. The integration **reads the current state** of the device and keeps the values it is about to change.
2. It sends **only** the field being changed.
3. It waits up to **45 seconds** for the reported state. Success requires the reported state to match the requested value **and** no pending change to remain.
4. If the device does not converge, the integration tries to **restore the previous value**.
5. If the restore also fails, the message reports that returning to the previous state failed. **Check the device physically.**

Practical consequences:

- The value shown in Home Assistant can take several seconds to change.
- If the device does not accept the command, the value returns to the previous one.
- A failed command can leave the device unavailable for a while, even after the restore attempt.

## Limits of this release

- **Cloud only.** Without the cloud there is no reading and no command. There is no local mode.
- **No schedule control.** Schedules are read-only.
- **Only the three commands listed above.** No other mode, time or function is supported.
- **No energy metrics.** Times and percentages come from the manufacturer and do not represent consumption.
- **Manufacturer dependency.** Changes to the service, the account or the interfaces can make entities unavailable.

## Next steps

- [Dashboard](dashboard.md): an example card set ready to adapt.
- [Automations](automations.md): optional alerts.
- [Troubleshooting](troubleshooting.md): when something does not match.
