# Optional dashboard

The integration creates standard Home Assistant entities and does not install or modify Lovelace dashboards.

`dashboard-native.yaml` is an optional starting point built only with native Home Assistant cards. It contains:

- the water-heater control;
- fan-mode selection;
- online, running, and problem states;
- ambient, inlet, and outlet temperatures;
- a 24-hour temperature graph;
- rolling activity metrics;
- the five read-only schedule slots.

## Add it to Home Assistant

1. Install and configure **Tholz Fairland**.
2. Open the integration's device page and note the entity IDs created for your device.
3. Create or open a dashboard.
4. Enter edit mode and open the raw configuration editor.
5. Copy the contents of `dashboard-native.yaml` as a new view or use its cards as a reference.
6. Replace any entity IDs that differ in your installation.

Entity IDs depend on the display name selected during setup and may receive a numeric suffix if similarly named entities already exist.

## Design policy

- The example is optional and never overwrites an existing dashboard.
- It has no dependency on Mushroom, card-mod, or another custom card.
- Schedule entities are displayed as read-only.
- The running binary sensor represents physical operation; it is not a power button.
- Mode and target-temperature changes act on the physical heat pump. Review the selected entity before using a control.
- Diagnostics are intentionally separated from the daily controls.

Screenshots will be added only from a sanitized demonstration dashboard with account, device, location, and infrastructure identifiers removed.
