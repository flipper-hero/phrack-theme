# Contributing

Keep the pack small: three readable screens, native one-bit pixels, the shared
Magazine animation rhythm, and the firmware's normal icons.

- Edit article titles and profile names in `assets/theme.json`.
- Edit Magazine artwork in `assets/magazine/` at its native 128 × 64 resolution.
- Keep text within the screen; wrap long titles instead of reducing its size.
- Include hardware results with the firmware name/version when reporting compatibility.
- Regenerate previews with `python build.py --previews previews` and run
  `python -m unittest discover -s tests -v` before opening a pull request.
- Intentional artwork changes require reviewing the regenerated previews and
  updating the known-good pack fingerprint in `tests/test_build.py`.

Artwork and asset contributions use CC0 1.0; code and documentation contributions
use MIT. Contribute work you are entitled to publish and state its provenance.
AI-assisted contributions are welcome here when disclosed and reviewed. Respect
the separate contribution policies of any firmware project you submit to.

Release maintainers: pushing a `v*` tag runs CI and publishes the three ZIPs and
`SHA256SUMS` automatically. Only tag a reviewed commit with a passing build.
