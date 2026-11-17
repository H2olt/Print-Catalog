# Print Catalog

Browsable web catalog of a 3D-print model library, generated from an [Orynt3D](https://orynt3d.com) database and published on Netlify.

## How it works

Two stages, split by what needs your PC:

```
Orynt3D database (your PC only)
   │  export.bat  →  export.py
   ▼
data/catalog.json + data/thumbs/     ← committed to git
   │  build.py  (Netlify runs this on every push)
   ▼
dist/                                ← the live site (never committed)
```

| File | Stage | Purpose |
|---|---|---|
| `export.py` / `export.bat` | 1 · PC | Reads Orynt3D DB, makes thumbnails, writes `data/` |
| `config.json` | 1 · PC | Your local paths. Copy from `config.example.json`. Not committed. |
| `build.py` | 2 · anywhere | Combines `data/` + `site/` + `theme.json` into `dist/` |
| `site/index.html` | 2 | The viewer design. Other files in `site/` are copied as-is. |
| `theme.json` | 2 | Optional colour/font/layout overrides. Copy from `theme.example.json`. |
| `preview.bat` | 2 · PC | Builds locally and opens the result in your browser |
| `netlify.toml` | 2 | Tells Netlify how to build |

## Changing things

- **Design** — edit `site/` or `theme.json`, commit, push. Netlify redeploys.
- **Catalog contents** — close Orynt3D, double-click `export.bat`, commit and push `data/`.
- **Try before publishing** — push to a branch other than the live one; Netlify gives it a preview URL.

## First-time setup on your PC

1. Clone this repo with [GitHub Desktop](https://desktop.github.com).
2. Copy `config.example.json` → `config.json` and fill in your paths.
3. `pip install Pillow` (export only — the build needs nothing extra).

## Licensing note

Each model's publisher sets its own commercial terms. This catalog is a personal index and must not be used to sell prints of models you aren't licensed to sell. `export.py` asks you to confirm this on every run.
