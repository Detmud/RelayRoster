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
| inline in `app/static/index.html` and `login.html` (`.sf-logo`) | Square 24-unit navbar icon. The body uses `currentColor` (Tabler body color, so it follows light and dark mode); the comb uses the vermilion accent via `.sf-logo-accent` in `style.css`. Designed for small sizes, including 16 px. |
| `app/static/favicon.svg` | Same geometry with the brand accent. Navy on light browser tabs; soft white on dark tabs via `prefers-color-scheme`. |
| `docs/assets/logo-light.svg` | Horizontal icon and RelayRoster wordmark for light backgrounds. |
| `docs/assets/logo-dark.svg` | Matching horizontal lockup for dark backgrounds. |
| `docs/assets/social-preview.png` | 1280 × 640 social card on solid deep navy. The large rooster sits above the wordmark. Both stay inside the central 1200 × 600 safe area. |

All SVGs have a viewBox with no fixed root width or height, embedded images, text elements, fonts, scripts, or external references. Lettering uses custom hand-drawn outlines. Lockups use three paths: body, comb, and wordmark. Scale proportionally and keep clear space around the mark. Use the standalone mark where the wordmark would become too small.

## User interface

The web UI maps the brand onto Tabler's theme variables in `app/static/style.css`. Navy alone does not work
everywhere: links in navy look like body text on light backgrounds, and navy buttons disappear on dark ones. So the
UI uses tints of the same hue, chosen for WCAG AA contrast.

| Mode | Buttons and active states (`--tblr-primary`) | Links (`--tblr-link-color`) |
| --- | --- | --- |
| Light | `#142D4E` midnight navy (13.9:1 with white text) | `#2A5F9E` (6.5:1 on white) |
| Dark | `#3E72B3` (4.9:1 with white text, 3.6:1 against the background) | `#8DB0E0` (8:1 on the background) |

Vermilion (`--sf-accent`) is only an accent in the mark. It is not used for text (3.6:1 on white) or for buttons,
where it would read as danger.
