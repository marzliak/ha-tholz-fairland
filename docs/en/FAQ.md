# Frequently asked questions

**Is the integration official from Tholz or Fairland?**
No. It is an independent, unaffiliated project. It depends on the manufacturer's cloud services, which can change without notice.

**Does it work without internet or without the cloud?**
No. The integration is cloud-only. Without cloud access there is no reading and no command.

**Do I need local hardware or a gateway?**
No. You need Home Assistant with internet access and the account already linked to the device in the manufacturer app.

**Which devices are supported?**
Fairland / TurboSilence heat pumps linked to an account. Other Tholz products are not supported.

**Can I control several heat pumps?**
Yes. Each device needs its own entry. Repeat setup for each one.

**Which Home Assistant version is required?**
Automated tests with mocked cloud services passed on **2025.2.4** and **2026.10.1**. The declared minimum is 2025.2.4; this does not certify every intermediate version or every device model.

**Can I install through HACS?**
Not for this candidate. HACS will be available after the repository is published. Until then use the manual package ([Installation](installation.md)).

**Is the heater's "current" temperature the pool temperature?**
No. The current temperature is the water **inlet** reading at the device. The integration has no separate pool probe. See [Operation](operation.md#meaning-of-the-temperature-sensors).

**What does "Entrada / Piscina" mean on the example dashboard?**
It is the inlet water temperature at the device, labelled by the dashboard's author. It is not a separate pool measurement.

**Does the "Cool" mode appear as "cold"?**
Yes. The internal value is `cold`. The project README calls the same mode "Cool". The Home Assistant interface may show a different label. Accepted values are listed in [Operation](operation.md#commands).

**Can I change the schedules through the integration?**
No. Schedules are read-only. Change them in the manufacturer's app.

**Does the integration show energy consumption or cost?**
No. The dashboard shows the 24-hour active time and percentage reported by the manufacturer. These are not energy measurements.

**Is "Heat pump running" a switch?**
No. It is an indicator of the reported running state. It does not turn the device on or off.

**Can I control the circulation pump?**
No. The integration does not expose that control, and no example in the project commands it.

**Is the password stored?**
Yes. It is kept in the config entry to allow automatic recovery. The field is masked on screen, but the value is not individually encrypted. See [Privacy](privacy.md).

**Do I have to reauthenticate often?**
Not if the account does not require verification and the password has not changed. The integration tries to renew the session automatically.

**What do I do if the verification code is not accepted?**
Request a new code in the app and try again. Codes expire quickly.

**The account asks for a step the form does not accept. What now?**
Complete the step in the manufacturer app and repeat setup. The integration does not complete every possible account step.

**Why does the integration not find my device?**
Check that the device is linked to the account used in setup and that it appears in the app. See [Troubleshooting](troubleshooting.md#no-device-appears).

**Can I use the dashboard example without changing anything?**
Only if your setup name is `Fairland`, which matches the example IDs. Otherwise adjust the IDs before saving. See [Dashboard](dashboard.md).

**Are the example automations active?**
No. All are optional and must be copied and enabled by you. See [Automations](automations.md).

**Why are some entity names in English and others in Portuguese?**
The names are the ones the integration creates in this release. Some entities use English and others Portuguese. Entity IDs derive from those names.

**How do I report a problem without exposing data?**
Remove e-mail addresses, passwords, tokens and identifiers from any log excerpt before sharing it. See [Troubleshooting](troubleshooting.md#collecting-logs-safely).

**Where is the diagnostics download?**
This release has no diagnostics download. Use the logs and the diagnostic sensors described in [Operation](operation.md).
