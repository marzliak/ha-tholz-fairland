# Privacy and security

This guide explains what data the integration stores, who it is sent to, and what you need to protect.

## What is stored

The integration stores the items below **in the config entry** in Home Assistant:

| Item | Why it is stored |
|---|---|
| Account e-mail or username | Login for renewal and automatic recovery |
| Account password | Automatic recovery without your intervention |
| Session refresh tokens | Renew the session without asking for the password each time |
| Cloud user identifier | Confirm that a reauthentication is for the same account |
| Device identifier | Link the entry to the device |
| Display name and polling interval | Preferences |

**Why the password is stored:** without it, any session expiry would require you to sign in manually again. The integration tries renewal without the password first; the password is used only when the other methods fail.

## Storage and backups

- The password field is **masked in the interface**, but the value is **not individually encrypted** in Home Assistant storage. It sits in `.storage`, in the same file as the other config entries.
- **Backups that include the integration contain the same credentials.** Restoring a backup restores the session and the password.
- Treat `.storage/`, backups and copies of the configuration folder as **sensitive data**.
  - Keep backups encrypted if your Home Assistant version offers backup encryption.
  - Restrict access to the file system and to Home Assistant's SSH access.
  - Do not send backups, `.storage` files or config entries to third parties, including support.

The integration does **not** add a private encryption layer to the entry data. Such a layer would need a decryption key accessible to the runtime itself, which would not improve Home Assistant's normal trust model.

## When to act

If the Home Assistant installation or a backup is exposed:

1. Remove the integration, or change the account password in the manufacturer app.
2. Invalidate old sessions, if the manufacturer allows it.
3. Set the entry up again with the new password.

## External services

The integration sends credentials and device data **only** to the manufacturer's services needed for operation:

- **Account authentication** (Amazon Cognito, region `us-east-1`).
- **Manufacturer API** (AWS API Gateway, region `us-east-1`), for device discovery and the connection authorizer.
- **24-hour metrics** from the manufacturer (same API).
- **Device messaging** (AWS IoT Core, region `us-east-1`), for reading state and sending commands.

No analytics, telemetry or statistics are sent to this project's maintainers.

The services are operated by the manufacturer. The integration does not control their retention policies.

## Logs

- The integration is designed **not** to write passwords, tokens, SRP values, `SECRET_HASH`, authorization headers or challenge sessions to log or error messages.
- Challenge sessions (code or new password) exist only in the memory of the configuration flow and are discarded when the flow ends.
- **Do not enable `debug` for `botocore`, `boto3`, `urllib3` or the root logger.** The AWS library logs full request and response contents at that level, including tokens, new passwords and challenge sessions. If you need to debug, limit the level to `custom_components.tholz_fairland`, as described in [Troubleshooting](troubleshooting.md#collecting-logs-safely).

Before sharing a log, remove e-mail addresses, passwords, tokens and identifiers.

## What the integration does not do

- It does not store energy consumption or send usage data to third parties.
- It does not read or change other devices on the account.
- It does not send commands during setup, reauthentication or credential validation.
- It does not offer a diagnostics download in this release.

## Recommendations

- Use a unique password for the device account.
- Enable two-step verification on the account if the manufacturer offers it.
- Limit who has administrative access to Home Assistant.
- Before selling or discarding the Home Assistant system, remove the integration and change the account password.

## Vulnerability reports

Do not open a public issue for a suspected vulnerability. Follow the policy in `SECURITY.md` (private report through GitHub). Do not include credentials, tokens, identifiers or backups in the report.
