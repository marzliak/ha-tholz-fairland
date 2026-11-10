# Automations (optional)

The examples are in [`examples/automations.yaml`](../../examples/automations.yaml) and [`examples/scripts.yaml`](../../examples/scripts.yaml). **No example is active by default.** You decide which to copy, which to change and which to enable.

> The examples only **notify**, except one manual script that changes the target temperature and runs only when you run it. No example controls circulation water, switches the device on or off on a timetable, creates schedules, or makes consumption decisions.

## Principles

- **Explicit opt-in:** you copy the block, adjust the IDs and create the automation. Until then, nothing happens.
- **No circulation pump control:** the integration does not expose a circulation pump and no example commands one.
- **No schedules:** the device schedules are read-only. The examples neither create nor change timetables.
- **No energy promises:** 24-hour values are calculated by the integration from manufacturer activity records and do not measure consumption.
- **Limits respected:** for type 7, Automatic allows **12–40 °C**, Heat **18–40 °C**, Cool **12–30 °C**, and Off retains **12–40 °C** without turning on when only the temperature is set. Reported limits can narrow the range; the value must also respect the step, without silent rounding ([full rules](operation.md#temperature-limits-and-step)).
- **Real commands:** every write action affects the physical device.

## How to add an automation

1. Go to **Settings → Automations & scenes → Create automation → Create new automation**.
2. Choose **Edit in YAML** and paste the block you want.
3. Change the `entity_id` values to your actual IDs.
4. Save. Enable it only after testing.
5. To test a notification, use **Run actions** from the automation's menu, where available.

For the script, copy the block from `scripts.yaml` into your Home Assistant `scripts.yaml`, or create a script under **Settings → Automations & scenes → Scripts → Create script → Edit in YAML**.

## Available examples

### 1. Reported problem warning (notification)

Notifies when the **Problem** sensor stays on for 5 minutes.

- Trigger: `binary_sensor.fairland_problem` changes to `on` for 5 minutes.
- Action: creates a persistent notification in Home Assistant that includes the current error code.
- It does not attempt any correction. Check the manufacturer's app.

### 2. Device offline (notification)

Notifies when the **Online** sensor stays off for 15 minutes.

- Trigger: `binary_sensor.fairland_online` changes to `off` for 15 minutes.
- Action: persistent notification.
- A shorter delay produces false alarms during brief cloud outages. Choose the delay for your situation.

### 3. Manual script: set target temperature

A script that **runs only when you run it**. The example has a general 12 to 40 °C check, but that **does not guarantee acceptance**: the integration validates the mode, limits and step against fresh state before sending. A previously accepted automation may now receive an error; it should not change the mode to bypass that validation.

- Field: `temperature` (the example selector spans 12 to 40 with a 1 °C step; it does not replace the integration's effective policy).
- Action: `water_heater.set_temperature` on the heater.
- No automatic trigger. Confirm the value before running it.
- Setting only the temperature does not turn on a device in Off mode. A mode-only change does not add a new target temperature.

## Notifications: choose a channel

The examples use a **persistent notification**, which appears in the Home Assistant interface and needs no external app or service. For a phone notification, replace the `persistent_notification.create` block with a `notify.<your_device>` action from the Home Assistant app. That channel requires the app installed and linked.

## Cautions

- **Do not copy automations from others without review.** Check every `entity_id` and every action.
- **Do not put the temperature script inside an automation** without a clear condition. Repeating commands in a loop can produce cloud traffic and unexpected physical changes.
- **Failed commands** can leave the device unavailable even when a restore was attempted. Check the device physically.
- Automations **cannot** bypass the automatic-login limits ([Configuration](configuration.md#automatic-session-recovery)).

## Next steps

- [Dashboard](dashboard.md): where to place the control cards.
- [Troubleshooting](troubleshooting.md): when an alert fires without an apparent cause.
