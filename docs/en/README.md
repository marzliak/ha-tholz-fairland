# Documentation — Tholz Fairland 0.3.0

User guides in English. The Portuguese (PT-BR) originals are in [`../`](../README.md) and are the reference text if the two ever differ.

> **Unofficial integration.** Tholz Fairland is not affiliated with or endorsed by Tholz or Fairland. It depends on the manufacturer's cloud services and may stop working if those services or their interfaces change.

## Status of this release

- **First public release: [0.3.0](https://github.com/marzliak/ha-tholz-fairland/releases/tag/v0.3.0), dated 2026-11-16.**
- **Installation available now:** manual package (`tholz-fairland-0.3.0-integration.zip`), described in [Installation](installation.md).
- **HACS:** search for **Tholz Fairland** in the HACS catalogue. See [Installation](installation.md) for the full steps and the manual alternative.
- **Home Assistant compatibility:** network-blocked automated tests passed on **2025.2.4** and **2026.10.1**, including configuration, entity setup, reload, and reauthentication with mocked services. This does not certify every intermediate version or replace verification with your own account/equipment.

## Reading order

| Guide | Purpose | PT-BR original |
|---|---|---|
| [Installation](installation.md) | Prerequisites, manual package copy, first checks | [../installation.md](../installation.md) |
| [Configuration](configuration.md) | Account sign-in, device discovery, verification, recovery, reauthentication | [../configuration.md](../configuration.md) |
| [Operation](operation.md) | Entities, meaning and limits of sensors, commands, supported modes | [../operation.md](../operation.md) |
| [Dashboard](dashboard.md) | Native-card example and how to adapt it | [../dashboard.md](../dashboard.md) |
| [Automations](automations.md) | Optional alerts that you enable explicitly | [../automations.md](../automations.md) |
| [Troubleshooting](troubleshooting.md) | Symptoms, likely causes, actions | [../troubleshooting.md](../troubleshooting.md) |
| [Privacy and security](privacy.md) | Stored data, external services, backup handling | [../privacy.md](../privacy.md) |
| [Updating](updating.md) | Updating, migrating existing entries, reverting | [../updating.md](../updating.md) |
| [FAQ](FAQ.md) | Common questions | [../FAQ.md](../FAQ.md) |
| [Release notes 0.3.0](releases/0.3.0.md) | What changed in this release | — |
| [Examples](../../examples/README.md) | Native dashboard and optional automations, ready to adapt | — |

## How to read the images

Every image is labelled in its caption as one of:

- **Real capture:** a screenshot of an example Home Assistant dashboard. Only `images/dashboard-overview.png` is a real capture.
- **Illustrative diagram:** a schematic of the architecture, onboarding flow, command lifecycle, recovery flow or entities. It is not a screenshot.
- **Illustrative UI guide:** a textual description of screens written for orientation. Menu and button names may differ by Home Assistant version.

## Scope

The integration supports **only** Fairland / TurboSilence heat pumps linked to a manufacturer cloud account. It does not control other Tholz controllers, lighting, auxiliary outputs or switches. There is no local access: without the cloud there is no control.

See also the [project README in Portuguese](../../README.pt-BR.md) and the [English project README](../../README.md).
