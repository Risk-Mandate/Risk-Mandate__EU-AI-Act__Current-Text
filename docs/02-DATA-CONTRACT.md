# Data contract: what is in this pack and how the site consumes it

Status: v1.0, 2026-07-31. Produced by the eu-ai-act-canonical vault session
(vault id 6kdhnpfx, release v0.6.3). Every file below is hash-anchored in
MANIFEST.json at the pack root; verify before first use. Pure ASCII.

## 1. The one file most of the site renders from

data/exports/eu-ai-act-current.json - the ordered CLEAN TREE of the composed
current text (text version 2026-07-27):

  { generated_by, generated_on, disclaimer, work{...}, text_version,
    recitals: [{id, number, text}],
    chapters: [{id, label, heading, articles: [ARTICLE], sections:
      [{id, label, heading, articles: [ARTICLE]}]}],
    final_articles: [ARTICLE],
    annexes: [{id, label, heading, items: [{id, label, text}]}] }
  ARTICLE = { id, label, heading, text|null, paragraphs: [{id, label, text,
              points: [{id, label, text, points: [...]}]}], points: [...] }

Properties you may rely on: document order is legal order (inserted
provisions already spliced: 4a after 4, 60a after 60, 75a-75d after 75,
Annex XIV last); deleted provisions are ABSENT (the numbering gap stands,
e.g. Article 10 has paragraphs 1,2,3,4,6); ids follow the frozen scheme in
docs/03-ID-SCHEME-SPEC.md and are the anchor/path currency of the site.

## 2. The graphs (for derivation pages and diffs)

- data/graph/g0-nodes.json      as-published nodes (id, type, text, label,
                                heading, number, source_member). The BEFORE
                                side of every diff.
- data/graph/g2-instructions.json  the 72 amendment instruction nodes:
    { id: "eu-2026-1744/art_001/pt_009/a", path: ["(9)","(a)"],
      instruction_text, context_text[], op, level, targets[]|container,
      inserted[], sub_target?, payload: {roots[], text, xml_sha256},
      applies: {in_force}, partition, target_resolution{partition,...},
      source_member }
    This IS the machine-readable amending act. One derivation page per
    instruction. payload.text is quoted OFFICIAL text; xml_sha256 anchors
    it to the raw Formex member (named in source_member).
- data/graph/g2-manifest.json   gates 1-3 results (round-trip 72/72, zero
                                orphans, zero missing targets) - cite on
                                the verify page.
- data/graph/g3-nodes.json      composed nodes: @v2 replaced/amended nodes
                                (composed_from chains name predecessor and
                                instruction), status deleted tombstones,
                                inserted bare-id nodes. The AFTER side.
- data/graph/g3-structure.json  per-container {removed, added(after),
                                replaced} - ordering/splicing truth.
- data/graph/g3-manifest.json   gates 2b/4/5 + composition inputs (sha256
                                of raw OJ zips) + corrigenda finding.
- data/graph/g1-manifest.json   corrigenda evidence: R(01)-R(04) correct 9
                                other language versions, NOT English ->
                                English G1 empty BY EVIDENCE. Verify page
                                material.
- data/graph/delta.json         2.1e findings: definitions 68->70 (SME,
                                SMC; safety component reworded), 30 usage
                                shifts (AI Office +25 etc.), external
                                instruments 115->120. Great landing-page
                                numbers - every one traces to a provision.
- data/graph/gate6-self-test.json  the endgame differ validated TODAY:
                                composed-vs-published self-test PASSES
                                exactly (agree 1089 / differ 44 / inserted
                                125 / deleted 4). The site's headline
                                verification claim: "when EUR-Lex publishes
                                its consolidation, this machine-diffs
                                against it - the differ is already proven."

## 3. The exports (the downloads ARE these files)

data/exports/eu-ai-act-current.{html,pdf,docx,md,json,jsonld,ttl} + their
MANIFEST.json (sha256 per file - surface the hashes next to each download).
The .html is the EUR-Lex-style standalone page (authentic oj-* class
conventions) - reuse it as the "current text" page base rather than
re-rendering from scratch.

## 4. Other-versions register (for the comparison page)

From the research brief (docs/), as of 2026-07-31 - re-verify links before
publish, date the table, keep the tone fair:

| source | url | state |
|---|---|---|
| EUR-Lex consolidated | https://eur-lex.europa.eu/eli/reg/2024/1689/2024-07-12/eng | stale (2024-07-12), labelled by its own date |
| EUR-Lex act text (OJ) | https://eur-lex.europa.eu/eli/reg/2024/1689/oj | as-published, authentic, immutable by design |
| AI Act Explorer | https://artificialintelligenceact.eu/the-act/ | stale, HONESTLY labelled (states OJ 13.06.2024) |
| artificial-intelligence-act.com | https://www.artificial-intelligence-act.com/ | stale, no label |
| aiact.algolia.com | https://aiact.algolia.com/ | pre-adoption draft - never was the law |
| Bird & Bird consolidation | https://www.twobirds.com/en/insights/2026/ai-act-,-a-,-provisionally-agreed-ai-digital-omnibus-consolidated-version | superseded (built on the May compromise text; scope honestly stated) |
| The Omnibus itself | https://eur-lex.europa.eu/eli/reg/2026/1744/oj | the amending act, authentic |

## 5. The disclaimer block (verbatim, every page)

COMPOSED TEXT - NOT AUTHENTIC. This is the EU AI Act (Regulation (EU)
2024/1689) with the Digital Omnibus on AI (Regulation (EU) 2026/1744, in
force 27 July 2026) applied by a deterministic, gated parser. Only the
Official Journal publications are authentic law; no official consolidated
version existed when this was generated. Corrigenda R(01)-R(04) do not
correct the English text (verified against CELLAR). Every fragment's
derivation chain is available in the source graphs. Not legal advice.

## 6. Hash conventions

- File hashes: sha256 of bytes (as in exports/MANIFEST.json).
- Provision hashes (you generate): sha256 of the provision's text.md bytes;
  parent index.json = {"children": {name: sha256-of-child-index-or-text}},
  root hash = sha256 of provisions/index.json. Record the root hash in the
  site footer and publish/published-state.json - it is the delta baseline
  for job 1 and the version a checker cites.

## 7. Provenance chain to cite per touched provision

  text -> instruction (g2 id) -> payload xml_sha256 -> raw Formex member
  (L_202601744EN.000101.fmx.xml) -> CELLAR CELEX 32026R1744
The raw OJ bytes themselves stay in the vault (hash-anchored there; their
sha256s are in g3-manifest.json inputs) - the site cites hashes, it does
not need to host the raw zips.
