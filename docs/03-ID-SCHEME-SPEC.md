# Node ID scheme: the shared contract between the two vaults

Status: v1.0, frozen 2026-07-31. Pure ASCII.

The source vault (regulation-graph) and the canonical vault MUST agree on node
ids, because amendment-instruction targets, composed-node ancestry, and the
downstream evidence layer all join on them. This spec freezes the scheme as
implemented in code/build_graph.py and instantiated in 1,523 nodes of the source
vault's data/graph/nodes.json. Do not diverge; extend only in the marked areas.

## 1. Existing scheme (G0, as-published graph)

| type | pattern | example |
|---|---|---|
| work | eu-{year}-{number} | eu-2024-1689 |
| chapter | {work}/chapter_{ROMAN} | eu-2024-1689/chapter_III |
| section | {work}/chapter_{ROMAN}/section_{n} | eu-2024-1689/chapter_III/section_1 |
| article | {work}/art_{NNN} (zero-padded 3) | eu-2024-1689/art_010 |
| paragraph | {article}/par_{NNN} | eu-2024-1689/art_010/par_005 |
| point | {paragraph}/pt_{label} | eu-2024-1689/art_001/par_002/pt_a |
| definition | {work}/def/{slug} | eu-2024-1689/def/ai-literacy |
| recital | {work}/rec_{NNN} | eu-2024-1689/rec_039 |
| annex | {work}/annex_{ROMAN} | eu-2024-1689/annex_I |
| annex item | {annex}/item_{label}_{NNN} | eu-2024-1689/annex_I/item_10_009 |

Notes:
- Articles are ordered by number, not by chapter position; sections exist only
  where the OJ has them (Chapter III).
- Paragraph ids use par_, NOT para_ (one prose doc in the source vault says
  para_005 in passing; par_005 is correct - this spec wins).
- pt_ labels are the OJ point labels lowercased: pt_a, pt_ba, pt_i (roman-vs-
  alpha ambiguity is resolved by document order in the parser, not in the id).

## 2. Extensions for the canonical vault (new, reserved here)

INSERTED PROVISIONS keep the OJ label verbatim in the same scheme:
- Article 4a  -> eu-2024-1689/art_004a   (suffix letters allowed after NNN)
- Article 75a -> eu-2024-1689/art_075a
- point (ba)  -> {paragraph}/pt_ba
- paragraph 1a -> {article}/par_001a

TEXT VERSIONS use an @v suffix on otherwise-identical ids:
- eu-2024-1689/art_010/par_001      the as-published node (implicit @v1)
- eu-2024-1689/art_010/par_001@v2   the composed node after the Omnibus
The bare id always denotes the as-published text. Composed graphs are sparse:
only touched provisions get @v2+ nodes. Version numbers are per-node, ordered
by the applying instrument's entry-into-force date.

AMENDMENT INSTRUCTION nodes live under the AMENDING work, addressed by the
enacting-terms position they occupy, with a stable letter for sub-instructions:
- eu-2026-1744/art_001/pt_009/a   = "Article 10 ... (a) paragraph 1 is replaced"
- eu-2026-1744/art_001/pt_009/b   = "(b) paragraph 5 is deleted"
An instruction with no lettered sub-items omits the trailing segment.

CORRIGENDUM instruction nodes: {work}-r{NN}/inst_{NNN}
- eu-2024-1689-r01/inst_001

TOMBSTONES are not new ids: a deleted provision's @vN node carries
status: "deleted" and valid_to; the bare id remains resolvable forever.

## 3. Join rules

1. Instruction.target is a bare G0 id (or an @vN id when an instruction edits
   already-amended text - not the case in the Omnibus, reserved).
2. Composed node ancestry: composed_from = [predecessor id, instruction id+].
3. The source vault joins on bare ids only, until it opts into composed views.
4. Any id-scheme change is a MAJOR event: version this spec, notify both vaults,
   never reuse an id for different content.
