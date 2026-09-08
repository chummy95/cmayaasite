# Creative Mayaa Local Mirror

This folder contains a local static mirror of `https://creativemayaa.com/`.

## Refresh the mirror

```bash
python3 scripts/mirror_site.py https://creativemayaa.com/ site
```

## Serve locally

```bash
python3 -m http.server 4321 --directory site
```

Then open `http://127.0.0.1:4321/`.
