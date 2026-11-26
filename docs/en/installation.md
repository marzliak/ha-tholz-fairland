# Installation

> **Illustrative UI guide.** Menu names may vary by Home Assistant version. For account setup, see [Configuration](configuration.md).

## 1. Before you start

Check the following:

- [ ] **Home Assistant 2025.2.4 or newer.** Automated tests with mocked cloud services passed on **2025.2.4** and **2026.10.1**; intermediate versions were not individually tested.
- [ ] **A Fairland / TurboSilence heat pump** already linked to the manufacturer's app account, with state reading working in that app.
- [ ] **E-mail or username and password** for that account.
- [ ] **Internet access** from Home Assistant to the manufacturer's cloud and AWS services.
- [ ] **A recent Home Assistant backup.** The integration's config entry is stored in Home Assistant's storage (see [Privacy](privacy.md)).

Other Tholz product lines are not supported.

## 2. Installation method

**0.3.0 is the first public release (2026-11-16).** See the [release page](https://github.com/marzliak/ha-tholz-fairland/releases/tag/v0.3.0). HACS is recommended; use the manual package if you do not use HACS.

### Through HACS

**HACS must already be installed.**

1. In HACS, search for **Tholz Fairland** and open it.
2. Select **Download** and confirm.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → Tholz Fairland** and follow [Configuration](configuration.md).

Sections 3 and 4 below are the manual-installation alternative; HACS users do not need to copy files.

## 3. Download and check the package

1. Download the [manual package `tholz-fairland-0.3.0-integration.zip`](https://github.com/marzliak/ha-tholz-fairland/releases/download/v0.3.0/tholz-fairland-0.3.0-integration.zip).
2. If the release page publishes a SHA-256 value for the file, compare it before extracting:

   ```bash
   sha256sum tholz-fairland-0.3.0-integration.zip
   ```

   On Windows (PowerShell): `Get-FileHash .\tholz-fairland-0.3.0-integration.zip -Algorithm SHA256`

3. Extract the archive into a temporary folder on your computer. The expected layout is:

   ```text
   . (ZIP root, with no wrapper folder)
   ├── LICENSE
   └── custom_components/
       └── tholz_fairland/
           ├── __init__.py
           ├── manifest.json
           ├── config_flow.py
           ├── sensor.py
           └── ...
   ```

## 4. Copy the integration folder

The only folder that goes into Home Assistant is `custom_components/tholz_fairland/`. `LICENSE` is not copied into Home Assistant.

The final destination is:

```text
<Home Assistant configuration folder>/custom_components/tholz_fairland/
```

Where the configuration folder is:

| Installation | Configuration folder |
|---|---|
| Home Assistant OS or Supervised | `/config` (reach it through the Samba, SSH or File editor add-on) |
| Container (Docker) | The configuration folder you mapped into the container |
| Home Assistant Core (Python environment) | Usually `~/.homeassistant` |

Cautions:

- Copy `tholz_fairland` directly inside `custom_components`. Do not create `custom_components/tholz_fairland/tholz_fairland`.
- Do not leave old backup files (`.bak`, dated copies) inside `custom_components`. Home Assistant tries to load folders in that tree.
- If an older version is already installed, replace the whole folder with this one and keep a copy of the old version outside `custom_components`. See [Updating](updating.md).

## 5. Restart Home Assistant

Go to **Settings → System → Restart** and confirm. Wait for Home Assistant to come back.

A restart is required so the new code loads. Reloading only the integration entry is not enough after copying files.

## 6. Confirm the integration appears

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **Tholz Fairland**.

If it does not appear:

- check that the folder is `custom_components/tholz_fairland/` and that `manifest.json` is inside it;
- check that Home Assistant was restarted after copying;
- see the logs in [Troubleshooting](troubleshooting.md#the-integration-does-not-appear-in-the-list).

When it appears, continue with [account setup](configuration.md).

## 7. Integration icon

From Home Assistant **2026.3**, the package can show the integration's own brand image. Earlier versions show the standard integration placeholder icon. This difference is visual only.

## 8. Removal

1. Go to **Settings → Devices & services → Tholz Fairland**.
2. Open the entry and choose **Delete** (or **Remove**).
3. Delete the folder `custom_components/tholz_fairland/` and restart Home Assistant.

Removal takes this entry's entities out of Home Assistant. History retention follows the Home Assistant recorder's own rules. If your backup or Home Assistant storage was exposed, also change the manufacturer account password.

## Next steps

- [Configuration](configuration.md): sign in and choose the device.
- [Operation](operation.md): understand entities and limits.
- [Updating](updating.md): how to update later.
