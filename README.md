# EU AI Act — Current Text (derived, verifiable)

![release](https://img.shields.io/badge/release-v0.1.0-blue)

**The composed current text of the EU AI Act** — Regulation (EU) 2024/1689
with the Digital Omnibus on AI (Regulation (EU) 2026/1744, in force
27 July 2026) applied — published so it can be **checked**, not just read.

> **Derived, not canonical.** Only Official Journal publications are
> authentic law. No official consolidated version existed when this was
> generated. Not legal advice.

**Site:** https://eu-ai-act.standards.riskmandate.ai
**Repo:** https://github.com/Risk-Mandate/Risk-Mandate__EU-AI-Act__Current-Text
**By:** [RiskMandate.ai](https://riskmandate.ai)

## Why this exists

Four days after the Omnibus entered into force there was no consolidated
text anywhere — the EU's own portal still served the 2024 text. Details and
the survey of public sources: the research brief in `docs/` (also linked
from the site).

## How this is different: you can verify it

1. **Per-provision derivation** — every changed provision has a page showing
   the original text, the exact amending instruction (quoted official text,
   hash-anchored to the OJ Formex bytes), and the result. Checking one
   paragraph takes ~2 minutes.
2. **Per-provision issues** — spot a problem in Article 10(6)? File an issue
   against exactly that provision (template provided).
3. **Published hashes** — every provision carries its content sha256 in
   `provisions/`; the tree rolls up to one root hash (in the site footer),
   so a checker can state precisely which version they examined.

## For agents and LLMs

No scraping required. Every page is published three ways on the same slug:

| what | where |
|---|---|
| index of everything | `/llms.txt` |
| any page as Markdown | `<slug>.md` — e.g. `/articles/art_010.md` |
| any page as structured data | `<slug>.llm.json` — provision ids, statuses, sha256s |
| the whole Act in one file | `/exports/eu-ai-act-current.md` (also JSON, JSON-LD, Turtle) |

**The workflow:** fetch `/llms.txt`, find the slug, fetch its `.md`. That's it.

Each twin repeats the disclaimer and the provisions root hash, because an
agent may hold only that one file — and anything quoting this text needs to
say that it is derived, not authentic law, and cite which version it read.
Twins are generated from the same data as the HTML (never scraped back out
of it), and a build gate fails if any page loses its twin.

## Repository layout (ownership split by path)

| path | owner | direction |
|---|---|---|
| `exports/`, `data/` | **generated** (vault pipeline) | vault → repo — never hand-edit |
| `provisions/` | **generated** (site build) — the published hash tree, committed as the delta baseline | never hand-edit |
| `site/` | **generated** (site build) — rebuilt in CI on every push, not committed | never hand-edit |
| issues, PRs to contributed paths | **contributed** (you!) | repo → vault, as proposals |

A correction never edits published text directly: it is reviewed in the
authoring vault and, if accepted, the next publish regenerates the text —
with your issue linked in the change record.

## Build

The site is generated deterministically from the packed data — same inputs,
same site. No hand edits to generated files.

```
python3 scripts/build_site.py                    # emit site/ + provisions/
python3 scripts/build_site.py --assemble _pages  # ...plus the served layout
cd _pages && python3 -m http.server              # local preview
```

Page links are relative to the *served* layout — pages at the root with
`exports/`, `provisions/` and `data/` as siblings — so preview from an
assembled tree, not from `site/`.

Gates fail the build if: any touched provision lacks a derivation page or
`derivation.json`; the provisions hash tree does not recompute identically;
any page is missing the disclaimer block; or the word "canonical" appears
describing our text.

## Branches, versions, deploys

- `dev` is the default branch. Every push to `dev` auto-increments the
  release tag (patch bump) and deploys the site to GitHub Pages.
- Pushes to `main` (promotion) bump the minor version and deploy.
- The current version lives in the root `version` file and in the release
  badge above; both are maintained by the CI pipeline — do not edit by hand.
- Job 1 of the two-pass pipeline (vault → repo delta, `publish/`) runs on
  the operator's machine and lands generated commits on `dev`; job 2 (the
  Pages deploy) runs here on every push.

## Provenance chain (per changed provision)

`text → instruction → payload sha256 → OJ Formex member → CELLAR 32026R1744`

## Domain / Pages setup checklist (operator side)

- [ ] DNS: explicit CNAME record `eu-ai-act.standards.riskmandate.ai` →
      `risk-mandate.github.io` (**no wildcard record** — wildcard DNS is a
      subdomain-takeover surface).
- [ ] Repo Settings → Pages → Source: **GitHub Actions**.
- [ ] Repo Settings → Pages → Custom domain:
      `eu-ai-act.standards.riskmandate.ai`.
- [ ] Org Settings → Pages → **Verified domains**: verify
      `standards.riskmandate.ai` (or `riskmandate.ai`).
- [ ] Enforce HTTPS once the certificate provisions.

## Licence

Our derived material: CC BY 4.0. Reproduced EU legal texts: © European
Union, reuse per Commission Decision 2011/833/EU. Code in `scripts/` and
`publish/`: Apache-2.0 (see `LICENSE`). Full split: `LICENSE.md`.
