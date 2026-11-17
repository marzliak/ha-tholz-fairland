# Visual assets: origin, licence and credits

Visual asset inventory for public release 0.3.0 (2026-11-16). This file records
what each visual is, where it came from and its licence.

## Categories

Every image falls into exactly one category. The category is stated in the table below
and in each image's own caption where a caption is possible.

| Category | Meaning |
| --- | --- |
| **Original** | Drawn for this project from SVG. Licensed under the project's MIT licence. |
| **Contextual photograph** | A third-party photograph used for mood only. It is not the user's installation, not a supported heater, and not a product image. |
| **Real screenshot** | A capture of a running Home Assistant instance. Shows real UI at the time of capture. |
| **Illustrative** | A synthetic layout with example values. It is not a Home Assistant screenshot and does not show real data. |

## Inventory

| File | Category | Source | Licence / credit |
| --- | --- | --- | --- |
| `docs/images/hero.svg`, `docs/images/hero.png` | Original layout, with a contextual photograph and an illustrative control card | `docs/images/pool-context.jpg` as the background, plus original SVG layout and card | MIT for the layout. The photograph keeps its own licence (see below). |
| `docs/images/pool-context.jpg` | Contextual photograph | Unsplash (see credit below) | Unsplash License |
| `docs/images/architecture.svg`, `.png` | Original diagram | Drawn for this project | MIT |
| `docs/images/onboarding.svg`, `.png` | Original diagram | Drawn for this project | MIT |
| `docs/images/command-lifecycle.svg`, `.png` | Original diagram | Drawn for this project | MIT |
| `docs/images/recovery.svg`, `.png` | Original diagram | Drawn for this project | MIT |
| `docs/images/entities.svg`, `.png` | Original diagram | Drawn for this project | MIT |
| `docs/images/brand/icon.svg` | Original integration icon | Drawn for this project | MIT |
| `docs/images/brand/logo.svg`, `logo-dark.svg` | Original wordmark | Drawn for this project | MIT |
| `custom_components/tholz_fairland/brand/icon.png`, `icon@2x.png` | Original integration icon (rendered) | Rendered from `docs/images/brand/icon.svg` | MIT |
| `custom_components/tholz_fairland/brand/logo.png`, `logo@2x.png` | Original wordmark (rendered) | Rendered from `docs/images/brand/logo.svg` | MIT |
| `custom_components/tholz_fairland/brand/dark_logo.png`, `dark_logo@2x.png` | Original wordmark for dark themes (rendered) | Rendered from `docs/images/brand/logo-dark.svg` | MIT |
| `docs/images/dashboard-overview.png` | **Real screenshot** of an existing Home Assistant dashboard | Existing file from the repository, not regenerated | MIT, repository contributor. See note below. |

## Note on the dashboard screenshot

`docs/images/dashboard-overview.png` is an existing real screenshot, kept unmodified. It
shows a generic example device with example values, with no private labels or locators.

It is an example of the interface, not evidence of new testing for this release.

## Note on the hero photograph

The hero background is a photograph of a swimming pool edge. It is used only as a
contextual image.

- It does not show the user's installation.
- It does not show a supported heat pump.
- It does not show any product, logo or person.

The control card on the right of the hero is **illustrative**. It is labelled
"ILLUSTRATIVE VALUES" and "Not a Home Assistant screenshot". Its values are examples.

## Photograph credit

- Source: Unsplash. Downloaded from
  `https://images.unsplash.com/photo-1557459310-ea39155bd081?auto=format&fit=crop&fm=jpg&q=85&w=1800`.
- Photographer: not verified.
- Licence: Unsplash License, `https://unsplash.com/license`. It allows free use,
  including commercial use, with no attribution required. Credit to Unsplash and the
  image download source is voluntary and is given above.
- The photograph is not relicensed under MIT. Its own licence applies to it.

## Trademarks and affiliation

- "Tholz" and "Fairland" are names of third-party products. They are used only to
  identify the device this integration works with.
- "Home Assistant" is a trademark of its owner. No Home Assistant logo or branded
  image is used. The integration brand is original.
- Nothing in these assets implies endorsement by, or affiliation with, Tholz,
  Fairland, or Home Assistant.

## Home Assistant brand images

Home Assistant reads custom-integration brand images from the integration's own
`brand/` folder (since Home Assistant 2026.3). The files follow the naming used by the
Home Assistant brands repository:

- `icon.png` 256×256 and `icon@2x.png` 512×512 (square icon)
- `logo.png` 560×160 and `logo@2x.png` 1120×320 (landscape wordmark; shortest side 160 px and 320 px, within the 128–256 and 256–512 ranges)
- `dark_logo.png` 560×160 and `dark_logo@2x.png` 1120×320 (wordmark for dark themes)

No `dark_icon` file is shipped; Home Assistant falls back to `icon.png`.

## Regenerating

`scripts/render_visuals.py` renders every PNG from its SVG source. It requires a local
Chrome or Chromium (`CHROME` environment variable, or `google-chrome` on `PATH`) and
uses no network access. Run `python3 scripts/render_visuals.py --check` to validate the
SVG metadata without rendering.

## Accessibility

Each SVG has a `<title>` and a `<desc>`, linked with `aria-labelledby`. Body text in the
diagrams is 21–30 px at the SVG's native width. The smallest labels (step-number badges
and one footnote) are 18–19 px. Long lines were measured against their card edges, with
at least 14 px of margin.
