# Changelog

User-visible changes to Tholz Fairland for Home Assistant.

## 0.3.0 — prepared version, not publicly released

### Test infrastructure

- Add a separate Home Assistant Core 2026.11.1 / Python 3.14.9 test stack using the matching official `pytest-homeassistant` framework and Core constraints.
- Retain the Core 2025.2.4 and 2026.10.1 legacy test stacks, with framework-aware helper imports and the respx pytest-plugin option limited to the official stack.
- Keep this test-harness update separate from integration behavior, live-account checks and physical-equipment acceptance.

### Confirmation diagnostics

- Provide bounded, sanitized observations for command confirmation and automatic restoration without logging account credentials or complete device snapshots.
- Preserve command intent, confirmation criteria, timeout and the original command/restoration outcomes when diagnostic collection fails.

### Temperature validation

- Share one temperature policy between the water-heater entity and command transport: Automatic 12–40 °C, Heat 18–40 °C, and Cool 12–30 °C for the supported Fairland family.
- Preserve the legacy 12–40 °C range while Off without switching the equipment on; reject temperature requests for unknown modes or unsupported families.
- Restrict limits further when valid device bounds require it. Respect `stepSp` before `step`, keeping the existing 1 °C default only when both are absent; reject malformed capabilities and off-step requests instead of silently rounding.
- Revalidate temperature against a fresh state and the requested mode before publishing. Mode-only commands do not add or normalize a setpoint; confirmation and rollback safeguards remain in place.
- Send only the requested field from temperature, operation-mode and fan controls, so an older entity snapshot cannot replay unrelated settings.
- Validate the rollback values before the first publish, including the original mode's temperature limits and step. Invalid vendor data cannot be replayed as a restoration command.

### Read-only schedules and metrics documentation

- Show selected weekdays for all valid seven-day schedule masks while preserving the raw mask, slot identities, times, and read-only behavior. Add the `repeat_days` attribute.
- Correct Portuguese and English guides: the integration calculates rolling 24-hour active seconds and duty cycle from vendor activity records, with a five-minute cache and retention of the previous summary after non-authentication failures.

### Account setup

- Sign in with the e-mail/username and password used for the Tholz account.
- Discover compatible Fairland/TurboSilence heat pumps already associated with that account.
- Select which heat pump to add when more than one compatible device is found.
- Complete supported verification-code and new-password account challenges in the configuration assistant.
- Validate account access and a read-only device request before creating the configuration entry. Setup does not send an equipment command.

### Session recovery

- Renew stored cloud sessions and persist token rotation without recreating the device.
- Attempt automatic account sign-in when all stored refresh tokens are rejected.
- Request Home Assistant reauthentication when the account requires user action.
- Validate replacement credentials through **Reconfigure**, keeping the existing device/account identity.
- Migrate existing entries to the current configuration schema while preserving their identity.

### Equipment controls and information

- Water-heater entity with Off, Automatic, Heat, and Cool operation modes and target temperature.
- Silent / Intelligent fan-mode selection.
- Ambient, inlet, and outlet temperatures, running/online/problem indicators, and device error code.
- Heating active time and duty cycle for the previous rolling 24 hours.
- Five read-only equipment schedule slots, active-slot count, schedule version, and schedule update time when supplied by the cloud.
- Bounded command confirmation against equipment-reported state; failed convergence attempts to restore the previous baseline.

### Documentation and presentation

- Complete Portuguese and English installation, configuration, operation, dashboard, automation, privacy, troubleshooting, FAQ, and update guides.
- Illustrated onboarding, communication, command-confirmation, session-recovery, and entity-reference diagrams.
- Original integration artwork for light and dark themes and a contextual pool photograph with separate image credits.
- Existing dashboard screenshot, clearly identified as an example capture rather than current telemetry.
- Native dashboard and optional automation examples, with entity-ID adaptation and safety instructions.
- Local distribution packaging and automated checks for the prepared source and integration archives.
- Explicit attribution to Felippe Rodrigo Puhle and the original tholz-hass-integration project in both language READMEs, with the original MIT notice retained.

### Command and read reliability

- Send only the field requested by each temperature, operation-mode or fan action, preventing another setting from being replayed from an old entity snapshot.
- Validate command intent before transport and determine changes against a fresh baseline inside the client transaction; already-reported values require convergence without a duplicate write.
- Mark failed, invalid or expired reads unavailable, using a monotonic age limit; command acknowledgement does not refresh sensor-read validity.
- Keep confirmed control values across entity updates without renewing temperature-read validity, and refuse queued commands after the integration is unloaded.
- Reject malformed and non-finite temperatures while retaining valid zero readings.
- Adapt read-validation and command-state principles from upstream work by the original project and its contributors ([#93](https://github.com/felippepuhle/tholz-hass-integration/pull/93), [#96](https://github.com/felippepuhle/tholz-hass-integration/pull/96)); preserve the original MIT notice.

### Security and limitations

- The account password and reusable session credentials are retained in Home Assistant configuration-entry storage for unattended recovery. Password inputs are masked; storage and backups require protection.
- Integration error/log handling is designed to exclude credentials; broad third-party DEBUG request logging must remain disabled.
- Commands are limited to operating mode, target temperature, and fan mode. Schedules are read-only.
- The integration requires the vendor cloud; it does not add local control, an energy meter, or circulation-pump control.

No public tag or release has been created for this prepared version.
