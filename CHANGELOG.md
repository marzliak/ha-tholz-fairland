# Changelog

User-visible changes to Tholz Fairland for Home Assistant.

## 0.3.0 — prepared version, not publicly released

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

### Security and limitations

- The account password and reusable session credentials are retained in Home Assistant configuration-entry storage for unattended recovery. Password inputs are masked; storage and backups require protection.
- Integration error/log handling is designed to exclude credentials; broad third-party DEBUG request logging must remain disabled.
- Commands are limited to operating mode, target temperature, and fan mode. Schedules are read-only.
- The integration requires the vendor cloud; it does not add local control, an energy meter, or circulation-pump control.

No public tag or release has been created for this prepared version.
