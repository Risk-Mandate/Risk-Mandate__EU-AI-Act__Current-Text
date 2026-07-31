# Contributing — how checking works here

**The most valuable contribution is verification.** Pick any changed
provision, open its derivation page, and check the result against the
instruction (both are shown; the instruction is quoted official text,
hash-anchored). It takes about two minutes per provision.

## Found a problem?
Open an issue with the **Provision check** template. Name the provision id
(e.g. `eu-2024-1689/art_010/par_006`), the root hash from the site footer
(so we know exactly which version you checked), and what you found.

## Why your PR cannot edit the text directly
Files under `site/`, `exports/`, `provisions/` and `data/` are GENERATED
by the authoring vault's gated pipeline; a hand edit would be overwritten
by the next publish and would break the hash tree. Corrections are
PROPOSALS: they are reviewed in the authoring system and, when accepted,
the next publish regenerates the text — your issue gets linked in the
change record. This is not a brush-off; it is what keeps every published
byte traceable to official sources.

## Verified a provision and found it correct?
That is worth recording too — file the same template with "checks out".
The public register of checked provisions is the point of this project.
