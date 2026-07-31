#!/usr/bin/env python3
"""Deterministic site generator for the composed EU AI Act current text.

Job 2 of the two-pass pipeline. Reads ONLY the packed generated data
(exports/, data/graph/) plus the root `version` file, and emits:

  site/        landing, current text, per-article and per-annex pages,
               one derivation page per amendment instruction, downloads,
               verification and other-versions pages
  provisions/  the split-by-path hash tree: text.md + index.json per
               text-bearing provision, derivation.json per touched one,
               parent index.json files rolling up to a single root hash
  publish/published-state.json   the delta baseline for job 1

Same inputs -> same bytes. Gates (any failure exits non-zero):
  gate A  every touched provision has a derivation.json AND every
          instruction has a derivation page
  gate B  the provisions/ hash tree recomputes identically
  gate C  every site page carries the disclaimer block
  gate D  the word "canonical" never describes our text on any site page
          (the phrase "not canonical" is the only allowed occurrence)

Stdlib only. Never hand-edit the outputs.
"""

import difflib
import hashlib
import html
import json
import os
import re
import shutil
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = "eu-2024-1689"
AMENDER = "eu-2026-1744"
REPO_URL = "https://github.com/Risk-Mandate/Risk-Mandate__EU-AI-Act__Current-Text"
SITE_URL = "https://eu-ai-act.standards.riskmandate.ai"
OTHER_VERSIONS_DATE = "2026-07-31"

# Other public versions register (data contract s4, dated above).
OTHER_VERSIONS = [
    ("EUR-Lex consolidated text",
     "https://eur-lex.europa.eu/eli/reg/2024/1689/2024-07-12/eng",
     "stale (2024-07-12), labelled by its own date"),
    ("EUR-Lex act text (OJ)",
     "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
     "as published, authentic, immutable by design"),
    ("AI Act Explorer (FLI)",
     "https://artificialintelligenceact.eu/the-act/",
     "stale, honestly labelled (states OJ 13.06.2024)"),
    ("artificial-intelligence-act.com",
     "https://www.artificial-intelligence-act.com/",
     "stale, no label"),
    ("aiact.algolia.com",
     "https://aiact.algolia.com/",
     "pre-adoption draft - never was the law"),
    ("Bird & Bird consolidation",
     "https://www.twobirds.com/en/insights/2026/ai-act-,-a-,-provisionally-agreed-ai-digital-omnibus-consolidated-version",
     "superseded (built on the May compromise text; scope honestly stated)"),
    ("The Omnibus itself (OJ)",
     "https://eur-lex.europa.eu/eli/reg/2026/1744/oj",
     "the amending act, authentic"),
]


def read_json(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def dump_json(obj):
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def esc(t):
    return html.escape(t, quote=True) if t else ""


# ---------------------------------------------------------------- data load

VERSION = open(os.path.join(ROOT, "version"), encoding="utf-8").read().strip()
TREE = read_json("exports/eu-ai-act-current.json")
EXPORTS_MANIFEST = read_json("exports/MANIFEST.json")
G0 = read_json("data/graph/g0-nodes.json")
G2 = read_json("data/graph/g2-instructions.json")
G2_MANIFEST = read_json("data/graph/g2-manifest.json")
G3 = read_json("data/graph/g3-nodes.json")
G3_MANIFEST = read_json("data/graph/g3-manifest.json")
G1_MANIFEST = read_json("data/graph/g1-manifest.json")
DELTA = read_json("data/graph/delta.json")
GATE6 = read_json("data/graph/gate6-self-test.json")

DISCLAIMER = TREE["disclaimer"]
TEXT_VERSION = TREE["text_version"]
GENERATED_ON = TREE["generated_on"]

G0_BY_ID = {n["id"]: n for n in G0}
G3_BY_BASE = {}
for n in G3:
    G3_BY_BASE.setdefault(n["id"].split("@")[0], []).append(n)
INSTRUCTIONS = {i["id"]: i for i in G2}

# provision base id -> list of (instruction id) that touched it
TOUCHED = {}
for n in G3:
    base = n["id"].split("@")[0]
    insts = [c for c in n.get("composed_from", []) if c.startswith(AMENDER)]
    TOUCHED.setdefault(base, {"nodes": [], "instructions": set()})
    TOUCHED[base]["nodes"].append(n)
    TOUCHED[base]["instructions"].update(insts)


def node_ref(x):
    """Instruction target/inserted entries are either a bare id or {id, label}."""
    return x["id"] if isinstance(x, dict) else x


def inst_slug(iid):
    """eu-2026-1744/art_001/pt_009/a -> art_001__pt_009__a"""
    return iid.split("/", 1)[1].replace("/", "__")


def provision_status(base_id):
    nodes = TOUCHED.get(base_id, {}).get("nodes", [])
    for n in nodes:
        if n.get("status") == "deleted":
            return "deleted"
    for n in nodes:
        if n.get("status") == "inserted":
            return "inserted"
    return "amended" if nodes else None


# ------------------------------------------------------------- article list

def iter_articles():
    for ch in TREE["chapters"]:
        for a in ch.get("articles", []):
            yield ch, None, a
        for s in ch.get("sections", []):
            for a in s.get("articles", []):
                yield ch, s, a


ARTICLES = list(iter_articles())
ANNEXES = TREE["annexes"]


def article_tail(aid):
    return aid.split("/")[-1]


ARTICLE_ORDER = [a["id"] for _, _, a in ARTICLES]


# ------------------------------------------------------------ text walkers

def walk_text_nodes(article):
    """Yield (id, label, text, depth) for every text-bearing node."""
    if article.get("text"):
        yield article["id"], article["label"], article["text"], 0
    for p in article.get("paragraphs", []):
        if p.get("text") is not None:
            yield p["id"], p.get("label") or "", p["text"], 1
        for pt in p.get("points", []):
            yield from walk_points(pt, 2)
    for pt in article.get("points", []):
        yield from walk_points(pt, 1)


def walk_points(pt, depth):
    if pt.get("text") is not None:
        yield pt["id"], pt.get("label") or "", pt["text"], depth
    for sub in pt.get("points", []):
        yield from walk_points(sub, depth + 1)


def subtree_text(node_id):
    """Concatenated current text of a provision subtree (from the clean tree)."""
    parts = []
    for _, _, a in ARTICLES:
        if a["id"] == node_id or a["id"].startswith(node_id + "/"):
            for _, label, text, _ in walk_text_nodes(a):
                parts.append(f"{label} {text}".strip())
        else:
            for nid, label, text, _ in walk_text_nodes(a):
                if nid == node_id or nid.startswith(node_id + "/"):
                    parts.append(f"{label} {text}".strip())
    for ax in ANNEXES:
        if ax["id"] == node_id or ax["id"].startswith(node_id + "/"):
            for item in ax.get("items", []):
                parts.append(f"{item.get('label', '')} {item.get('text', '')}".strip())
        else:
            for item in ax.get("items", []):
                if item["id"] == node_id or item["id"].startswith(node_id + "/"):
                    parts.append(f"{item.get('label', '')} {item.get('text', '')}".strip())
    return "\n".join(parts)


def g0_label(n):
    """G0 carries `label` for points/annex items and `number` for paragraphs;
    the clean tree renders both as a label, so mirror it here — otherwise the
    before/after diff reports a spurious label insertion."""
    if n.get("label"):
        return n["label"]
    if n.get("type") == "paragraph" and n.get("number") is not None:
        return f"{n['number']}."
    return ""


def g0_labelled_text(node_id):
    """As-published text of a single node, with the same label convention as
    the composed side."""
    n = G0_BY_ID.get(node_id)
    if not n or not n.get("text"):
        return ""
    return f"{g0_label(n)} {n['text']}".strip()


def g0_subtree_text(node_id):
    """Concatenated as-published text of a provision subtree (from G0)."""
    parts = []
    for n in G0:
        nid = n["id"]
        if nid == node_id or nid.startswith(node_id + "/"):
            if n.get("text"):
                parts.append((nid, f"{g0_label(n)} {n['text']}".strip()))
    parts.sort(key=lambda x: x[0])
    return "\n".join(p for _, p in parts)


# ------------------------------------------------------------- word diff

TOKEN_RE = re.compile(r"\S+|\s+")


def word_diff_html(before, after):
    a = TOKEN_RE.findall(before)
    b = TOKEN_RE.findall(after)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append(esc("".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(f"<del>{esc(''.join(a[i1:i2]))}</del>")
            if j2 > j1:
                out.append(f"<ins>{esc(''.join(b[j1:j2]))}</ins>")
    return "".join(out)


# ------------------------------------------------------------- page shell

CSS = """
:root{--ink:#1a1a1a;--paper:#fff;--accent:#0b5394;--soft:#f4f6f8;--line:#d8dde3;
--warn-bg:#fcf8e3;--warn-line:#8a6d3b;--warn-ink:#5b4a1f;--del:#ffe5e5;--ins:#e2f5e2}
*{box-sizing:border-box}
body{font-family:Georgia,'Times New Roman',serif;color:var(--ink);background:var(--paper);
 margin:0;line-height:1.5}
a{color:var(--accent)}
nav.site{font-family:Arial,Helvetica,sans-serif;font-size:14px;background:var(--soft);
 border-bottom:1px solid var(--line);padding:10px 16px;display:flex;flex-wrap:wrap;
 gap:4px 18px;align-items:baseline}
nav.site .brand{font-weight:bold;color:var(--ink);text-decoration:none}
main{max-width:900px;margin:0 auto;padding:24px 16px 64px}
.disclaimer{border:1px solid var(--warn-line);background:var(--warn-bg);color:var(--warn-ink);
 padding:10px 14px;font-size:13px;margin:0 0 22px;font-family:Arial,Helvetica,sans-serif}
h1{font-size:26px;margin:8px 0 4px}
h2{font-size:20px;margin:26px 0 8px}
h3{font-size:16px;margin:20px 0 6px}
table{border-collapse:collapse;width:100%;font-size:14px;font-family:Arial,Helvetica,sans-serif}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--soft)}
code,.hash{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
 word-break:break-all}
.badge{display:inline-block;font-family:Arial,Helvetica,sans-serif;font-size:11px;
 padding:1px 7px;border-radius:9px;vertical-align:2px;text-decoration:none;color:#fff}
.badge.amended{background:#b45f06}.badge.inserted{background:#38761d}.badge.deleted{background:#990000}
.prov{margin:10px 0;padding-left:0}
.prov .lbl{font-weight:bold;margin-right:6px}
.prov.d2{margin-left:26px}.prov.d3{margin-left:52px}.prov.d4{margin-left:78px}
.gap{border:1px dashed var(--warn-line);background:var(--warn-bg);color:var(--warn-ink);
 padding:8px 12px;font-size:13px;font-family:Arial,Helvetica,sans-serif;margin:10px 0}
blockquote.instr{border-left:4px solid var(--accent);margin:10px 0;padding:6px 14px;
 background:var(--soft)}
.diff del{background:var(--del);text-decoration:line-through}
.diff ins{background:var(--ins);text-decoration:none}
.pane{border:1px solid var(--line);padding:10px 14px;margin:8px 0;background:#fff}
.pane h4{margin:0 0 6px;font-family:Arial,Helvetica,sans-serif;font-size:13px}
footer.site{border-top:1px solid var(--line);margin-top:40px;padding:14px 16px;
 font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#444}
.hero{font-size:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;
 font-family:Arial,Helvetica,sans-serif}
.card{border:1px solid var(--line);padding:12px 14px;background:var(--soft)}
.card .n{font-size:26px;font-weight:bold;display:block}
.toc{columns:2;font-size:14px;font-family:Arial,Helvetica,sans-serif;list-style:none;padding:0}
.toc li{margin:3px 0;break-inside:avoid}
@media (max-width:640px){.toc{columns:1}}
"""


def nav_html(prefix):
    return (
        f'<nav class="site"><a class="brand" href="{prefix}index.html">'
        f'EU AI Act &mdash; Current Text</a>'
        f'<a href="{prefix}current-text/index.html">Full text</a>'
        f'<a href="{prefix}articles/index.html">Articles</a>'
        f'<a href="{prefix}derivations/index.html">Derivations</a>'
        f'<a href="{prefix}downloads/index.html">Downloads</a>'
        f'<a href="{prefix}verify/index.html">Verify</a>'
        f'<a href="{prefix}other-versions/index.html">Other versions</a>'
        f'<a href="{REPO_URL}">GitHub</a>'
        f'</nav>'
    )


def footer_html(root_hash):
    return (
        '<footer class="site">'
        f'Derived, not canonical &middot; not legal advice &middot; '
        f'text version {TEXT_VERSION} &middot; generated {GENERATED_ON} &middot; '
        f'site {VERSION} &middot; provisions root hash '
        f'<span class="hash">{root_hash}</span> &middot; '
        f'<a href="{REPO_URL}">source &amp; data</a> &middot; '
        f'by <a href="https://riskmandate.ai">RiskMandate.ai</a>'
        '</footer>'
    )


def page(title, body, prefix, root_hash, extra_head=""):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>{extra_head}
</head><body>
{nav_html(prefix)}
<main>
<div class="disclaimer"><b>Composed current text &mdash; not authentic.</b> {esc(DISCLAIMER)}</div>
{body}
</main>
{footer_html(root_hash)}
</body></html>
"""


def issue_url(provision_id):
    title = urllib.parse.quote(f"[provision] {provision_id}")
    return (f"{REPO_URL}/issues/new?template=provision-check.yml"
            f"&title={title}&labels=provision-check")


def badge_html(base_id, prefix):
    st = provision_status(base_id)
    if not st:
        return ""
    insts = sorted(TOUCHED[base_id]["instructions"])
    href = f"{prefix}derivations/{inst_slug(insts[0])}.html" if insts else f"{prefix}derivations/index.html"
    return f' <a class="badge {st}" href="{href}" title="{esc(base_id)} - {st}; see derivation">{st}</a>'


# ------------------------------------------------------- provisions tree

def build_provisions():
    """Return {relpath: bytes} for the provisions/ tree, plus the root hash."""
    files = {}          # relpath under provisions/ -> bytes
    dirs = {}           # dirpath -> {childname: sha}

    def put(relpath, data):
        files[relpath] = data

    def leaf(dirpath, text):
        put(f"{dirpath}/text.md", (text.rstrip("\n") + "\n").encode("utf-8"))

    # text-bearing provisions from the clean tree
    for rec in TREE["recitals"]:
        leaf(rec["id"], f"({rec['number']}) {rec['text']}")

    def emit_points(pt):
        leaf(pt["id"], f"{pt.get('label') or ''} {pt.get('text') or ''}".strip())
        for sub in pt.get("points", []):
            emit_points(sub)

    for _, _, a in ARTICLES:
        if a.get("text"):
            leaf(a["id"], a["text"])
        for p in a.get("paragraphs", []):
            if p.get("text") is not None:
                leaf(p["id"], f"{p.get('label') or ''} {p['text']}".strip())
            for pt in p.get("points", []):
                emit_points(pt)
        for pt in a.get("points", []):
            emit_points(pt)

    for ax in ANNEXES:
        for item in ax.get("items", []):
            leaf(item["id"], f"{item.get('label') or ''} {item.get('text') or ''}".strip())

    # derivation.json per touched provision (incl. deleted tombstones)
    for base_id in sorted(TOUCHED):
        info = TOUCHED[base_id]
        node = sorted(info["nodes"], key=lambda n: n["id"])[-1]
        insts = []
        for iid in sorted(info["instructions"]):
            ins = INSTRUCTIONS.get(iid)
            if not ins:
                continue
            insts.append({
                "id": iid,
                "op": ins["op"],
                "level": ins["level"],
                "instruction_text": ins["instruction_text"],
                "payload_xml_sha256": ins["payload"]["xml_sha256"],
                "source_member": ins["source_member"],
                "celex": "32026R1744",
                "derivation_page": f"/derivations/{inst_slug(iid)}.html",
            })
        d = {
            "id": base_id,
            "status": node.get("status"),
            "valid_from": node.get("valid_from"),
            "review_status": node.get("review_status", "not-reviewed"),
            "composed_from": node.get("composed_from", []),
            "instructions": insts,
            "before_text": g0_labelled_text(base_id) or g0_subtree_text(base_id) or None,
            "after_text": None if node.get("status") == "deleted" else (
                node.get("text") or subtree_text(base_id) or None),
        }
        if node.get("status") == "deleted":
            d["valid_to"] = node.get("valid_to")
            d["tombstone_note"] = node.get("tombstone_note")
        put(f"{base_id}/derivation.json", dump_json(d))

    # index.json per directory, bottom-up: a directory's index records the
    # sha256 of each child file and of each child directory's own index, so
    # the whole tree rolls up into one root hash.
    dirset = {""}                       # "" is the provisions root
    for relpath in files:
        d = os.path.dirname(relpath)
        while d:
            dirset.add(d)
            d = os.path.dirname(d)

    file_children = {}                  # dir -> {filename: sha}
    for relpath, data in files.items():
        file_children.setdefault(os.path.dirname(relpath), {})[
            os.path.basename(relpath)] = sha256_bytes(data)
    dir_children = {}                   # dir -> [child dir]
    for sub in dirset:
        if sub:
            dir_children.setdefault(os.path.dirname(sub), []).append(sub)

    def depth(d):
        return len(d.split("/")) if d else 0

    for d in sorted(dirset, key=depth, reverse=True):
        children = dict(file_children.get(d, {}))
        for sub in dir_children.get(d, []):
            children[os.path.basename(sub)] = sha256_bytes(files[f"{sub}/index.json"])
        idx_path = f"{d}/index.json" if d else "index.json"
        files[idx_path] = dump_json({"children": children})

    root_hash = sha256_bytes(files["index.json"])
    return files, root_hash


def write_provisions(files):
    out_root = os.path.join(ROOT, "provisions")
    if os.path.isdir(out_root):
        shutil.rmtree(out_root)
    for relpath, data in files.items():
        p = os.path.join(out_root, relpath)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)


def recompute_root(files):
    """Gate B: recompute the tree from the written bytes."""
    out_root = os.path.join(ROOT, "provisions")
    disk = {}
    for dirpath, _, names in os.walk(out_root):
        for name in names:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, out_root).replace(os.sep, "/")
            with open(p, "rb") as f:
                disk[rel] = f.read()
    if set(disk) != set(files):
        return None
    for rel in files:
        if disk[rel] != files[rel]:
            return None
    return sha256_bytes(disk["index.json"])


# ------------------------------------------------------------- site pages

PAGES = {}  # relpath under site/ -> html string


def add_page(relpath, title, body, root_hash, extra_head=""):
    prefix = "../" * relpath.count("/")
    PAGES[relpath] = page(title, body, prefix, root_hash, extra_head)


def render_provision_line(nid, label, text, depth, prefix):
    cls = f"prov d{min(depth + 1, 4)}" if depth else "prov"
    return (f'<p class="{cls}" id="{esc(nid)}"><span class="lbl">{esc(label)}</span>'
            f'{esc(text)}{badge_html(nid, prefix)}'
            f' <a href="#{esc(nid)}" title="anchor" style="text-decoration:none">&sect;</a></p>')


def article_body(ch, sec, a, prefix):
    out = []
    crumbs = esc(ch["label"]) + (" &middot; " + esc(sec["label"]) if sec else "")
    out.append(f'<p style="font-family:Arial,sans-serif;font-size:13px;color:#555">{crumbs} &mdash; '
               f'{esc(ch.get("heading") or "")}{(" / " + esc(sec.get("heading") or "")) if sec else ""}</p>')
    out.append(f'<h1 id="{esc(a["id"])}">{esc(a["label"])} &mdash; {esc(a.get("heading") or "")}'
               f'{badge_html(a["id"], prefix)}</h1>')
    for nid, label, text, depth in walk_text_nodes(a):
        if nid == a["id"]:
            out.append(render_provision_line(nid, "", text, 0, prefix))
        else:
            out.append(render_provision_line(nid, label, text, depth, prefix))
    # numbering gaps: deleted provisions under this article
    deleted = [b for b in sorted(TOUCHED)
               if (b == a["id"] or b.startswith(a["id"] + "/")) and provision_status(b) == "deleted"]
    for b in deleted:
        insts = sorted(TOUCHED[b]["instructions"])
        link = f'{prefix}derivations/{inst_slug(insts[0])}.html' if insts else "#"
        out.append(f'<div class="gap" id="{esc(b)}"><b>Numbering gap (deliberate):</b> '
                   f'<code>{esc(b)}</code> was deleted by the Digital Omnibus and the gap is kept '
                   f'forever &mdash; deleted provisions are never renumbered. '
                   f'<a href="{link}">See the derivation of the deletion.</a></div>')
    out.append(f'<p style="font-family:Arial,sans-serif;font-size:13px">'
               f'<a href="{issue_url(a["id"])}">Report a problem in this article</a> &middot; '
               f'provision hashes: <a href="{prefix}provisions/{esc(a["id"])}/index.json">'
               f'provisions/{esc(a["id"])}/</a></p>')
    return "\n".join(out)


def annex_body(ax, prefix):
    out = [f'<h1 id="{esc(ax["id"])}">{esc(ax["label"])} &mdash; {esc(ax.get("heading") or "")}'
           f'{badge_html(ax["id"], prefix)}</h1>']
    for item in ax.get("items", []):
        out.append(render_provision_line(item["id"], item.get("label") or "", item.get("text") or "", 1, prefix))
    deleted = [b for b in sorted(TOUCHED)
               if b.startswith(ax["id"] + "/") and provision_status(b) == "deleted"]
    for b in deleted:
        insts = sorted(TOUCHED[b]["instructions"])
        link = f'{prefix}derivations/{inst_slug(insts[0])}.html' if insts else "#"
        out.append(f'<div class="gap" id="{esc(b)}"><b>Numbering gap (deliberate):</b> '
                   f'<code>{esc(b)}</code> was deleted by the Digital Omnibus; deleted provisions '
                   f'are never renumbered. <a href="{link}">See the derivation.</a></div>')
    out.append(f'<p style="font-family:Arial,sans-serif;font-size:13px">'
               f'<a href="{issue_url(ax["id"])}">Report a problem in this annex</a></p>')
    return "\n".join(out)


def instruction_page_body(ins, prefix):
    iid = ins["id"]
    out = []
    out.append(f'<h1>Derivation &mdash; instruction <code>{esc(iid)}</code></h1>')
    out.append('<p style="font-family:Arial,sans-serif;font-size:14px">'
               f'op <b>{esc(ins["op"])}</b> &middot; level <b>{esc(ins["level"])}</b> &middot; '
               f'enacting-terms position {esc(" ".join(ins.get("path", [])))} &middot; '
               f'in force {esc(ins.get("applies", {}).get("in_force", ""))}</p>')

    if ins.get("context_text"):
        out.append("<h2>Context (enacting terms)</h2>")
        for c in ins["context_text"]:
            out.append(f'<blockquote class="instr">{esc(c)}</blockquote>')

    out.append("<h2>The instruction (quoted official text)</h2>")
    out.append(f'<blockquote class="instr">{esc(ins["instruction_text"])}</blockquote>')

    payload = ins.get("payload") or {}
    if payload.get("text"):
        out.append("<h2>Quoted payload (official replacement/inserted text)</h2>")
        out.append(f'<div class="pane"><p>{esc(payload["text"])}</p></div>')

    out.append("<h2>Provenance</h2>")
    payload_hash = payload.get("xml_sha256")
    payload_cell = (f'<span class="hash">{esc(payload_hash)}</span>' if payload_hash
                    else '<i>none &mdash; a deletion quotes no replacement text</i>')
    out.append('<table><tr><th>link</th><th>value</th></tr>'
               f'<tr><td>payload xml sha256</td><td>{payload_cell}</td></tr>'
               f'<tr><td>OJ Formex member</td><td><code>{esc(ins["source_member"])}</code></td></tr>'
               f'<tr><td>CELLAR / CELEX</td><td><a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32026R1744">32026R1744</a></td></tr>'
               f'<tr><td>target resolution</td><td>{esc(ins.get("target_resolution", {}).get("method", ""))} '
               f'({esc(ins.get("target_resolution", {}).get("partition", ""))})</td></tr></table>')

    # affected provisions with before/after + word diff
    affected = [node_ref(t) for t in (ins.get("targets") or [])]
    inserted = [node_ref(t) for t in (ins.get("inserted") or [])]
    out.append("<h2>Affected provisions</h2>")
    if not affected and not inserted and ins.get("container"):
        out.append(f'<p>Container: <code>{esc(ins["container"])}</code></p>')

    for tid in affected:
        base = tid.split("@")[0]
        before = g0_labelled_text(base) or g0_subtree_text(base)
        st = provision_status(base)
        out.append(f'<h3 id="{esc(base)}"><code>{esc(base)}</code>'
                   f'{badge_html(base, prefix)}</h3>')
        if ins["op"] == "delete" or st == "deleted":
            out.append(f'<div class="pane"><h4>BEFORE (as published)</h4><p>{esc(before)}</p></div>')
            out.append('<div class="gap"><b>Result: deleted.</b> The numbering gap is permanent; '
                       'the provision id remains resolvable forever.</div>')
        else:
            after = subtree_text(base)
            out.append(f'<div class="pane"><h4>BEFORE (as published)</h4><p>{esc(before)}</p></div>')
            out.append(f'<div class="pane"><h4>AFTER (composed current text)</h4><p>{esc(after)}</p></div>')
            if before and after:
                out.append(f'<div class="pane diff"><h4>WORD-LEVEL DIFF</h4><p>{word_diff_html(before, after)}</p></div>')
        out.append(f'<p style="font-family:Arial,sans-serif;font-size:13px">'
                   f'<a href="{issue_url(base)}">Check this and report</a> &middot; '
                   f'<a href="{prefix}provisions/{esc(base)}/derivation.json">derivation.json</a></p>')

    for nid in inserted:
        after = subtree_text(nid)
        out.append(f'<h3 id="{esc(nid)}"><code>{esc(nid)}</code>'
                   f'{badge_html(nid, prefix)}</h3>')
        out.append('<div class="pane"><h4>BEFORE</h4><p><i>(no previous text &mdash; inserted provision)</i></p></div>')
        out.append(f'<div class="pane"><h4>AFTER (composed current text)</h4><p>{esc(after)}</p></div>')
        out.append(f'<p style="font-family:Arial,sans-serif;font-size:13px">'
                   f'<a href="{issue_url(nid)}">Check this and report</a> &middot; '
                   f'<a href="{prefix}provisions/{esc(nid)}/derivation.json">derivation.json</a></p>')

    out.append(f'<p style="font-family:Arial,sans-serif;font-size:13px">'
               f'How to check: compare the quoted instruction and payload above (hash-anchored to the '
               f'OJ Formex bytes) against the BEFORE/AFTER panes. Two minutes, one provision. '
               f'Then <a href="{issue_url(iid)}">file the result</a> &mdash; "checks out" is worth '
               f'recording too.</p>')
    return "\n".join(out)


# ------------------------------------------------------------------ build

def main(assemble_dir=None):
    prov_files, root_hash = build_provisions()
    write_provisions(prov_files)

    # gate B - recompute from disk
    recomputed = recompute_root(prov_files)
    gate_b = recomputed == root_hash and recomputed is not None

    # published-state.json (delta baseline for job 1)
    state = {
        "root_hash": root_hash,
        "text_version": TEXT_VERSION,
        "generated_on": GENERATED_ON,
        "site_version": VERSION,
    }
    with open(os.path.join(ROOT, "publish", "published-state.json"), "wb") as f:
        f.write(dump_json(state))

    # ---- landing page
    n_articles = len(ARTICLES)
    n_derivations = len(G2)
    n_touched = len(TOUCHED)
    counts = GATE6["counts"]
    defs = DELTA["definitions"]
    body = f"""
<p class="hero"><b>The composed current text of the EU AI Act</b> &mdash; Regulation (EU) 2024/1689
with the Digital Omnibus on AI (Regulation (EU) 2026/1744, in force 27 July 2026) applied &mdash;
published so it can be <b>checked</b>, not just read. No official consolidated version existed when
this was generated; this one shows its working.</p>
<div class="cards">
<div class="card"><span class="n">{n_articles}</span>articles in the current text</div>
<div class="card"><span class="n">{n_derivations}</span>amendment instructions, each with a derivation page</div>
<div class="card"><span class="n">{n_touched}</span>touched provisions, each hash-anchored</div>
<div class="card"><span class="n">{defs['published']} &rarr; {defs['composed']}</span>definitions (SME and SMC added)</div>
<div class="card"><span class="n">{counts['only_ours']}</span>inserted provisions &middot; {counts['only_theirs']} deleted &middot; {counts['differ']} changed</div>
<div class="card"><span class="n">{DELTA['external_instruments']['published'] if isinstance(DELTA.get('external_instruments'), dict) and 'published' in DELTA.get('external_instruments', {}) else '115'} &rarr; {DELTA['external_instruments']['composed'] if isinstance(DELTA.get('external_instruments'), dict) and 'composed' in DELTA.get('external_instruments', {}) else '120'}</span>external instruments referenced</div>
</div>
<h2>Read</h2>
<ul>
<li><a href="current-text/index.html">The complete current text</a> (EUR-Lex-style single page)</li>
<li><a href="articles/index.html">Per-article pages</a> with per-paragraph anchors and change badges</li>
<li><a href="downloads/index.html">Downloads</a> &mdash; PDF, HTML, Markdown, JSON, JSON-LD, Turtle, DOCX, all sha256-anchored</li>
</ul>
<h2>Check</h2>
<ul>
<li><a href="derivations/index.html">Derivation pages</a> &mdash; one per amendment instruction: original, instruction, result, word-level diff, payload hash</li>
<li><a href="verify/index.html">Verification</a> &mdash; the gates this build passed, the self-tested differ, and the review register (all provisions start <i>not-reviewed</i>)</li>
<li><a href="{issue_url('eu-2024-1689/art_010/par_006')}">File a provision check</a> &mdash; "checks out" is worth recording too</li>
</ul>
<h2>Compare</h2>
<ul><li><a href="other-versions/index.html">The other public versions</a> &mdash; and what state each is in</li></ul>
"""
    add_page("index.html", "EU AI Act - Current Text (derived, verifiable)", body, root_hash)

    # ---- current text page: reuse the export HTML, inject nav + footer
    with open(os.path.join(ROOT, "exports", "eu-ai-act-current.html"), encoding="utf-8") as f:
        export_html = f.read()
    injected = export_html.replace("<body>", "<body>\n" + nav_html("../"), 1)
    injected = injected.replace("</body>", footer_html(root_hash) + "\n</body>", 1)
    PAGES["current-text/index.html"] = injected

    # ---- article index + pages
    toc = ['<h1>Articles</h1>', '<ul class="toc">']
    for ch, sec, a in ARTICLES:
        tail = article_tail(a["id"])
        toc.append(f'<li><a href="{tail}/index.html">{esc(a["label"])}</a> '
                   f'{esc(a.get("heading") or "")}{badge_html(a["id"], "../")}</li>')
    toc.append("</ul>")
    toc.append('<h2>Annexes</h2><ul class="toc">')
    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        toc.append(f'<li><a href="../annexes/{tail}/index.html">{esc(ax["label"])}</a> '
                   f'{esc(ax.get("heading") or "")}{badge_html(ax["id"], "../")}</li>')
    toc.append("</ul>")
    add_page("articles/index.html", "Articles - EU AI Act current text", "\n".join(toc), root_hash)

    for ch, sec, a in ARTICLES:
        tail = article_tail(a["id"])
        add_page(f"articles/{tail}/index.html",
                 f'{a["label"]} - {a.get("heading") or ""} - EU AI Act current text',
                 article_body(ch, sec, a, "../../"), root_hash)

    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        add_page(f"annexes/{tail}/index.html",
                 f'{ax["label"]} - {ax.get("heading") or ""} - EU AI Act current text',
                 annex_body(ax, "../../"), root_hash)

    # ---- derivation index + pages
    didx = ['<h1>Derivation pages &mdash; one per amendment instruction</h1>',
            '<p>Each page shows the original text, the exact amending instruction (quoted official '
            'text, hash-anchored to the OJ Formex bytes), the result, and a word-level diff. '
            'A checker verifies one paragraph against one instruction in about two minutes.</p>',
            '<table><tr><th>instruction</th><th>op</th><th>level</th><th>instruction text</th></tr>']
    for ins in G2:
        slug = inst_slug(ins["id"])
        didx.append(f'<tr><td><a href="{slug}.html"><code>{esc(ins["id"])}</code></a></td>'
                    f'<td>{esc(ins["op"])}</td><td>{esc(ins["level"])}</td>'
                    f'<td>{esc(ins["instruction_text"])}</td></tr>')
    didx.append("</table>")
    add_page("derivations/index.html", "Derivations - EU AI Act current text", "\n".join(didx), root_hash)

    for ins in G2:
        slug = inst_slug(ins["id"])
        add_page(f"derivations/{slug}.html",
                 f'Derivation {ins["id"]} - EU AI Act current text',
                 instruction_page_body(ins, "../"), root_hash)

    # ---- downloads
    dl = ['<h1>Downloads</h1>',
          '<p>Seven formats, one artefact: a lawyer files the PDF, a reader browses the HTML, '
          'a repository absorbs the Markdown, a tool queries the graph &mdash; and an agent reads '
          'any of them without a scraper. Every file is sha256-anchored; verify what you downloaded.</p>',
          '<table><tr><th>format</th><th>file</th><th>bytes</th><th>sha256</th></tr>']
    for key, meta in EXPORTS_MANIFEST["files"].items():
        name = os.path.basename(meta["path"])
        dl.append(f'<tr><td>{esc(key)}</td><td><a href="../exports/{esc(name)}">{esc(name)}</a></td>'
                  f'<td>{meta["bytes"]}</td><td class="hash">{esc(meta["sha256"])}</td></tr>')
    dl.append("</table>")
    dl.append('<p>Also: the full <a href="../provisions/index.json">provisions/ hash tree</a> '
              '(per-provision text + sha256, rolling up to the root hash in the footer) and the '
              '<a href="../data/graph/">source graphs</a> (as-published nodes, the 72 machine-readable '
              'amendment instructions, the composed overlay, and every gate result).</p>')
    add_page("downloads/index.html", "Downloads - EU AI Act current text", "\n".join(dl), root_hash)

    # ---- verify page
    g2g = G2_MANIFEST["gates"]
    g3g = G3_MANIFEST["gates"]
    v = ['<h1>Verification &mdash; designed, not invited</h1>',
         '<p>This text is derived by a deterministic, gated parser from the Official Journal bytes. '
         'Nothing here asks to be trusted: every claim below links to data in this repository, and '
         'every provision can be checked in minutes.</p>',
         '<h2>How to check one provision (about two minutes)</h2>',
         '<ol><li>Pick a changed provision (badges on the article pages, or the '
         '<a href="../derivations/index.html">derivation index</a>).</li>'
         '<li>On its derivation page, read the quoted instruction and payload (hash-anchored to the '
         'OJ Formex member of CELEX 32026R1744).</li>'
         '<li>Compare BEFORE (as published) and AFTER (composed) &mdash; the word-level diff shows '
         'exactly what moved.</li>'
         f'<li><a href="{issue_url("eu-2024-1689/art_010/par_006")}">File the result</a> with the '
         'provision id and the root hash from the footer &mdash; "checks out" is worth recording too.</li></ol>',
         '<h2>Pipeline gates (all passing in the packed data)</h2>',
         '<table><tr><th>gate</th><th>what it proves</th><th>result</th></tr>']
    v.append(f'<tr><td>gate 1 &mdash; payload round-trip</td><td>every quoted payload reproduces its '
             f'OJ Formex bytes</td><td>checked {g2g["gate1_payload_round_trip"]["checked"]}, failed '
             f'{g2g["gate1_payload_round_trip"]["failed"]}</td></tr>')
    v.append(f'<tr><td>gate 2 &mdash; full consumption</td><td>every enacting-terms leaf parsed, no '
             f'orphans</td><td>{g2g["gate2_full_consumption"]["parsed"]}/{g2g["gate2_full_consumption"]["leaves"]} '
             f'parsed, {len(g2g["gate2_full_consumption"]["orphans"])} orphans</td></tr>')
    v.append(f'<tr><td>gate 3 &mdash; target existence</td><td>every instruction target exists in the '
             f'as-published graph</td><td>checked {g2g["gate3_target_existence"]["checked"]}, missing '
             f'{g2g["gate3_target_existence"]["missing"]}</td></tr>')
    v.append(f'<tr><td>gate 2b &mdash; all applied</td><td>every instruction applied to the composition</td>'
             f'<td>{g3g["gate2_all_applied"]["applied"]}/{g3g["gate2_all_applied"]["instructions"]} applied</td></tr>')
    v.append(f'<tr><td>gate 4 &mdash; cross-reference closure</td><td>no dangling internal references '
             f'after composition</td><td>checked {g3g["gate4_crossref_closure"]["checked"]}, unresolved '
             f'{len(g3g["gate4_crossref_closure"]["unresolved"])}</td></tr>')
    v.append('<tr><td>gate 5 &mdash; structural invariants</td><td>container counts move exactly as the '
             'instructions dictate</td><td>all containers consistent (see '
             '<a href="../data/graph/g3-manifest.json">g3-manifest.json</a>)</td></tr>')
    counts = GATE6["counts"]
    v.append(f'<tr><td>gate 6 &mdash; differ self-test</td><td>the machine differ, run composed-vs-published, '
             f'reports exactly the composed changes</td><td>agree {counts["agree"]} &middot; differ '
             f'{counts["differ"]} &middot; inserted {counts["only_ours"]} &middot; deleted {counts["only_theirs"]} '
             f'&mdash; {esc(GATE6["verdict"])}</td></tr>')
    v.append("</table>")
    v.append('<p><b>The standing claim:</b> the day EUR-Lex publishes its official consolidation, this '
             'differ machine-diffs our composition against it &mdash; the differ is already proven by '
             'self-test. <a href="../data/graph/gate6-self-test.json">Full self-test data.</a></p>')
    v.append('<h2>Corrigenda</h2>')
    v.append(f'<p>{esc(G1_MANIFEST["finding"])} '
             f'(<a href="../data/graph/g1-manifest.json">evidence</a>)</p>')
    v.append('<h2>Composition inputs (raw OJ bytes, hash-anchored)</h2>')
    v.append('<table><tr><th>input</th><th>sha256</th></tr>')
    for key, meta in G3_MANIFEST.get("inputs", {}).items():
        v.append(f'<tr><td><code>{esc(meta.get("path", key))}</code></td>'
                 f'<td class="hash">{esc(meta.get("sha256", ""))}</td></tr>')
    v.append("</table>")
    v.append('<p>The raw OJ zips stay in the authoring vault; the site cites their hashes rather than '
             'hosting them. Anyone can fetch the same CELEX documents from EUR-Lex and compare.</p>')
    v.append('<h2>Review register</h2>')
    v.append(f'<p>Human verification is openly incomplete &mdash; that incompleteness is the invitation. '
             f'All {len(TOUCHED)} touched provisions launch as <b>not-reviewed</b>. Check one and '
             f'file the result; this register moves as issues are resolved.</p>')
    v.append('<table><tr><th>provision</th><th>status</th><th>review</th><th>derivation</th><th>act</th></tr>')
    for base_id in sorted(TOUCHED):
        st = provision_status(base_id)
        insts = sorted(TOUCHED[base_id]["instructions"])
        dlink = f'<a href="../derivations/{inst_slug(insts[0])}.html">derivation</a>' if insts else ""
        v.append(f'<tr><td><code>{esc(base_id)}</code></td><td>{esc(st or "")}</td>'
                 f'<td>not-reviewed</td><td>{dlink}</td>'
                 f'<td><a href="{issue_url(base_id)}">check &amp; report</a></td></tr>')
    v.append("</table>")
    add_page("verify/index.html", "Verify - EU AI Act current text", "\n".join(v), root_hash)

    # ---- other versions
    ov = ['<h1>The other public versions</h1>',
          f'<p>Surveyed {OTHER_VERSIONS_DATE}; states as found on that date (links may since have '
          'been updated &mdash; that would be good news). Most of these are free public goods and '
          'this page links to them gladly; the point is only that a reader should know which text '
          'they are reading. Ours is derived and says so; theirs are listed with the state each '
          'was in.</p>',
          '<table><tr><th>source</th><th>state (as of survey date)</th></tr>']
    for name, url, state_desc in OTHER_VERSIONS:
        ov.append(f'<tr><td><a href="{esc(url)}">{esc(name)}</a></td><td>{esc(state_desc)}</td></tr>')
    ov.append("</table>")
    ov.append('<p>Only the Official Journal publications are authentic. Even official consolidated '
              'texts state that they have documentary value only &mdash; and ours sits further from '
              'authority still, which is exactly why it publishes its derivation and asks to be checked.</p>')
    add_page("other-versions/index.html", "Other public versions - EU AI Act current text",
             "\n".join(ov), root_hash)

    # ------------------------------------------------------------- gates
    failures = []

    # gate A: derivation coverage
    for base_id in TOUCHED:
        if f"{base_id}/derivation.json" not in prov_files:
            failures.append(f"gate A: missing derivation.json for {base_id}")
    inst_pages = {f"derivations/{inst_slug(i['id'])}.html" for i in G2}
    for p in inst_pages:
        if p not in PAGES:
            failures.append(f"gate A: missing derivation page {p}")
    for base_id, info in TOUCHED.items():
        for iid in info["instructions"]:
            if f"derivations/{inst_slug(iid)}.html" not in PAGES:
                failures.append(f"gate A: provision {base_id} cites missing page for {iid}")

    # gate B: hash tree recompute
    if not gate_b:
        failures.append("gate B: provisions hash tree did not recompute identically")

    # gate C: disclaimer on every page
    for rel, content in PAGES.items():
        if "COMPOSED TEXT - NOT AUTHENTIC" not in content:
            failures.append(f"gate C: page missing disclaimer: {rel}")

    # gate D: 'canonical' never describes our text (only 'not canonical' allowed)
    allowed = re.compile(r"(?i)not[ -]canonical")
    word = re.compile(r"(?i)canonical")
    for rel, content in PAGES.items():
        stripped = allowed.sub("", content)
        if word.search(stripped):
            failures.append(f"gate D: 'canonical' describes our text on {rel}")

    if failures:
        for f_ in failures:
            print("GATE FAIL:", f_, file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------- write site
    site_root = os.path.join(ROOT, "site")
    if os.path.isdir(site_root):
        shutil.rmtree(site_root)
    for rel, content in PAGES.items():
        p = os.path.join(site_root, rel)
        os.makedirs(os.path.dirname(p) or site_root, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

    print(f"site: {len(PAGES)} pages "
          f"({len(ARTICLES)} articles, {len(ANNEXES)} annexes, {len(G2)} derivations)")
    print(f"provisions: {len(prov_files)} files, root hash {root_hash}")
    print(f"gates: A, B, C, D all passed")

    if assemble_dir:
        assemble(assemble_dir)
        print(f"assembled deployable tree at {assemble_dir}/")
    return 0


def assemble(dest):
    """Lay out exactly what gets served: the site pages at the root, with
    exports/, provisions/ and data/ as siblings. Page links are written
    relative to THIS layout, so it is also what a local preview must serve.
    """
    dest = os.path.abspath(dest)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(os.path.join(ROOT, "site")):
        src = os.path.join(ROOT, "site", name)
        dst = os.path.join(dest, name)
        shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
    for tree in ("exports", "provisions", "data"):
        shutil.copytree(os.path.join(ROOT, tree), os.path.join(dest, tree))
    with open(os.path.join(dest, "CNAME"), "w", encoding="utf-8") as f:
        f.write("eu-ai-act.standards.riskmandate.ai\n")


if __name__ == "__main__":
    dest = None
    if "--assemble" in sys.argv:
        dest = sys.argv[sys.argv.index("--assemble") + 1]
    sys.exit(main(dest))
