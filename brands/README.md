# Brand assets

These are **not** part of the integration. Home Assistant does not read brand
images from a custom component — it fetches them from a CDN keyed on the
integration domain:

```
https://brands.home-assistant.io/kasa_cloud/icon.png
```

Until that path exists, Home Assistant shows "icon not available". There is no
local override: a custom integration's `icons.json` sets *entity and service*
icons, not the brand logo.

## Making the icon appear

The files here mirror the layout of [home-assistant/brands](https://github.com/home-assistant/brands),
so they can be copied straight in:

```
custom_integrations/kasa_cloud/icon.png      256x256
custom_integrations/kasa_cloud/icon@2x.png   512x512
```

To submit: fork `home-assistant/brands`, copy `custom_integrations/kasa_cloud/`
into the same path there, and open a pull request. Once merged, the CDN serves
it and the icon appears after a browser cache refresh.

Only the PNGs belong in a brands submission. `kasa-mark.svg` is kept here as the
source they were rendered from; do not copy it into the brands repo.

## Regenerating

```bash
rsvg-convert -w 256 -h 256 -o custom_integrations/kasa_cloud/icon.png    kasa-mark.svg
rsvg-convert -w 512 -h 512 -o custom_integrations/kasa_cloud/icon@2x.png kasa-mark.svg
```

Both sizes are rendered natively from vector, so neither is upscaled.

## Requirements these files satisfy

- PNG only, square 1:1, transparent background, trimmed of empty space (the
  mark fills its 24x24 viewBox edge to edge, so the render has no margin)
- `icon.png` exactly 256x256, `icon@2x.png` exactly 512x512
- Lossless, optimized
- No Home Assistant branding (the one rule the brands repo enforces strictly,
  since HA branding would imply this is an official integration)

No dark-theme variant is needed: the mark is a single cyan on transparency, and
its negative space is transparent rather than white, so it reads correctly on
both light and dark backgrounds.

## Provenance

The SVG is the Kasa Smart mark from [Simple Icons](https://simpleicons.org/),
which publishes its icon set under CC0, filled with Kasa's `#4ACBD6`. That
colour was verified to match the dominant colour of the vendor's own
`kasasmart.com/assets/images/logo-mark.png` exactly.

The mark itself remains TP-Link's trademark, reproduced to identify which
service this integration talks to — the same convention Home Assistant's own
`tplink` integration follows. CC0 covers Simple Icons' reproduction, not the
trademark.
