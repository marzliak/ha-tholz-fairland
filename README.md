# Tholz Fairland for Home Assistant

An unofficial Home Assistant custom integration for cloud-connected **Tholz Fairland / TurboSilence pool heat pumps**.

It presents the heat pump as a Home Assistant water heater and adds operating telemetry, fan-mode control, rolling activity metrics, and a read-only view of the configured schedule.

> This project is not affiliated with or endorsed by Tholz or Fairland. It depends on the vendor cloud and may stop working if that service or its interfaces change.

## Features

- Water-heater control:
  - Off
  - Automatic
  - Heat
  - Cool
  - target temperature from 12–40 °C
- Fan mode: Silent or Intelligent
- Read-only telemetry:
  - ambient, water inlet, and water outlet temperatures
  - running state, connection state, and device error code
  - heating active time and duty cycle over the previous 24 hours
  - five schedule slots plus schedule diagnostics
- Account sign-in with device discovery, MFA/new-password challenges, automatic token recovery, and Home Assistant reauthentication
- Safe command handling:
  - only operation mode, target temperature, and fan mode are writable
  - every command starts from a fresh reported-state baseline
  - success requires the reported state to match and the pending delta to clear
  - waits are bounded; failed convergence triggers an attempt to restore the baseline

The schedule is deliberately read-only.

## Requirements

- Home Assistant 2025.2.4 or newer
- A supported heat pump already linked to the Tholz cloud service
- The e-mail or username and password for that cloud account
- Internet access from Home Assistant to the vendor's cloud and AWS services

This integration supports only the cloud-connected Fairland/TurboSilence product family. Other Tholz controllers, lighting, auxiliary outputs, and switches are outside its scope. Installations of other Tholz integrations remain independent.

## Installation

### HACS

1. In HACS, add this repository as a custom repository in the **Integration** category.
2. Install **Tholz Fairland**.
3. Restart Home Assistant.

### Manual

1. Download a tagged release.
2. Copy `custom_components/tholz_fairland` into your Home Assistant `custom_components` directory.
3. Restart Home Assistant.

## Configuration

1. Open **Settings → Devices & services → Add integration**.
2. Search for **Tholz Fairland**.
3. Enter a display name, the account e-mail or username, and the account password.
4. Complete any verification-code or new-password challenge requested by the account.
5. If the account has more than one compatible heat pump, select the device to add.
6. Choose a polling interval from 10–300 seconds. The default is 30 seconds.

Setup signs in, discovers compatible devices, and validates a read-only state request before it creates the entry. It does not send a heat-pump command.

The device identifier is fixed after setup. Add another integration entry to manage another heat pump. The polling frequency can be changed under **Configure**, and credentials under **Reconfigure**. If the cloud rejects every stored token, the integration first attempts an unattended sign-in with the stored credentials. If the account requires user action or rejects the credentials, Home Assistant opens a reauthentication flow. Recovery reuses the existing entry, so devices, entities, dashboards, automations, and history are preserved.

## Entities

| Platform | Entity | Access |
| --- | --- | --- |
| Water heater | Pool heat pump | Read/write |
| Select | Fan mode | Read/write |
| Sensor | Ambient temperature | Read-only |
| Sensor | Inlet temperature | Read-only |
| Sensor | Outlet temperature | Read-only |
| Binary sensor | Heat pump running | Read-only |
| Binary sensor | Online | Read-only |
| Binary sensor | Problem | Read-only |
| Sensor | Error code | Read-only |
| Sensor | Heating active time, previous 24 hours | Read-only |
| Sensor | Heating duty cycle, previous 24 hours | Read-only |
| Sensors | Five schedule slots and diagnostics | Read-only |

Availability depends on fields reported by the device and cloud service.

## Dashboard preview

[![Home Assistant dashboard showing Fairland heat-pump control, pool temperatures, and rolling activity metrics](docs/images/dashboard-overview.png)](docs/images/dashboard-overview.png)

The screenshot shows one possible Home Assistant dashboard. Click the image to open it at full resolution.

## Optional dashboard

The integration works with Home Assistant's standard device page and does not install or modify dashboards. A native-card dashboard example is available in [`examples/dashboard-native.yaml`](examples/dashboard-native.yaml), with setup notes in [`examples/README.md`](examples/README.md).

The example is optional, uses no custom cards, and can be adapted to the entity IDs created by your installation.

## Privacy and security

Like established Home Assistant integrations that need reusable credentials, this integration stores the account username, password, refresh tokens, cloud user identifier, and device identifier in the config entry. This permits unattended session recovery. Home Assistant's password selector masks the value in the interface, but config-entry fields are not individually encrypted: `.storage` and backups that include the entry must be treated as sensitive.

The integration does not add a private encryption layer around config-entry fields. Such a layer would still need a runtime decryption key and would not improve the normal Home Assistant host-and-backup trust model. Protect the Home Assistant host, restrict access to its configuration directory, encrypt and control backups, and rotate the cloud password if either is exposed.

This integration never writes credentials, tokens, SRP values, `SECRET_HASH`, authorization headers, or Cognito challenge sessions to logs, diagnostics, or error messages, and it does not enable debug logging for any third-party library. Challenge sessions exist only in flow memory and are discarded when the flow ends.

One caveat outside the integration's control: the AWS SDK (`botocore`) logs full request and response contents — including tokens, new passwords, `SECRET_HASH`, and challenge sessions — at `DEBUG`. Do not set `DEBUG` logging for `botocore`, `boto3`, `urllib3`, or the root logger in production. Scope any debugging to `custom_components.tholz_fairland`, whose own log lines are sanitized.

At runtime it sends credentials and device data only to the vendor-operated authentication, device, metrics, custom-authorizer, and messaging services required for the integration. Metrics and schedule data remain read-only. No analytics or telemetry are sent to this project's maintainers.

Remove the integration and change the account password if the Home Assistant installation or a backup is exposed.

## Limitations

- Cloud access is mandatory; there is no local fallback.
- Vendor outages, account changes, rate limits, or interface changes can make entities unavailable.
- The account password is retained for unattended recovery; protect Home Assistant storage and backups accordingly.
- Schedules can be viewed but not edited.
- Only the documented Fairland/TurboSilence mode, setpoint, and fan controls are supported.
- A failed command can leave the device unavailable even after rollback is attempted. Verify the physical equipment if Home Assistant reports an error.

## Support

Before reporting a problem:

1. Update Home Assistant and this integration.
2. Reload the integration.
3. Confirm the device is online in the vendor app.
4. Check **Settings → System → Logs** for `tholz_fairland` messages.
5. Remove credentials and device identifiers from any log excerpt before sharing it.

Use the repository issue tracker for reproducible bugs. Include the integration version, Home Assistant version, affected entity, expected behavior, actual behavior, and sanitized logs. Do not post passwords, tokens, account identifiers, device identifiers, backups, or complete config entries.

## License

MIT. See [LICENSE](LICENSE). The extracted implementation retains the original project's copyright notice.
