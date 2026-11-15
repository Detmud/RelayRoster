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
| `docs/assets/social-preview.png` | 1280 × 640 social card on solid deep navy. The large rooster sits above the wordmark. Both stay inside the central 1200 × 600 safe area. |

All SVGs have a viewBox with no fixed root width or height, embedded images, text elements, fonts, scripts, or external references. Lettering uses custom hand-drawn outlines. Lockups use three paths: body, comb, and wordmark. Scale proportionally and keep clear space around the mark. Use the standalone mark where the wordmark would become too small.

## User interface

The web UI maps the palette below onto Tabler's theme variables in `app/static/style.css`, separately for light and
dark mode. Navy is for actions, cool berry red for danger. Vermilion `#F34A3E` (`--rr-accent`) stays inside the logo:
it is not used for text (3.6:1 on white) or for buttons, where it would read as danger. Tabler's colour names map
onto the semantic colours (green → success, yellow and orange → warning, red → danger, blue → info; azure and purple
→ navy tint for classification badges such as profiles), so existing classes like `bg-green-lt` follow the palette.

### Neutral ramp (slight navy tint)

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| N50 | `#F4F7FB` | `#0F1723` | Light: cards and inputs; dark: page canvas |
| N100 | `#EBF0F6` | `#172334` | Light: page canvas, table headers; dark: cards and inputs |
| N200 | `#DCE5EF` | `#213046` | Hover backgrounds, neutral badge background |
| N300 | `#BBC9DA` | `#32455F` | Card and table borders, strong neutral fills (text N900) |
| N400 | `#667A94` | `#6F829C` | Input borders, essential separators, functional icons |
| N500 | `#52647D` | `#9AAEC7` | Muted text (`text-secondary`), placeholders, metadata |
| N600 | `#40516A` | `#B6C5D8` | Secondary text and supporting labels |
| N700 | `#2D3D54` | `#D1DCEA` | Body text and table values |
| N800 | `#1D2B40` | `#E4ECF5` | Table headings and prominent labels |
| N900 | `#101B2B` | `#F4F7FB` | Headings and strongest emphasis |

### Navy ramp

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| P50 | `#EEF3FA` | `#11243C` | Active navigation, navy badge background |
| P100 | `#DEE8F5` | `#172F4D` | Hover inside active navigation |
| P200 | `#BED0E9` | `#213F63` | Selected-row fill |
| P300 | `#95B3D8` | `#31567F` | Strong decorative fills |
| P400 | `#527AAD` | `#668FBD` | Focus outline (2 px, 2 px gap), functional icons |
| P500 | `#3B6599` | `#83A9D7` | Dark: primary button |
| P600 | `#2A5F9E` | `#9EBDE3` | Links; dark: primary button hover, active navigation text |
| P700 | `#214A7A` | `#B8CFEA` | Light: link hover; navy badge text |
| P800 | `#142D4E` | `#D2E1F3` | Light: primary button and active navigation text (midnight navy); dark: link hover |
| P900 | `#0D1E35` | `#E9F0FA` | Light: primary button hover |

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
| Text | N500–N900 on N50, N100, N200 or P50 |
| Solid buttons and badges | Label `#F4F7FB` in light mode, `#0F1723` in dark mode |
| Tinted badges | Semantic solid on its own badge background |
| Active navigation | P50 background, P800 (light) or P600 (dark) text, 3 px primary marker |
| Focus | P400 outline, 2 px, 2 px gap |
| Inputs and essential boundaries | N400 |

Measured in the rendered UI, the lowest contrast is 4.74:1 for text and badges in light mode and 5.87:1 in dark mode;
essential boundaries and the focus outline reach at least 3.39:1. Inline links in alerts and help text are underlined,
and every status badge carries a label, so meaning does not depend on colour alone.
