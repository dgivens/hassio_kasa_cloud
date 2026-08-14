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

## Requirements these files satisfy

- PNG only, square 1:1, transparent background, trimmed of empty space
- `icon.png` exactly 256x256, `icon@2x.png` exactly 512x512
- Lossless, optimized
- No Home Assistant branding (the one rule the brands repo enforces strictly,
  since HA branding would imply this is an official integration)

## Known imperfection

`icon@2x.png` is upscaled from a 455px source, so it is marginally softer than a
native 512px render. Replace it from a larger original — ideally the vector
source — if one becomes available.

## Note on the mark

The Kasa logo is TP-Link's trademark, reproduced here to identify which service
this integration talks to. This is the same convention Home Assistant's own
`tplink` integration follows.
