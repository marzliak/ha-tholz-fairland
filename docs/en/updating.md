# Updating

This guide explains how to update the integration, what happens to existing entries, and how to revert.

## Before you update

1. Take a **backup** of Home Assistant. It contains the integration's credentials ([Privacy](privacy.md)).
2. Note the current integration version (`manifest.json` in `custom_components/tholz_fairland/`).
3. Confirm the manufacturer app account works normally.

## Update with the manual package

1. Download the new package from the [release page](https://github.com/marzliak/ha-tholz-fairland/releases/tag/v0.3.0) (`tholz-fairland-0.3.0-integration.zip` for 0.3.0).
2. If the old version is in `custom_components/tholz_fairland/`, **move the old folder** outside `custom_components` (for example to `/config/.tholz-backup-<version>/`).
3. Copy the `tholz_fairland` folder from the new package into `custom_components/`.
4. Restart Home Assistant ([Installation](installation.md)).
5. Reload the entry and check the log under **Settings → System → Logs**, filtered for `tholz_fairland`.

Do not leave two folders of different versions inside `custom_components`. The old folder kept outside it serves as the safety copy.

## Update through HACS

If installed through HACS, open **Tholz Fairland** in HACS, select the update and confirm **Download**. Restart Home Assistant and check entities and logs. You do not need to remove or re-add the configuration entry. For first-time custom-repository installation, see [Installation](installation.md); default-catalogue inclusion has not yet been approved.

## Existing entries are migrated

Version 0.3.0 migrates entries created by earlier versions (schemas 1.1 to 1.3) automatically on first start:

- **Identifiers, entities, device and names are preserved.** Entity IDs do not change, so dashboards and automations remain valid.
- The entry moves to schema **1.4**. Automatic recovery becomes the only supported policy.
- If the old entry already had a username and password in automatic mode, both are kept.
- If the old entry was in **interactive** mode, the password is **not** adopted. The integration keeps the session token and asks for **reauthentication** when it needs a fresh password. Follow the **Reauthenticate Tholz Fairland** alert in that case.
- The obsolete client secret from older cloud versions is removed and replaced with an empty value, for compatibility.

After updating, check:

- the **Online** sensor responds;
- entities keep the same IDs;
- there is no pending reauthentication alert, or you have resolved it if there is one.

## Revert to an earlier version

Migration changes the entry to schema **1.4**. Do not assume an older build accepts that entry: downgrade compatibility has not been validated. For a reliable return to the previous version, restore the **pre-update backup**, including the matching integration folder and Home Assistant configuration. Replacing only the code is a troubleshooting attempt, not a guaranteed rollback.

1. Remove the new `tholz_fairland` folder from `custom_components/`.
2. Restore the old folder you kept.
3. Restart Home Assistant.

If the revert fails or the entry is left inconsistent, restore the **backup** taken before the update. Restoring a backup replaces Home Assistant's current state, so use it only if a simple revert is not enough.

## Changing Home Assistant versions

Update the integration and confirm it works on the new Home Assistant version before updating Home Assistant in production. Automated tests with mocked cloud services passed on **2025.2.4** and **2026.10.1**. Verify your account and equipment after updating; intermediate versions and future vendor changes are not automatically certified.

## Changing credentials after an update

If the password changed, use **Reconfigure** (do not delete the entry). See [Configuration → Update credentials](configuration.md#update-credentials-reconfigure).

## Next steps

- [Release notes 0.3.0](releases/0.3.0.md): what changed in this version.
- [Troubleshooting](troubleshooting.md): when something fails after an update.
