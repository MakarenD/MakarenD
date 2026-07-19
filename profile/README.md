# Profile graphics

`generate.py` builds the four SVG assets used by the profile README. It prefers
the ignored local file `profile/avatar-source.png` and falls back to the current
GitHub avatar when that file is absent.

The public `portrait-mosaic.json` contains only the selected glyph/tone grid,
not the photograph. CI reads this derived file so scheduled activity updates do
not replace the reviewed portrait. To refresh it locally:

```bash
PROFILE_GITHUB_TOKEN="$(gh auth token)" python profile/generate.py \
  --write-portrait-cache profile/portrait-mosaic.json
```

Run the deterministic checks and browser timeline QA with:

```bash
python -m pip install -r profile/requirements.txt -r profile/qa-requirements.txt
python -m playwright install chromium
python -m unittest discover -s profile/tests -v
python profile/qa_visual.py --assets dist --output qa-artifacts/timeline
```

The portrait candidate stage is intentionally separate from the production hero.
It uses the exact ignored `profile/avatar-source.png`, verifies its hash, and writes
eight dark/light vector-stencil + glyph-fill variants for owner selection:

```bash
python profile/qa_portraits.py \
  --avatar-source profile/avatar-source.png \
  --output profile/qa
```

Open `profile/qa/portrait-candidates.html` or the contact sheet
`profile/qa/portrait-candidates.png`. No candidate is copied into the production
hero until its number is explicitly selected.
