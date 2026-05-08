# Licensing

vc-roe is dual-licensed. The split mirrors the Wikipedia model: software under a free-software license, prose content under a Creative Commons license.

## Code: GPL-3.0-or-later

Governs all source files that are programmatically executed: Python, Bash, JSON config, install scripts.

Applies to:

- `hooks/*.py` (SessionStart, UserPromptSubmit, Stop)
- `bin/*.sh` (anchor-rewrite, sync-user-aliases)
- `install.sh`, `install.ps1`
- `test-detection.py`
- `detection-rules.json`
- `hooks/hooks.json`
- `.claude-plugin/plugin.json`
- `commands/*.md` insofar as they contain executable shell snippets (the markdown wrapper is technically content; the embedded bash blocks are code under the same license as the rest of the codebase for simplicity)

Full text in `LICENSE-CODE`.

## Content: CC BY-SA 4.0

Governs prose: methodology slices, README, CHANGELOG, this licensing explainer.

Applies to:

- `methodology-content/T0.md` through `T4.md`
- `README.md`
- `CHANGELOG.md`
- `LICENSING.md` (this file)
- `CONTRIBUTING.md`

Full text in `LICENSE-CONTENT`.

## Why dual-licensed

The plugin is half code, half methodology. CC BY-SA was designed for creative works (text, images, video) and Creative Commons explicitly recommend against using it for software (their FAQ: "We recommend against using Creative Commons licenses for software"). GPL is purpose-built for code and shares the same share-alike philosophy. Splitting along the natural code/content boundary keeps each part under the most-suitable license without losing the share-alike property.

The Wikipedia precedent: MediaWiki (the software running Wikipedia) is GPL-2.0; Wikipedia articles (the content) are CC BY-SA 4.0. Same model, different surface.

## Practical consequences

- **Forking the plugin code**: GPL-3.0 share-alike. Any modified version distributed must be under GPL-3.0-or-later, with source available.
- **Quoting from a methodology slice in a blog post / book / paper**: CC BY-SA 4.0. Attribute, link this repo, share-alike on derivatives. Verbatim quotation under fair use is fine without the share-alike clause kicking in.
- **Building a downstream product on top of this plugin**: GPL viral effect applies if the downstream is itself a derivative work. For a separate product that COMMUNICATES with this plugin via the documented hook interface (stdin/stdout JSON), the downstream is not a derivative work. Same legal posture as Linux kernel modules vs userspace, or WordPress core vs WordPress plugins.

When in doubt: ask via a GitHub issue. The license is the legal floor; explicit operator-grant exceptions can be discussed.
