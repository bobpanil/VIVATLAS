# VIVATLAS Clipper on the Chrome Web Store

Everything the developer dashboard asks for, field by field. The package is
`dist/chrome/vivatlas-clipper-<version>.zip`, built by `scripts/chrome-extension.sh`.
Bump `version` in `extension/manifest.json` for each upload.

The texts to paste are in this folder:

| file | dashboard field |
|---|---|
| `description.txt` | Store listing > Description |
| `privacy-practices.txt` | Privacy practices tab |
| `test-instructions.txt` | Test instructions > Additional instructions (500 characters at most; fill in the demo server, and put the account in the Username and Password fields) |

## Store listing

- **Description:** `description.txt`. Plain text: the store shows Markdown and HTML as typed.
- **Category:** Productivity > Tools.
- **Language:** English.
- **Store icon:** `extension/icons/icon128.png`.
- **Screenshots** (1280x800), in this order: `images/screenshot-1.png` to `screenshot-5.png`.
  The store has no captions; the description says what they show.
- **Small promo tile** (440x280): `images/promo-small-440x280.png`.
- **Marquee promo tile** (1400x560, optional): `images/promo-marquee-1400x560.png`.
  Both are drawn by `promo-tiles.py`.
- **Homepage URL:** https://github.com/bobpanil/VIVATLAS
- **Support URL:** https://github.com/bobpanil/VIVATLAS/issues
- **Official URL:** none (it needs a domain verified in Search Console).
- **Mature content:** no.

## Privacy practices

`privacy-practices.txt` has the single purpose, a justification for each
permission, and the answers to the data-use questions.

**Privacy policy URL:**
https://github.com/bobpanil/VIVATLAS/blob/main/extension/amo/privacy-policy.txt
It is the same policy as on Firefox Add-ons and names no browser.

## Distribution

- **Payments:** free.
- **Visibility:** public.
- **Regions:** all.
