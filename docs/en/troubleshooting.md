# Troubleshooting

Find the symptom below. Each item gives a likely cause and what to do. If nothing helps, see [Collecting logs safely](#collecting-logs-safely) before opening a report.

## Symptoms and actions

### The integration does not appear in the list

- Confirm the folder is `custom_components/tholz_fairland/` and that `manifest.json` is inside it ([Installation](installation.md)).
- Confirm Home Assistant was **restarted** after copying. Reloading the entry does not load new code.
- Go to **Settings → System → Logs** and filter for `tholz_fairland`. Import or version errors appear there.
- Check your Home Assistant version: automated tests passed on 2025.2.4 and 2026.10.1. Future versions or other combinations may need their own verification.

### No device appears

Message: **"No compatible Fairland heat pump was found in this account."**

- In the manufacturer app, confirm the device is linked to **this account** (not another account of the same family).
- The integration lists only Fairland / TurboSilence devices. Other Tholz products are ignored by design.
- Confirm the app shows the device as available.

### Credentials rejected

Message: **"The account e-mail or password was rejected."**

- Check the e-mail or username (the same one you use in the app) and the password. Watch for capitalisation and spaces.
- If you changed the password recently, use the new one.
- Try signing in to the manufacturer app with the same credentials. If that fails, the problem is the account.

### Asks for verification or a new password

- Enter the code you just received, without spaces. Codes expire quickly; request another if the message says the value was not accepted.
- If the screen asks for a new password, the password you type becomes the stored one.
- If the message says **"Finish signing in with the vendor app"**, the account requires a step the integration does not complete. Complete it in the app and repeat setup.

### Asks for reauthentication

This appears when the session could not be renewed automatically.

1. Open the **Reauthenticate Tholz Fairland** alert under **Settings → Devices & services**.
2. Enter the current account and password. If the account requires a code, follow the verification steps.
3. If the message says the account **does not match this device**, you signed in with a different account. Use the original account. Nothing is changed in that case.

If the alert keeps coming back, check whether the password was changed in the app. In that case, use **Reconfigure** with the new password ([Configuration](configuration.md#update-credentials-reconfigure)).

### Recovery on hold or spaced logins

The integration limits automatic password logins per config entry: at most 3 per day, and at least 15 minutes between attempts. Temporary service failures also cause a 30-second wait. This is expected after several consecutive failures. Wait; do not repeat setup in quick succession, because each login can send a code to the account.

### Missing or unavailable entities

- **Missing entities:** temperature sensors, indicators and schedules are created only if the device reports the field at setup or reload. If an entity is missing, wait one refresh interval and then **reload the entry** (**Devices & services → Tholz Fairland → ⋮ → Reload**).
- **Unavailable entities:** usually the cloud or the device connection has dropped. Check the **Online** sensor. If it is off, check the manufacturer app. If the app also shows the device offline, the problem is the device or its connection to the cloud.
- If entities stay unavailable for more than a few minutes while the app works, collect logs ([below](#collecting-logs-safely)).
- Entity IDs may get a numeric suffix (`_2`) if the name already exists. Check actual IDs under **Developer tools → States** before editing dashboards or automations.

### A command was not applied, or the value went back

- Wait a few seconds. Success requires the device to report the new value with no pending change, which can take a moment.
- If the value went back, the device did not converge on the requested value and the integration restored the previous state. Try again once the device is confirmed online.
- Use only modes Off / Automatic / Heat / Cool and fan Silent / Intelligent. For type-7 temperatures: Automatic **12–40 °C**, Heat **18–40 °C**, Cool **12–30 °C**, Off **12–40 °C** without turning on when only the temperature is set. Reported limits may narrow these ranges.
- Out-of-range values, values not exactly representable in tenths of a degree, and values off the step grid are rejected without rounding. Malformed limits or steps and an unknown mode also block temperature commands. Validation uses fresh state: an external mode change may invalidate a value the interface previously showed as allowed. See [limits and step](operation.md#temperature-limits-and-step); do not retry the command in a loop.

### Failed to restore the previous state

The integration treats this case as a failed return to the previous state (internal error `ROLLBACK_FAILED`). The device may be in a state different from what Home Assistant expects. In this release, the exact text shown in the interface is not guaranteed; note the time and the value you tried to set.

- **Check the device physically** and in the manufacturer app before sending any other command.
- Adjust mode, temperature and fan from the app or Home Assistant only after confirming the real state.

### Problem is on, or error code is not zero

- The **Problem** sensor turns on when the error code is not 0. The integration **does not translate** the code.
- Note the value in **Error code** and check the manufacturer's manual or app for its meaning.
- No automatic action is taken; the device may need technical service.
- If the code returns to 0 and **Problem** turns off, the alert has cleared.

### 24-hour metrics or schedules are empty

- Activity rows come from the manufacturer's API; the integration calculates the rolling 24-hour summary and holds it in memory for **5 minutes**, separately from state polling.
- A non-authentication metrics failure leaves the initial summary as `{}` (sensors with no value, not zero). After a successful query, it preserves the previous summary, which may become stale. After the cache wait, the next read attempts another query.
- HTTP **401/403** are not hidden by the metrics fallback: they follow the authentication/authorization error path. Check the entry's notices rather than treating an old value as a new valid reading.
- Schedules are read together with the state. If the schedule read fails, the schedule entities may not be created at setup. Reload the entry.
- None of these metrics measures energy consumption.

### Too many requests, or a lagging dashboard

- Shorter intervals produce more cloud requests. Adjust in **Configure** (range 10 to 300 s). See [Configuration → Polling interval](configuration.md#polling-interval).

### After changing the account password

- Use **Reconfigure** with the new password. Do not delete and re-add the entry: deletion removes the entities and customisations tied to it.

## Collecting logs safely

1. Update Home Assistant and the integration.
2. Reload the entry.
3. Go to **Settings → System → Logs** and filter for `tholz_fairland`.
4. For more detail, raise the log level **only** for the integration. In `configuration.yaml`:

   ```yaml
   logger:
     default: warning
     logs:
       custom_components.tholz_fairland: debug
   ```

   Restart Home Assistant, reproduce the problem, collect the log and set the level back to `warning`.

5. **Do not** enable `debug` for `botocore`, `boto3`, `urllib3` or the root logger. The AWS library logs full request and response contents at that level, including tokens and session data. See [Privacy](privacy.md).
6. Before sharing a log excerpt, remove e-mail addresses, passwords, tokens, account identifiers and device identifiers.

Do not post passwords, tokens, account or device identifiers, backups or complete config entries in issues. Vulnerability reports follow the policy in `SECURITY.md`, not a public issue.

## Reporting a problem

Include: the integration version, the Home Assistant version, the affected entity, expected and observed behaviour, and the log excerpt with personal data removed. Use the repository issue tracker for reproducible bugs.
