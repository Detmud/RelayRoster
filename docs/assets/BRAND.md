# RelayRoster brand

The mark combines a rooster (the roster/rooster wordplay), three list-like tail feathers, and three circular electrical contact holes.

## Colors

| Color | Hex | Use |
| --- | --- | --- |
| Midnight navy | `#142D4E` | Primary mark and lettering on light backgrounds |
| Vermilion | `#F34A3E` | Rooster comb accent |
| Soft white | `#F4F7FB` | Mark and lettering on dark backgrounds |
| Deep navy | `#0D192B` | Social preview background |

## Files

| File | Purpose |
| --- | --- |
| inline in `app/static/index.html` and `login.html` (`.rr-logo`) | Square 24-unit navbar icon. The body uses `currentColor` (Tabler body color, so it follows light and dark mode); the comb uses the vermilion accent via `.rr-logo-accent` in `style.css`. Designed for small sizes, including 16 px. |
| `app/static/favicon.svg` | Same geometry with the brand accent. Navy on light browser tabs; soft white on dark tabs via `prefers-color-scheme`. |
| `docs/assets/logo-light.svg` | Horizontal icon and RelayRoster wordmark for light backgrounds. |
| `docs/assets/logo-dark.svg` | Matching horizontal lockup for dark backgrounds. |
| `docs/assets/icons/relayroster-icon.svg` | Standalone square mark (24-unit viewBox) with 10 % clear space on every side, so a round or rounded-square crop keeps the beak. Navy body and vermilion comb, for light backgrounds. |
| `docs/assets/icons/relayroster-icon-dark.svg` | The same mark with a soft white body and vermilion comb, for dark backgrounds. |
| `docs/assets/icons/relayroster-icon[-dark]-<size>.png` | Both variants as PNG with transparency in 16, 24, 32, 48, 64, 128, 256, 512 and 1024 px, for places that do not take SVG (app stores, container dashboards, avatars). |
| `docs/assets/social-preview.png` | 1280 × 640 social card on solid deep navy. The large rooster sits above the wordmark. Both stay inside the central 1200 × 600 safe area. |

All SVGs have a viewBox with no fixed root width or height, embedded images, text elements, fonts, scripts, or external references. Lettering uses custom hand-drawn outlines. Lockups use three paths: body, comb, and wordmark. Scale proportionally and keep clear space around the mark. Use the standalone mark where the wordmark would become too small.

## User interface

The web UI is Tabler with as few overrides as possible, set in `app/static/style.css` separately for light and dark
mode. Neutrals (page canvas, cards, inputs, borders, body and muted text), the focus ring and the active navigation
are Tabler's defaults. Only the brand colours below replace Tabler's: navy for actions and links, cool berry red for
danger. Vermilion `#F34A3E` (`--rr-accent`) stays inside the logo: it is not used for text (3.6:1 on white) or for
buttons, where it would read as danger. Tabler's colour names map onto the semantic colours (green → success, yellow
and orange → warning, red → danger, blue → info; azure and purple → navy for classification badges such as
profiles), so existing classes like `bg-green-lt` follow the palette.

### Navy

| Use | Light | Dark |
| --- | --- | --- |
| Primary button, `text-primary` | `#142D4E` (midnight navy) | `#83A9D7` |
| Primary button hover | `#0D1E35` | `#9EBDE3` |
| Links | `#2A5F9E` | `#9EBDE3` |
| Link hover | `#214A7A` | `#D2E1F3` |
| Navy badge (`bg-azure-lt`, `bg-purple-lt`) | `#214A7A` on `#EEF3FA` | `#B8CFEA` on `#11243C` |
| `bg-primary-lt` (changed rows, selected tiles) | `#EEF3FA` | `#11243C` |

Tabler derives the active navigation, hover and focus tints from the primary colour, so they follow navy without
further overrides.

### Semantic colours

Solid is used for status text, icons and filled buttons; the badge background for tinted badges (`bg-*-lt`).

| Colour | Light solid | Light badge | Dark solid | Dark badge | Use |
| --- | --- | --- | --- | --- | --- |
| Success (forest green) | `#216847` | `#E7F4ED` | `#79C8A4` | `#16392F` | Online, synchronized, valid |
| Warning (muted ochre) | `#825600` | `#FFF3D6` | `#E9C16A` | `#3C3019` | Needs attention, stale data |
| Danger (cool berry red) | `#A51C4A` | `#FCE8EE` | `#F0A0BC` | `#432336` | Failed, invalid, destructive |
| Info (blue teal) | `#166779` | `#E5F3F6` | `#7BC9DD` | `#173642` | Firmware, scanning, notes |

Danger's cool pink undertone keeps it apart from the warm vermilion of the logo.

### Pairing rules

| Component | Pairing |
| --- | --- |
| Solid buttons and badges | Label `#F4F7FB` in light mode, `#0F1723` in dark mode |
| Tinted badges | Semantic or navy solid on its own badge background |
| Everything else | Tabler defaults |

Every solid-on-badge pair and every solid on Tabler's white or dark card surface measures at least 4.5:1. Inline links
in alerts and help text are underlined, and every status badge carries a label, so meaning does not depend on colour
alone.
