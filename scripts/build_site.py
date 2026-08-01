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
  gate E  every page carries the banner, header, footer and root hash
  gate F  changelog.json does not misstate this build's root hash / text version
  gate G  every page has .md and .llm.json twins, advertised and disclaimed

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
# Repo-authored, not vault-generated: the human-maintained release notes.
CHANGELOG = read_json("changelog.json")["releases"]

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

CSS_TOKENS = """


/* Design tokens lifted verbatim from riskmandate.ai (v0.9.0 host shell), so
   this site reads as part of the same family. Do not drift them locally --
   if the brand moves, re-copy the :root block. */
:root{
 --bg:#F7F6F2; --bg2:#EFEDE7; --card:#FFFFFF; --ink:#0D0D0C; --canvas:#0A0A09;
 --text:#1A1917; --muted:#4A4845; --faint:#8A8780; --border:#E2DFD8;
 --green:#1A7F5A; --green-2:#22c55e; --greenBg:#EBF5F0;
 --gold:#B45309; --goldBg:#FFFBEB; --red:#C0392B; --redBg:#FDF2F1;
 --blue:#1D4ED8; --blueBg:#EFF6FF;
 --fg:#F7F6F2; --fg-2:rgba(247,246,242,.55); --fg-3:rgba(247,246,242,.32);
 --line:rgba(247,246,242,.10); --line-2:rgba(247,246,242,.18);
 --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 --mono:ui-monospace,"SF Mono","JetBrains Mono","Roboto Mono",Menlo,Consolas,monospace;
 --wrap:1100px; --r:10px; --r-sm:8px;
}
*{box-sizing:border-box}
"""

# Chrome only (banner, header, footer). Injected on its own into the
# EUR-Lex-style export page, which keeps its own Official Journal
# typography - so this block must not restyle body text or links.
CSS_CHROME = """

/* ---- Risk Mandate banner: the parent-brand strip, on brand canvas ------- */
.rm-banner,.top a,.foot a{text-decoration:none}
.rm-banner:hover,.top a:hover,.foot a:hover{text-decoration:none}
.rm-banner{display:flex;align-items:center;gap:10px;background:var(--canvas);
 color:var(--fg-2);border-bottom:1px solid var(--line);padding:9px 24px;
 font-family:var(--mono);font-size:11.5px;letter-spacing:.02em;text-decoration:none}
.rm-banner:hover{text-decoration:none}
.rm-banner .inner{display:flex;align-items:center;gap:10px;width:100%;
 max-width:var(--wrap);margin:0 auto}
.rm-banner .rm{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;
 background:rgba(26,127,90,.18);border:1px solid rgba(26,127,90,.4);
 color:var(--green-2);font-weight:700;font-size:9px;flex:none}
.rm-banner b{color:var(--fg);font-weight:700}
.rm-banner .sep{color:var(--fg-3)}
.rm-banner .cta{margin-left:auto;color:var(--green-2);white-space:nowrap}
.rm-banner:hover .cta{text-decoration:underline}
@media (max-width:720px){.rm-banner .tagline{display:none}}

/* ---- site header ------------------------------------------------------- */
/* The chrome must never inherit the host page's typography: the current-text
   page is an Official Journal facsimile whose body is Times serif, and the
   header/footer would silently pick that up. Set the family explicitly. */
.top,.foot{font-family:var(--sans)}
.top .hash,.foot .hash,.top code,.foot code{font-family:var(--mono)}
.top{position:sticky;top:0;z-index:60;background:rgba(255,255,255,.92);
 backdrop-filter:blur(14px);border-bottom:1px solid var(--border)}
.top .wrap{display:flex;align-items:center;gap:22px;flex-wrap:wrap;
 min-height:56px;max-width:var(--wrap);margin:0 auto;padding:6px 24px}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:15px;
 color:var(--text);letter-spacing:-.01em}
.brand:hover{text-decoration:none}
.brand .mark{width:26px;height:26px;border-radius:7px;background:var(--ink);
 display:grid;place-items:center;color:var(--green-2);font-family:var(--mono);
 font-weight:700;font-size:9px;flex:none}
.navlinks{display:flex;flex-wrap:wrap;gap:22px;margin-left:auto}
.navlinks a{color:var(--muted);font-size:13.5px;padding:6px 0;transition:color .15s}
.navlinks a:hover{color:var(--text);text-decoration:none}
.navlinks a.ext{color:var(--faint)}
/* version chip - mirrors the version switcher in the riskmandate.ai header */
.ver{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);
 font-size:11.5px;font-weight:700;letter-spacing:.04em;color:var(--muted);
 background:var(--bg2);border:1px solid var(--border);border-radius:999px;
 padding:4px 11px;white-space:nowrap;transition:border-color .15s,color .15s}
.ver:hover{color:var(--text);border-color:var(--faint);text-decoration:none}
.ver .dot{width:6px;height:6px;border-radius:50%;background:var(--green-2);flex:none}

/* ---- footer ------------------------------------------------------------ */
.foot{background:var(--canvas);border-top:1px solid var(--line);padding:34px 24px;
 color:var(--fg-3);font-size:12.5px;line-height:1.7}
.foot .wrap{max-width:var(--wrap);margin:0 auto}
.foot a{color:var(--fg-2)}
.foot a:hover{color:var(--fg)}
.foot .hash{color:var(--fg-3);font-size:11.5px}
.foot .claim{color:var(--fg-2);font-weight:600}
"""

CSS_CONTENT = """
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
 font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--green);text-decoration:none}
a:hover{text-decoration:underline}

main{max-width:var(--wrap);margin:0 auto;padding:32px 24px 72px}

/* ---- headings ---------------------------------------------------------- */
h1,h2,h3{margin:0;font-weight:700;letter-spacing:-.02em;line-height:1.15;color:var(--text)}
h1{font-size:clamp(28px,3.4vw,42px);font-weight:800;letter-spacing:-.03em;
 line-height:1.05;margin:10px 0 6px}
h1.id{font-family:var(--mono);font-size:clamp(19px,2.1vw,26px);font-weight:700;
 letter-spacing:-.01em;word-break:break-all}
h2{font-size:21px;margin:34px 0 10px}
h3{font-size:16px;margin:24px 0 8px}
.tag{display:inline-block;font-family:var(--mono);font-size:10px;font-weight:700;
 letter-spacing:.2em;text-transform:uppercase;color:var(--green);margin-bottom:12px}
.crumbs{font-family:var(--mono);font-size:11px;letter-spacing:.08em;
 text-transform:uppercase;color:var(--faint);margin:0 0 4px}

/* ---- disclaimer -------------------------------------------------------- */
.disclaimer{border:1px solid rgba(180,83,9,.28);background:var(--goldBg);
 color:#6b4413;border-radius:var(--r);padding:12px 16px;font-size:13px;
 line-height:1.55;margin:0 0 26px}
.disclaimer b{color:var(--gold)}

/* ---- tables ------------------------------------------------------------ */
table{border-collapse:separate;border-spacing:0;width:100%;font-size:14px;
 background:var(--card);border:1px solid var(--border);border-radius:var(--r);
 overflow:hidden;margin:8px 0}
th,td{border-bottom:1px solid var(--border);padding:9px 12px;text-align:left;
 vertical-align:top}
tr:last-child td{border-bottom:0}
th{background:var(--bg2);font-family:var(--mono);font-size:10.5px;font-weight:700;
 letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.tablewrap{overflow-x:auto}
code,.hash{font-family:var(--mono);font-size:12px;word-break:break-all;color:var(--muted)}

/* ---- change badges ----------------------------------------------------- */
.badge{display:inline-block;font-family:var(--mono);font-size:9.5px;font-weight:700;
 letter-spacing:.14em;text-transform:uppercase;padding:2px 8px;border-radius:999px;
 vertical-align:2px;text-decoration:none;border:1px solid transparent}
.badge:hover{text-decoration:none;filter:brightness(.97)}
.badge.amended{color:var(--gold);background:var(--goldBg);border-color:rgba(180,83,9,.32)}
.badge.inserted{color:var(--green);background:var(--greenBg);border-color:rgba(26,127,90,.32)}
.badge.deleted{color:var(--red);background:var(--redBg);border-color:rgba(192,57,43,.3)}

/* ---- provisions -------------------------------------------------------- */
.prov{margin:11px 0}
.prov .lbl{font-weight:700;margin-right:7px;color:var(--text)}
.prov.d2{margin-left:26px}.prov.d3{margin-left:52px}.prov.d4{margin-left:78px}
.prov .anchor{color:var(--faint);text-decoration:none;font-size:13px;opacity:0;
 transition:opacity .15s}
.prov:hover .anchor{opacity:1}
.gap{border:1px solid rgba(180,83,9,.28);background:var(--goldBg);color:#6b4413;
 border-radius:var(--r);padding:11px 15px;font-size:13.5px;margin:14px 0}
.gap b{color:var(--gold)}

/* ---- panes and quotes -------------------------------------------------- */
blockquote.instr{border-left:3px solid var(--green);margin:10px 0;padding:10px 16px;
 background:var(--greenBg);border-radius:0 var(--r-sm) var(--r-sm) 0;font-size:15px}
.pane{border:1px solid var(--border);background:var(--card);border-radius:var(--r);
 padding:12px 16px;margin:10px 0}
.pane h4{margin:0 0 7px;font-family:var(--mono);font-size:10px;font-weight:700;
 letter-spacing:.16em;text-transform:uppercase;color:var(--faint)}
.pane p{margin:0}
.diff del{background:var(--redBg);color:var(--red);text-decoration:line-through;
 border-radius:3px;padding:0 2px}
.diff ins{background:var(--greenBg);color:var(--green);text-decoration:none;
 border-radius:3px;padding:0 2px}

/* ---- landing ----------------------------------------------------------- */
.hero{font-size:18px;line-height:1.55;color:var(--muted);max-width:760px;margin:0 0 26px}
.hero b{color:var(--text)}
.eyebrow{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);
 font-size:11px;letter-spacing:.04em;font-weight:700;color:var(--green);
 background:rgba(26,127,90,.10);border:1px solid rgba(26,127,90,.34);
 border-radius:999px;padding:5px 12px}
.eyebrow .d{width:6px;height:6px;border-radius:50%;background:var(--green-2)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;
 margin:22px 0 8px}
.card{border:1px solid var(--border);background:var(--card);border-radius:var(--r);
 padding:16px 18px;font-size:13.5px;color:var(--muted);line-height:1.45}
.card .n{font-family:var(--mono);font-size:27px;font-weight:700;letter-spacing:-.02em;
 color:var(--text);display:block;margin-bottom:5px}
.linklist{list-style:none;padding:0;margin:8px 0}
.linklist li{margin:9px 0;color:var(--muted);font-size:15px}
.linklist a{font-weight:600}
.toc{columns:2;column-gap:32px;font-size:14.5px;list-style:none;padding:0;margin:10px 0}
.toc li{margin:5px 0;break-inside:avoid;color:var(--muted)}
.toc a{font-weight:600}
@media (max-width:640px){.toc{columns:1}}
.actions{font-size:13.5px;color:var(--muted);margin-top:22px;
 border-top:1px solid var(--border);padding-top:14px}
"""

CSS = CSS_TOKENS + CSS_CHROME + CSS_CONTENT


BRAND_URL = "https://riskmandate.ai"


def banner_html():
    """The parent-brand strip: Risk Mandate's own canvas colour and mono type,
    linking out to riskmandate.ai in a new tab."""
    return (
        f'<a class="rm-banner" href="{BRAND_URL}" target="_blank" '
        f'rel="noopener noreferrer">'
        f'<span class="inner">'
        f'<span class="rm">RM</span>'
        f'<span><b>Risk Mandate</b><span class="tagline">'
        f'<span class="sep"> &middot; </span>the business risk layer for '
        f'autonomous systems</span></span>'
        f'<span class="cta">riskmandate.ai &#8599;</span>'
        f'</span></a>'
    )


def nav_html(prefix):
    return (
        banner_html() +
        f'<header class="top"><div class="wrap">'
        f'<a class="brand" href="{prefix}index.html">'
        f'<span class="mark">EU</span> EU AI Act &mdash; Current Text</a>'
        f'<a class="ver" href="{prefix}version/index.html" '
        f'title="What this version is, and what changed between versions">'
        f'<span class="dot"></span>{VERSION}</a>'
        f'<nav class="navlinks">'
        f'<a href="{prefix}current-text/index.html">Full text</a>'
        f'<a href="{prefix}articles/index.html">Articles</a>'
        f'<a href="{prefix}derivations/index.html">Derivations</a>'
        f'<a href="{prefix}downloads/index.html">Downloads</a>'
        f'<a href="{prefix}verify/index.html">Verify</a>'
        f'<a href="{prefix}other-versions/index.html">Other versions</a>'
        f'<a class="ext" href="{REPO_URL}" target="_blank" rel="noopener noreferrer">'
        f'GitHub &#8599;</a>'
        f'</nav></div></header>'
    )


def footer_html(root_hash, prefix=""):
    return (
        '<footer class="foot"><div class="wrap">'
        '<span class="claim">Derived, not canonical</span> &middot; not legal advice '
        f'&middot; text version {TEXT_VERSION} &middot; generated {GENERATED_ON} '
        f'&middot; site <a href="{prefix}version/index.html">{VERSION}</a><br>'
        f'provisions root hash <span class="hash">{root_hash}</span><br>'
        f'<a href="{REPO_URL}" target="_blank" rel="noopener noreferrer">source '
        f'&amp; data on GitHub</a> &middot; by '
        f'<a href="{BRAND_URL}" target="_blank" rel="noopener noreferrer">'
        f'RiskMandate.ai</a>'
        '</div></footer>'
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


# ------------------------------------------------- machine-readable twins
#
# Agents should not have to scrape HTML to read a legal text. Every page is
# published three ways on the same slug - the page itself, `<slug>.md`, and
# `<slug>.llm.json` - with `/llms.txt` indexing the lot. The pattern is the
# one sgraph.ai uses; the difference is that this site is static, so the
# twins are emitted at build time from the same data the HTML is built from
# (never scraped back out of the HTML, which would be lossy and would let the
# two drift). GitHub Pages serves .md as text/markdown and .json as
# application/json, so no server-side negotiation is needed.

MD_PAGES = {}      # relpath -> markdown text
JSON_PAGES = {}    # relpath -> bytes
PROV_HASH = {}     # provision id -> sha256 of its text.md (filled during build)


def twin_base(relpath):
    """site/articles/art_010/index.html -> articles/art_010 (the slug)."""
    if relpath == "index.html":
        return "index"
    if relpath.endswith("/index.html"):
        return relpath[: -len("/index.html")]
    return relpath[: -len(".html")]


def page_url(relpath):
    if relpath == "index.html":
        return f"{SITE_URL}/"
    if relpath.endswith("/index.html"):
        return f"{SITE_URL}/{relpath[: -len('/index.html')]}/"
    return f"{SITE_URL}/{relpath}"


def md_url(relpath):
    return f"{SITE_URL}/{twin_base(relpath)}.md"


def json_url(relpath):
    return f"{SITE_URL}/{twin_base(relpath)}.llm.json"


def alternates(relpath):
    """<link rel=alternate> so the twins are discoverable from the page."""
    return (f'\n<link rel="alternate" type="text/markdown" href="{md_url(relpath)}">'
            f'\n<link rel="alternate" type="application/json" '
            f'href="{json_url(relpath)}">')


def md_front(title, relpath, extra_lines=()):
    """Every twin repeats the provenance: an agent may hold only this file."""
    lines = [f"# {title}", ""]
    lines.append(f"> {DISCLAIMER}")
    lines.append("")
    lines.append(f"- Page: {page_url(relpath)}")
    lines.append(f"- Structured: {json_url(relpath)}")
    lines.append(f"- Text version: {TEXT_VERSION} (generated {GENERATED_ON})")
    lines.append(f"- Site version: {VERSION}")
    lines.append(f"- Provisions root hash: `{BUILD_STATE['root_hash']}`")
    lines.extend(extra_lines)
    lines.append("")
    return lines


BUILD_STATE = {"root_hash": ""}


def prov_meta_row(pid):
    st = provision_status(pid) or "unchanged"
    sha = PROV_HASH.get(pid, "")
    insts = sorted(TOUCHED.get(pid, {}).get("instructions", []))
    der = ", ".join(f"{SITE_URL}/derivations/{inst_slug(i)}.md" for i in insts)
    return f"| `{pid}` | {st} | `{sha}` | {der} |"


def article_md(ch, sec, a, relpath):
    crumbs = a["label"] + " in " + ch["label"] + (f" / {sec['label']}" if sec else "")
    counts = {}
    for other in sorted(TOUCHED):
        if other == a["id"] or other.startswith(a["id"] + "/"):
            st = provision_status(other)
            counts[st] = counts.get(st, 0) + 1
    change = ", ".join(f"{n} {st}" for st, n in sorted(counts.items())) or "no changes"

    extra = [f"- Provision id: `{a['id']}`",
             f"- Location: {crumbs}",
             f"- Changes applied by the Digital Omnibus: {change}"]
    out = md_front(f"{a['label']} — {a.get('heading') or ''}".strip(" —"), relpath, extra)

    out.append("## Text")
    out.append("")
    for nid, label, text, depth in walk_text_nodes(a):
        indent = "    " * max(depth - 1, 0)
        st = provision_status(nid)
        mark = f" _[{st}]_" if st else ""
        lbl = f"**{label}** " if label else ""
        out.append(f"{indent}{lbl}{text}{mark}")
        out.append("")

    deleted = [b for b in sorted(TOUCHED)
               if b.startswith(a["id"] + "/") and provision_status(b) == "deleted"]
    if deleted:
        out.append("## Numbering gaps (deliberate)")
        out.append("")
        for b in deleted:
            insts = sorted(TOUCHED[b]["instructions"])
            der = f"{SITE_URL}/derivations/{inst_slug(insts[0])}.md" if insts else ""
            out.append(f"- `{b}` was deleted by the Digital Omnibus. Deleted provisions "
                       f"are never renumbered, so the gap is permanent. Derivation: {der}")
        out.append("")

    touched_here = [b for b in sorted(TOUCHED)
                    if b == a["id"] or b.startswith(a["id"] + "/")]
    out.append("## Provisions")
    out.append("")
    out.append("| provision id | status | sha256 of text | derivation |")
    out.append("|---|---|---|---|")
    for nid, _, _, _ in walk_text_nodes(a):
        out.append(prov_meta_row(nid))
    for b in touched_here:
        if provision_status(b) == "deleted":
            out.append(prov_meta_row(b))
    out.append("")
    out.append(f"Report a check against any provision here: {REPO_URL}/issues/new"
               f"?template=provision-check.yml")
    out.append("")
    return "\n".join(out)


def article_json(ch, sec, a, relpath):
    provisions = []
    for nid, label, text, depth in walk_text_nodes(a):
        insts = sorted(TOUCHED.get(nid, {}).get("instructions", []))
        provisions.append({
            "id": nid, "label": label, "depth": depth, "text": text,
            "status": provision_status(nid) or "unchanged",
            "sha256": PROV_HASH.get(nid, ""),
            "instructions": insts,
            "derivation_urls": [f"{SITE_URL}/derivations/{inst_slug(i)}.md" for i in insts],
        })
    deleted = []
    for b in sorted(TOUCHED):
        if b.startswith(a["id"] + "/") and provision_status(b) == "deleted":
            insts = sorted(TOUCHED[b]["instructions"])
            deleted.append({
                "id": b, "status": "deleted",
                "note": "deleted by the Digital Omnibus; the numbering gap is permanent "
                        "and the id stays resolvable",
                "instructions": insts,
                "derivation_urls": [f"{SITE_URL}/derivations/{inst_slug(i)}.md" for i in insts],
            })
    return {
        "schema": "eu-ai-act-current/v1",
        "kind": "article",
        "slug": twin_base(relpath),
        "url": page_url(relpath),
        "markdown_url": md_url(relpath),
        "title": f"{a['label']} — {a.get('heading') or ''}".strip(" —"),
        "provision_id": a["id"],
        "chapter": {"id": ch["id"], "label": ch["label"], "heading": ch.get("heading")},
        "section": ({"id": sec["id"], "label": sec["label"], "heading": sec.get("heading")}
                    if sec else None),
        "authentic": False,
        "not_legal_advice": True,
        "disclaimer": DISCLAIMER,
        "text_version": TEXT_VERSION,
        "generated_on": GENERATED_ON,
        "site_version": VERSION,
        "provisions_root_hash": BUILD_STATE["root_hash"],
        "provisions": provisions,
        "deleted_provisions": deleted,
    }


def annex_md(ax, relpath):
    out = md_front(f"{ax['label']} — {ax.get('heading') or ''}".strip(" —"), relpath,
                   [f"- Provision id: `{ax['id']}`"])
    out.append("## Text")
    out.append("")
    for item in ax.get("items", []):
        st = provision_status(item["id"])
        mark = f" _[{st}]_" if st else ""
        out.append(f"**{item.get('label') or ''}** {item.get('text') or ''}{mark}")
        out.append("")
    out.append("## Provisions")
    out.append("")
    out.append("| provision id | status | sha256 of text | derivation |")
    out.append("|---|---|---|---|")
    for item in ax.get("items", []):
        out.append(prov_meta_row(item["id"]))
    out.append("")
    return "\n".join(out)


def annex_json(ax, relpath):
    items = []
    for item in ax.get("items", []):
        insts = sorted(TOUCHED.get(item["id"], {}).get("instructions", []))
        items.append({
            "id": item["id"], "label": item.get("label"), "text": item.get("text"),
            "status": provision_status(item["id"]) or "unchanged",
            "sha256": PROV_HASH.get(item["id"], ""),
            "instructions": insts,
        })
    return {
        "schema": "eu-ai-act-current/v1", "kind": "annex",
        "slug": twin_base(relpath), "url": page_url(relpath),
        "markdown_url": md_url(relpath),
        "title": f"{ax['label']} — {ax.get('heading') or ''}".strip(" —"),
        "provision_id": ax["id"], "authentic": False, "not_legal_advice": True,
        "disclaimer": DISCLAIMER, "text_version": TEXT_VERSION,
        "site_version": VERSION, "provisions_root_hash": BUILD_STATE["root_hash"],
        "items": items,
    }


def derivation_md(ins, relpath):
    payload = ins.get("payload") or {}
    extra = [f"- Instruction id: `{ins['id']}`",
             f"- Operation: {ins['op']} · level: {ins['level']}",
             f"- In force: {ins.get('applies', {}).get('in_force', '')}",
             f"- Payload sha256: `{payload.get('xml_sha256') or 'none (a deletion quotes no text)'}`",
             f"- OJ Formex member: `{ins['source_member']}` (CELEX 32026R1744)"]
    out = md_front(f"Derivation — {ins['id']}", relpath, extra)

    if ins.get("context_text"):
        out.append("## Context (enacting terms)")
        out.append("")
        for c in ins["context_text"]:
            out.append(f"> {c}")
            out.append("")
    out.append("## Instruction (quoted official text)")
    out.append("")
    out.append(f"> {ins['instruction_text']}")
    out.append("")
    if payload.get("text"):
        out.append("## Quoted payload (official replacement/inserted text)")
        out.append("")
        out.append(payload["text"])
        out.append("")

    out.append("## Affected provisions")
    out.append("")
    for tid in [node_ref(t) for t in (ins.get("targets") or [])]:
        base = tid.split("@")[0]
        st = provision_status(base) or "amended"
        out.append(f"### `{base}` — {st}")
        out.append("")
        before = g0_labelled_text(base) or g0_subtree_text(base)
        out.append("**Before (as published):**")
        out.append("")
        out.append(before or "_(none)_")
        out.append("")
        if st == "deleted":
            out.append("**After:** deleted. The numbering gap is permanent and the id "
                       "stays resolvable.")
        else:
            out.append("**After (composed current text):**")
            out.append("")
            out.append(subtree_text(base) or "_(none)_")
        out.append("")
        out.append(f"- sha256 of composed text: `{PROV_HASH.get(base, '')}`")
        out.append(f"- derivation.json: {SITE_URL}/provisions/{base}/derivation.json")
        out.append("")
    for nid in [node_ref(t) for t in (ins.get("inserted") or [])]:
        out.append(f"### `{nid}` — inserted")
        out.append("")
        out.append("**Before:** _(no previous text — inserted provision)_")
        out.append("")
        out.append("**After (composed current text):**")
        out.append("")
        out.append(subtree_text(nid) or "_(none)_")
        out.append("")
        out.append(f"- sha256 of composed text: `{PROV_HASH.get(nid, '')}`")
        out.append("")
    out.append("## How to check this")
    out.append("")
    out.append("Compare the quoted instruction and payload above (hash-anchored to the "
               "OJ Formex bytes of CELEX 32026R1744) against the before/after text. "
               "That is the whole verification for this provision — about two minutes. "
               f"Report the result, including the root hash above: {REPO_URL}/issues/new"
               f"?template=provision-check.yml")
    out.append("")
    return "\n".join(out)


def derivation_json(ins, relpath):
    payload = ins.get("payload") or {}
    affected = []
    for tid in [node_ref(t) for t in (ins.get("targets") or [])]:
        base = tid.split("@")[0]
        st = provision_status(base) or "amended"
        affected.append({
            "id": base, "status": st,
            "before_text": g0_labelled_text(base) or g0_subtree_text(base) or None,
            "after_text": None if st == "deleted" else (subtree_text(base) or None),
            "sha256": PROV_HASH.get(base, ""),
            "derivation_json_url": f"{SITE_URL}/provisions/{base}/derivation.json",
        })
    for nid in [node_ref(t) for t in (ins.get("inserted") or [])]:
        affected.append({
            "id": nid, "status": "inserted", "before_text": None,
            "after_text": subtree_text(nid) or None,
            "sha256": PROV_HASH.get(nid, ""),
            "derivation_json_url": f"{SITE_URL}/provisions/{nid}/derivation.json",
        })
    return {
        "schema": "eu-ai-act-current/v1", "kind": "derivation",
        "slug": twin_base(relpath), "url": page_url(relpath),
        "markdown_url": md_url(relpath),
        "title": f"Derivation — {ins['id']}",
        "instruction": {
            "id": ins["id"], "op": ins["op"], "level": ins["level"],
            "path": ins.get("path", []),
            "instruction_text": ins["instruction_text"],
            "context_text": ins.get("context_text", []),
            "in_force": ins.get("applies", {}).get("in_force"),
            "payload_text": payload.get("text") or None,
            "payload_xml_sha256": payload.get("xml_sha256"),
            "source_member": ins["source_member"],
            "celex": "32026R1744",
            "target_resolution": ins.get("target_resolution", {}),
        },
        "affected_provisions": affected,
        "authentic": False, "not_legal_advice": True, "disclaimer": DISCLAIMER,
        "text_version": TEXT_VERSION, "site_version": VERSION,
        "provisions_root_hash": BUILD_STATE["root_hash"],
    }


def add_twin(relpath, markdown, obj):
    MD_PAGES[twin_base(relpath) + ".md"] = markdown
    JSON_PAGES[twin_base(relpath) + ".llm.json"] = dump_json(obj)


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


BADGE_ORDER = ("amended", "inserted", "deleted")


def rollup_badges(container_id):
    """Status label(s) for a container (an article or an annex).

    Only 9 of the 119 articles were replaced or inserted wholesale and so have
    a node of their own; the other changed ones - Article 10 among them - were
    amended at paragraph or point level. Badging the container by its own node
    alone therefore leaves 33 changed articles looking untouched. Roll the
    descendants up instead, and count them, so the index tells the truth about
    what is inside.

    These are labels rather than links: the container's own title is the link,
    and with several changed provisions inside there is no single derivation
    page to point at.
    """
    own = provision_status(container_id)
    if own:
        # The container itself changed (replaced or inserted wholesale); its
        # descendants are part of that one change, so do not also count them.
        return f' <span class="badge {own}" title="{esc(container_id)} - {own}">{own}</span>'
    counts = {}
    for other in sorted(TOUCHED):
        if other.startswith(container_id + "/"):
            st = provision_status(other)
            if st:
                counts[st] = counts.get(st, 0) + 1
    out = []
    for st in BADGE_ORDER:
        n = counts.get(st)
        if not n:
            continue
        label = f"{st} {n}" if n > 1 else st
        out.append(f' <span class="badge {st}" title="{n} provision(s) {st} '
                   f'in {esc(container_id)}">{label}</span>')
    return "".join(out)


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
    PAGES[relpath] = page(title, body, prefix, root_hash,
                          extra_head + alternates(relpath))


def render_provision_line(nid, label, text, depth, prefix):
    cls = f"prov d{min(depth + 1, 4)}" if depth else "prov"
    return (f'<p class="{cls}" id="{esc(nid)}"><span class="lbl">{esc(label)}</span>'
            f'{esc(text)}{badge_html(nid, prefix)}'
            f' <a class="anchor" href="#{esc(nid)}" title="Link to this provision">&sect;</a></p>')


def article_body(ch, sec, a, prefix):
    out = []
    crumbs = esc(ch["label"]) + (" &middot; " + esc(sec["label"]) if sec else "")
    out.append(f'<p class="crumbs">{crumbs} &mdash; '
               f'{esc(ch.get("heading") or "")}{(" / " + esc(sec.get("heading") or "")) if sec else ""}</p>')
    out.append(f'<h1 id="{esc(a["id"])}">{esc(a["label"])} &mdash; {esc(a.get("heading") or "")}'
               f'{rollup_badges(a["id"])}</h1>')
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
    out.append(f'<p class="actions">'
               f'<a href="{issue_url(a["id"])}">Report a problem in this article</a> &middot; '
               f'provision hashes: <a href="{prefix}provisions/{esc(a["id"])}/index.json">'
               f'provisions/{esc(a["id"])}/</a></p>')
    return "\n".join(out)


def annex_body(ax, prefix):
    out = [f'<h1 id="{esc(ax["id"])}">{esc(ax["label"])} &mdash; {esc(ax.get("heading") or "")}'
           f'{rollup_badges(ax["id"])}</h1>']
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
    out.append(f'<p class="actions">'
               f'<a href="{issue_url(ax["id"])}">Report a problem in this annex</a></p>')
    return "\n".join(out)


def instruction_page_body(ins, prefix):
    iid = ins["id"]
    out = []
    out.append('<span class="tag">Derivation</span>')
    out.append(f'<h1 class="id">{esc(iid)}</h1>')
    out.append('<p class="actions">'
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
    out.append('<div class="tablewrap"><table><tr><th>link</th><th>value</th></tr>'
               f'<tr><td>payload xml sha256</td><td>{payload_cell}</td></tr>'
               f'<tr><td>OJ Formex member</td><td><code>{esc(ins["source_member"])}</code></td></tr>'
               f'<tr><td>CELLAR / CELEX</td><td><a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32026R1744">32026R1744</a></td></tr>'
               f'<tr><td>target resolution</td><td>{esc(ins.get("target_resolution", {}).get("method", ""))} '
               f'({esc(ins.get("target_resolution", {}).get("partition", ""))})</td></tr></table></div>')

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
        out.append(f'<p class="actions">'
                   f'<a href="{issue_url(base)}">Check this and report</a> &middot; '
                   f'<a href="{prefix}provisions/{esc(base)}/derivation.json">derivation.json</a></p>')

    for nid in inserted:
        after = subtree_text(nid)
        out.append(f'<h3 id="{esc(nid)}"><code>{esc(nid)}</code>'
                   f'{badge_html(nid, prefix)}</h3>')
        out.append('<div class="pane"><h4>BEFORE</h4><p><i>(no previous text &mdash; inserted provision)</i></p></div>')
        out.append(f'<div class="pane"><h4>AFTER (composed current text)</h4><p>{esc(after)}</p></div>')
        out.append(f'<p class="actions">'
                   f'<a href="{issue_url(nid)}">Check this and report</a> &middot; '
                   f'<a href="{prefix}provisions/{esc(nid)}/derivation.json">derivation.json</a></p>')

    out.append(f'<p class="actions">'
               f'How to check: compare the quoted instruction and payload above (hash-anchored to the '
               f'OJ Formex bytes) against the BEFORE/AFTER panes. Two minutes, one provision. '
               f'Then <a href="{issue_url(iid)}">file the result</a> &mdash; "checks out" is worth '
               f'recording too.</p>')
    return "\n".join(out)


# ------------------------------------------------------------ version page

def version_body(root_hash):
    """What this version is, and what changed between versions.

    The distinction that matters here is between the SITE version and the
    composed TEXT. The site can be released many times against one text
    version, so each release records the provisions root hash: if it is
    unchanged, the text did not change, and a provision someone verified
    against that hash is still verified. That is stated on the page rather
    than left for the reader to infer from two hex strings.
    """
    entry = next((r for r in CHANGELOG if r["version"] == VERSION), None)

    out = ['<span class="tag">Version</span>', f'<h1>{esc(VERSION)}</h1>']

    if entry is None:
        # CI bumps the tag on every push to dev, so the running version can
        # legitimately be ahead of the hand-written notes. Say so plainly
        # rather than rendering a page that looks like there is nothing to say.
        out.append('<div class="gap"><b>No release notes recorded for this '
                   'version yet.</b> The facts below are read from this build, '
                   'so they are accurate regardless; the notes are added by '
                   f'hand in <code>changelog.json</code>.</div>')

    out.append('<h2>This build</h2>')
    out.append('<div class="tablewrap"><table><tr><th>what</th><th>value</th></tr>'
               f'<tr><td>site version</td><td><code>{esc(VERSION)}</code></td></tr>'
               f'<tr><td>composed text version</td><td><code>{esc(TEXT_VERSION)}</code> '
               f'&mdash; the date the legal text speaks as of</td></tr>'
               f'<tr><td>text generated</td><td>{esc(GENERATED_ON)}</td></tr>'
               f'<tr><td>provisions root hash</td>'
               f'<td><span class="hash">{esc(root_hash)}</span></td></tr>'
               f'<tr><td>pages</td><td>{len(ARTICLES)} articles &middot; {len(ANNEXES)} annexes '
               f'&middot; {len(G2)} derivations</td></tr>'
               f'<tr><td>touched provisions</td><td>{len(TOUCHED)}, all '
               f'<a href="../verify/index.html">not-reviewed</a> at launch</td></tr>'
               '</table></div>')
    out.append('<p class="actions">The root hash is the root of the published '
               '<a href="../provisions/index.json">provisions hash tree</a>. Quote it when '
               'you report a check, so everyone knows exactly which text you read.</p>')

    out.append('<h2>Releases</h2>')
    if not CHANGELOG:
        out.append('<p>No releases recorded yet.</p>')
        return "\n".join(out)

    out.append('<p>Newest first. <b>Text unchanged</b> means the composed legal text is '
               'byte-for-byte what the previous release published &mdash; only the site '
               'around it moved, so any provision already checked stays checked.</p>')

    for i, rel in enumerate(CHANGELOG):
        prev = CHANGELOG[i + 1] if i + 1 < len(CHANGELOG) else None
        is_current = rel["version"] == VERSION
        rh = rel.get("provisions_root_hash", "")

        if prev is None:
            text_note = ('<span class="badge inserted" title="first published release">'
                         'first release</span>')
        elif rh and rh == prev.get("provisions_root_hash"):
            text_note = ('<span class="badge inserted" title="composed text identical to '
                         'the previous release">text unchanged</span>')
        else:
            text_note = ('<span class="badge amended" title="the composed text changed in '
                         'this release">text changed</span>')

        current_note = (' <span class="badge inserted" title="the version this site is '
                        'serving">current</span>' if is_current else '')
        out.append(f'<h3 id="{esc(rel["version"])}"><code>{esc(rel["version"])}</code> '
                   f'&middot; {esc(rel.get("date", ""))} {text_note}{current_note}</h3>')
        if rel.get("summary"):
            out.append(f'<p>{esc(rel["summary"])}</p>')
        out.append('<div class="tablewrap"><table>'
                   f'<tr><th>text version</th><td>{esc(rel.get("text_version", ""))}</td></tr>'
                   f'<tr><th>provisions root hash</th>'
                   f'<td><span class="hash">{esc(rh)}</span></td></tr></table></div>')
        if rel.get("changes"):
            out.append('<ul class="linklist">')
            for c in rel["changes"]:
                out.append(f'<li>{esc(c)}</li>')
            out.append('</ul>')

    out.append('<p class="actions">Release notes are maintained by hand in '
               f'<a href="{REPO_URL}/blob/dev/changelog.json">changelog.json</a>; the '
               'version number and tag are set by the CI pipeline on each push. '
               f'Full commit history is <a href="{REPO_URL}/commits">on GitHub</a>.</p>')
    return "\n".join(out)


# --------------------------------------------- twins for summary pages

def landing_md(relpath):
    counts = GATE6["counts"]
    defs = DELTA["definitions"]
    out = md_front("EU AI Act - Current Text (derived, verifiable)", relpath)
    out.append("The composed current text of the EU AI Act, published so it can be "
               "checked rather than merely read. No official consolidated version "
               "existed when this was generated; this one shows its working.")
    out.append("")
    out.append("## By the numbers")
    out.append("")
    out.append(f"- {len(ARTICLES)} articles and {len(ANNEXES)} annexes in the current text")
    out.append(f"- {len(G2)} amendment instructions, each with a derivation page")
    out.append(f"- {len(TOUCHED)} touched provisions, each hash-anchored and "
               f"currently not-reviewed")
    out.append(f"- definitions {defs['published']} -> {defs['composed']} "
               f"(SME and SMC added)")
    out.append(f"- {counts['only_ours']} provisions inserted, {counts['only_theirs']} "
               f"deleted, {counts['differ']} changed")
    out.append("")
    out.append("## Where to go")
    out.append("")
    out.append(f"- Agent index of everything: {SITE_URL}/llms.txt")
    out.append(f"- Whole text in one file: {SITE_URL}/exports/eu-ai-act-current.md")
    out.append(f"- Articles: {SITE_URL}/articles.md")
    out.append(f"- Derivations: {SITE_URL}/derivations.md")
    out.append(f"- How to verify: {SITE_URL}/verify.md")
    out.append(f"- Other public versions: {SITE_URL}/other-versions.md")
    out.append("")
    return "\n".join(out)


def landing_json(relpath):
    counts = GATE6["counts"]
    return {
        "schema": "eu-ai-act-current/v1", "kind": "site",
        "slug": twin_base(relpath), "url": page_url(relpath),
        "markdown_url": md_url(relpath),
        "title": "EU AI Act - Current Text (derived, verifiable)",
        "work": TREE.get("work", {}),
        "authentic": False, "not_legal_advice": True, "disclaimer": DISCLAIMER,
        "text_version": TEXT_VERSION, "generated_on": GENERATED_ON,
        "site_version": VERSION, "provisions_root_hash": BUILD_STATE["root_hash"],
        "counts": {
            "articles": len(ARTICLES), "annexes": len(ANNEXES),
            "instructions": len(G2), "touched_provisions": len(TOUCHED),
            "inserted": counts["only_ours"], "deleted": counts["only_theirs"],
            "changed": counts["differ"],
        },
        "entry_points": {
            "llms_txt": f"{SITE_URL}/llms.txt",
            "full_text_markdown": f"{SITE_URL}/exports/eu-ai-act-current.md",
            "full_text_json": f"{SITE_URL}/exports/eu-ai-act-current.json",
            "exports_manifest": f"{SITE_URL}/exports/MANIFEST.json",
            "provisions_hash_tree": f"{SITE_URL}/provisions/index.json",
            "articles_index": f"{SITE_URL}/articles.md",
            "derivations_index": f"{SITE_URL}/derivations.md",
            "verify": f"{SITE_URL}/verify.md",
            "report_a_check": f"{REPO_URL}/issues/new?template=provision-check.yml",
        },
    }


def index_md(relpath):
    out = md_front("Articles and annexes", relpath)
    out.append("| article | heading | changes | markdown |")
    out.append("|---|---|---|---|")
    for ch, sec, a in ARTICLES:
        counts = {}
        for other in TOUCHED:
            if other == a["id"] or other.startswith(a["id"] + "/"):
                st = provision_status(other)
                counts[st] = counts.get(st, 0) + 1
        note = ", ".join(f"{n} {st}" for st, n in sorted(counts.items())) or "unchanged"
        tail = article_tail(a["id"])
        out.append(f"| {a['label']} | {(a.get('heading') or '').strip()} | {note} | "
                   f"{SITE_URL}/articles/{tail}.md |")
    out.append("")
    out.append("| annex | heading | markdown |")
    out.append("|---|---|---|")
    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        out.append(f"| {ax['label']} | {(ax.get('heading') or '').strip()} | "
                   f"{SITE_URL}/annexes/{tail}.md |")
    out.append("")
    return "\n".join(out)


def index_json(relpath):
    arts = []
    for ch, sec, a in ARTICLES:
        counts = {}
        for other in TOUCHED:
            if other == a["id"] or other.startswith(a["id"] + "/"):
                st = provision_status(other)
                counts[st] = counts.get(st, 0) + 1
        tail = article_tail(a["id"])
        arts.append({
            "id": a["id"], "label": a["label"], "heading": a.get("heading"),
            "chapter": ch["label"], "section": sec["label"] if sec else None,
            "changes": counts,
            "url": f"{SITE_URL}/articles/{tail}/",
            "markdown_url": f"{SITE_URL}/articles/{tail}.md",
            "json_url": f"{SITE_URL}/articles/{tail}.llm.json",
        })
    anns = []
    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        anns.append({
            "id": ax["id"], "label": ax["label"], "heading": ax.get("heading"),
            "markdown_url": f"{SITE_URL}/annexes/{tail}.md",
            "json_url": f"{SITE_URL}/annexes/{tail}.llm.json",
        })
    return {
        "schema": "eu-ai-act-current/v1", "kind": "index",
        "slug": twin_base(relpath), "url": page_url(relpath),
        "markdown_url": md_url(relpath), "title": "Articles and annexes",
        "authentic": False, "not_legal_advice": True, "disclaimer": DISCLAIMER,
        "text_version": TEXT_VERSION, "site_version": VERSION,
        "provisions_root_hash": BUILD_STATE["root_hash"],
        "articles": arts, "annexes": anns,
    }


def derivations_index_md(relpath):
    out = md_front("Derivations - one per amendment instruction", relpath)
    out.append("Each entry shows one instruction from the Digital Omnibus and the "
               "provisions it changed. Checking one provision against one instruction "
               "takes about two minutes.")
    out.append("")
    out.append("| instruction | op | level | instruction text | markdown |")
    out.append("|---|---|---|---|---|")
    for ins in G2:
        out.append(f"| `{ins['id']}` | {ins['op']} | {ins['level']} | "
                   f"{ins['instruction_text']} | "
                   f"{SITE_URL}/derivations/{inst_slug(ins['id'])}.md |")
    out.append("")
    return "\n".join(out)


def derivations_index_json(relpath):
    return {
        "schema": "eu-ai-act-current/v1", "kind": "derivation-index",
        "slug": twin_base(relpath), "url": page_url(relpath),
        "markdown_url": md_url(relpath),
        "title": "Derivations - one per amendment instruction",
        "authentic": False, "not_legal_advice": True, "disclaimer": DISCLAIMER,
        "text_version": TEXT_VERSION, "site_version": VERSION,
        "provisions_root_hash": BUILD_STATE["root_hash"],
        "amending_act": {"celex": "32026R1744",
                         "title": "Regulation (EU) 2026/1744 (Digital Omnibus on AI)",
                         "in_force": "2026-07-27"},
        "instructions": [{
            "id": ins["id"], "op": ins["op"], "level": ins["level"],
            "instruction_text": ins["instruction_text"],
            "targets": [node_ref(t) for t in (ins.get("targets") or [])],
            "inserted": [node_ref(t) for t in (ins.get("inserted") or [])],
            "payload_xml_sha256": (ins.get("payload") or {}).get("xml_sha256"),
            "markdown_url": f"{SITE_URL}/derivations/{inst_slug(ins['id'])}.md",
            "json_url": f"{SITE_URL}/derivations/{inst_slug(ins['id'])}.llm.json",
        } for ins in G2],
    }


def downloads_md(relpath):
    out = md_front("Downloads", relpath)
    out.append("Seven formats of the same artefact, each sha256-anchored. Verify what "
               "you downloaded against these hashes.")
    out.append("")
    out.append("| format | url | bytes | sha256 |")
    out.append("|---|---|---|---|")
    for key, meta in EXPORTS_MANIFEST["files"].items():
        name = os.path.basename(meta["path"])
        out.append(f"| {key} | {SITE_URL}/exports/{name} | {meta['bytes']} | "
                   f"`{meta['sha256']}` |")
    out.append("")
    out.append(f"- Provisions hash tree: {SITE_URL}/provisions/index.json")
    out.append(f"- Source graphs: {SITE_URL}/data/graph/")
    out.append("")
    return "\n".join(out)


def verify_md(relpath):
    g2g, g3g = G2_MANIFEST["gates"], G3_MANIFEST["gates"]
    counts = GATE6["counts"]
    out = md_front("Verification - designed, not invited", relpath)
    out.append("## How to check one provision (about two minutes)")
    out.append("")
    out.append("1. Pick a changed provision from the derivation index "
               f"({SITE_URL}/derivations.md).")
    out.append("2. Read the quoted instruction and payload on its page - both are "
               "hash-anchored to the OJ Formex member of CELEX 32026R1744.")
    out.append("3. Compare the before (as published) and after (composed) text.")
    out.append(f"4. Report the result with the provision id and the root hash above: "
               f"{REPO_URL}/issues/new?template=provision-check.yml")
    out.append("")
    out.append("## Pipeline gates (all passing)")
    out.append("")
    out.append("| gate | what it proves | result |")
    out.append("|---|---|---|")
    out.append(f"| 1 payload round-trip | every quoted payload reproduces its OJ Formex "
               f"bytes | checked {g2g['gate1_payload_round_trip']['checked']}, failed "
               f"{g2g['gate1_payload_round_trip']['failed']} |")
    out.append(f"| 2 full consumption | every enacting-terms leaf parsed, no orphans | "
               f"{g2g['gate2_full_consumption']['parsed']}/"
               f"{g2g['gate2_full_consumption']['leaves']} parsed |")
    out.append(f"| 3 target existence | every instruction target exists as published | "
               f"checked {g2g['gate3_target_existence']['checked']}, missing "
               f"{g2g['gate3_target_existence']['missing']} |")
    out.append(f"| 2b all applied | every instruction applied | "
               f"{g3g['gate2_all_applied']['applied']}/"
               f"{g3g['gate2_all_applied']['instructions']} |")
    out.append(f"| 4 cross-reference closure | no dangling internal references | "
               f"checked {g3g['gate4_crossref_closure']['checked']}, unresolved "
               f"{len(g3g['gate4_crossref_closure']['unresolved'])} |")
    out.append("| 5 structural invariants | container counts move exactly as the "
               "instructions dictate | all consistent |")
    out.append(f"| 6 differ self-test | the differ reports exactly the composed changes | "
               f"agree {counts['agree']}, differ {counts['differ']}, inserted "
               f"{counts['only_ours']}, deleted {counts['only_theirs']} - "
               f"{GATE6['verdict']} |")
    out.append("")
    out.append("## Corrigenda")
    out.append("")
    out.append(G1_MANIFEST["finding"])
    out.append("")
    out.append("## Composition inputs (raw OJ bytes)")
    out.append("")
    out.append("| input | sha256 |")
    out.append("|---|---|")
    for key, meta in G3_MANIFEST.get("inputs", {}).items():
        out.append(f"| `{meta.get('path', key)}` | `{meta.get('sha256', '')}` |")
    out.append("")
    out.append("## Review register")
    out.append("")
    out.append(f"All {len(TOUCHED)} touched provisions are currently **not-reviewed**. "
               f"That incompleteness is the invitation.")
    out.append("")
    out.append("| provision | status | derivation |")
    out.append("|---|---|---|")
    for base_id in sorted(TOUCHED):
        insts = sorted(TOUCHED[base_id]["instructions"])
        der = f"{SITE_URL}/derivations/{inst_slug(insts[0])}.md" if insts else ""
        out.append(f"| `{base_id}` | {provision_status(base_id)} | {der} |")
    out.append("")
    return "\n".join(out)


def other_versions_md(relpath):
    out = md_front("The other public versions", relpath)
    out.append(f"Surveyed {OTHER_VERSIONS_DATE}. Most of these are free public goods "
               f"and this page links to them gladly; the point is only that a reader "
               f"should know which text they are reading.")
    out.append("")
    out.append("| source | url | state as of survey |")
    out.append("|---|---|---|")
    for name, url, state_desc in OTHER_VERSIONS:
        out.append(f"| {name} | {url} | {state_desc} |")
    out.append("")
    out.append("Only the Official Journal publications are authentic. Even official "
               "consolidated texts state that they have documentary value only.")
    out.append("")
    return "\n".join(out)


def version_md(relpath):
    out = md_front(f"Version {VERSION}", relpath)
    out.append("## This build")
    out.append("")
    out.append(f"- Site version: {VERSION}")
    out.append(f"- Composed text version: {TEXT_VERSION}")
    out.append(f"- Text generated: {GENERATED_ON}")
    out.append(f"- Provisions root hash: `{BUILD_STATE['root_hash']}`")
    out.append(f"- {len(ARTICLES)} articles, {len(ANNEXES)} annexes, "
               f"{len(G2)} derivations, {len(TOUCHED)} touched provisions")
    out.append("")
    out.append("## Releases")
    out.append("")
    out.append("`text unchanged` means the composed legal text is byte-for-byte what "
               "the previous release published, so a provision already checked stays "
               "checked.")
    out.append("")
    for i, rel in enumerate(CHANGELOG):
        prev = CHANGELOG[i + 1] if i + 1 < len(CHANGELOG) else None
        rh = rel.get("provisions_root_hash", "")
        if prev is None:
            note = "first release"
        elif rh and rh == prev.get("provisions_root_hash"):
            note = "text unchanged"
        else:
            note = "text changed"
        cur = " (current)" if rel["version"] == VERSION else ""
        out.append(f"### {rel['version']} - {rel.get('date', '')} [{note}]{cur}")
        out.append("")
        if rel.get("summary"):
            out.append(rel["summary"])
            out.append("")
        out.append(f"- text version: {rel.get('text_version', '')}")
        out.append(f"- provisions root hash: `{rh}`")
        out.append("")
        for c in rel.get("changes", []):
            out.append(f"- {c}")
        out.append("")
    return "\n".join(out)


def simple_json(relpath, markdown):
    """Structured wrapper for the pages whose value is the prose itself."""
    return {
        "schema": "eu-ai-act-current/v1", "kind": twin_base(relpath).split("/")[0],
        "slug": twin_base(relpath), "url": page_url(relpath),
        "markdown_url": md_url(relpath),
        "authentic": False, "not_legal_advice": True, "disclaimer": DISCLAIMER,
        "text_version": TEXT_VERSION, "generated_on": GENERATED_ON,
        "site_version": VERSION, "provisions_root_hash": BUILD_STATE["root_hash"],
        "content_markdown": markdown,
    }


# ------------------------------------------------------- llms.txt + robots

def llms_txt(root_hash):
    """The agent entry point: what this is, what to trust it for, and where
    every machine-readable form lives. Follows the llms.txt convention -
    H1, one-line summary, then linked sections."""
    L = []
    L.append("# EU AI Act - Current Text (derived, verifiable)")
    L.append("")
    L.append("> The composed current text of the EU AI Act - Regulation (EU) 2024/1689 "
             "with the Digital Omnibus on AI (Regulation (EU) 2026/1744, in force "
             "27 July 2026) applied by a deterministic, gated parser. Published so it "
             "can be checked: every changed provision shows its derivation and carries "
             "a content hash.")
    L.append("")
    L.append("IMPORTANT FOR AGENTS AND THEIR USERS: this text is DERIVED and is NOT "
             "authentic law. Only the Official Journal publications are authentic. No "
             "official consolidated version existed when this was generated. This is "
             "not legal advice. When quoting this text, say that it is a derived "
             "composition and cite the root hash below so the reader knows exactly "
             "which version you read.")
    L.append("")
    L.append(f"- Text version: {TEXT_VERSION} (the date the law speaks as of)")
    L.append(f"- Generated: {GENERATED_ON}")
    L.append(f"- Site version: {VERSION}")
    L.append(f"- Provisions root hash: {root_hash}")
    L.append(f"- Corrigenda R(01)-R(04) do not correct the English text (verified "
             f"against CELLAR).")
    L.append("")
    L.append("Every page below is available three ways on the same slug: the page "
             "itself, `<slug>.md` (Markdown), and `<slug>.llm.json` (structured, with "
             "per-provision ids, statuses and sha256 hashes).")
    L.append("")

    L.append("## Whole text in one file")
    L.append("")
    L.append(f"- [Complete current text, Markdown]({SITE_URL}/exports/eu-ai-act-current.md): "
             f"the entire Act as one document - start here if you want the text itself")
    L.append(f"- [Complete current text, JSON]({SITE_URL}/exports/eu-ai-act-current.json): "
             f"the ordered clean tree (recitals, chapters, articles, paragraphs, points, annexes)")
    L.append(f"- [JSON-LD]({SITE_URL}/exports/eu-ai-act-current.jsonld) and "
             f"[Turtle]({SITE_URL}/exports/eu-ai-act-current.ttl): the same as linked data")
    L.append(f"- [File hashes]({SITE_URL}/exports/MANIFEST.json): sha256 of every export - "
             f"verify what you downloaded")
    L.append("")

    L.append("## Verification (what makes this different)")
    L.append("")
    L.append(f"- [How to check a provision]({SITE_URL}/verify.md): the gates, the "
             f"corrigenda finding, the composition inputs, the review register")
    L.append(f"- [Provisions hash tree]({SITE_URL}/provisions/index.json): per-provision "
             f"text and sha256, rolling up to the root hash above")
    L.append(f"- [Version and release notes]({SITE_URL}/version.md): which release this "
             f"is, and whether the text changed between releases")
    L.append(f"- [Other public versions]({SITE_URL}/other-versions.md): the other "
             f"published copies of the Act and how stale each was when surveyed")
    L.append(f"- Report a check: {REPO_URL}/issues/new?template=provision-check.yml")
    L.append("")

    L.append("## Articles")
    L.append("")
    L.append(f"- [Article index]({SITE_URL}/articles.md)")
    for ch, sec, a in ARTICLES:
        counts = {}
        for other in TOUCHED:
            if other == a["id"] or other.startswith(a["id"] + "/"):
                st = provision_status(other)
                counts[st] = counts.get(st, 0) + 1
        note = ", ".join(f"{n} {st}" for st, n in sorted(counts.items())) or "unchanged"
        tail = article_tail(a["id"])
        heading = (a.get("heading") or "").strip()
        L.append(f"- [{a['label']} - {heading}]({SITE_URL}/articles/{tail}.md): {note}")
    L.append("")

    L.append("## Annexes")
    L.append("")
    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        heading = (ax.get("heading") or "").strip()
        L.append(f"- [{ax['label']} - {heading}]({SITE_URL}/annexes/{tail}.md)")
    L.append("")

    L.append("## Derivations (one per amendment instruction)")
    L.append("")
    L.append(f"- [Derivation index]({SITE_URL}/derivations.md)")
    for ins in G2:
        L.append(f"- [{ins['id']}]({SITE_URL}/derivations/{inst_slug(ins['id'])}.md): "
                 f"{ins['op']} at {ins['level']} - {ins['instruction_text']}")
    L.append("")

    L.append("## Optional")
    L.append("")
    L.append(f"- [Human-readable site]({SITE_URL}/)")
    L.append(f"- [Source, data and build script]({REPO_URL})")
    L.append(f"- [PDF]({SITE_URL}/exports/eu-ai-act-current.pdf) and "
             f"[DOCX]({SITE_URL}/exports/eu-ai-act-current.docx) for filing and citing")
    L.append("")
    return "\n".join(L)


def sitemap_xml():
    urls = sorted({page_url(rel) for rel in PAGES})
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        out.append(f"  <url><loc>{esc(u)}</loc><lastmod>{GENERATED_ON}</lastmod></url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def robots_txt():
    return ("# This is a public good: crawl it, quote it, feed it to a model.\n"
            "# Please carry the disclaimer with the text - it is derived, not\n"
            "# authentic law, and it is not legal advice.\n"
            "User-agent: *\n"
            "Allow: /\n"
            "\n"
            f"Sitemap: {SITE_URL}/sitemap.xml\n"
            f"# Agent index: {SITE_URL}/llms.txt\n")


# ------------------------------------------------------------------ build

def main(assemble_dir=None):
    prov_files, root_hash = build_provisions()
    write_provisions(prov_files)
    BUILD_STATE["root_hash"] = root_hash
    for relpath, data in prov_files.items():
        if relpath.endswith("/text.md"):
            PROV_HASH[relpath[: -len("/text.md")]] = sha256_bytes(data)

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
<span class="eyebrow"><span class="d"></span>DERIVED &middot; VERIFIABLE &middot; NOT AUTHENTIC</span>
<h1>The EU AI Act, as it now reads</h1>
<p class="hero"><b>Regulation (EU) 2024/1689</b> with the Digital Omnibus on AI
(Regulation (EU) 2026/1744, in force 27 July 2026) applied &mdash; published so it can be
<b>checked</b>, not just read. No official consolidated version existed when this was
generated; this one shows its working.</p>
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

    # ---- current text page: reuse the export HTML as the base and wrap it in
    # the site chrome. Only CSS_CHROME goes in - the export keeps its own
    # Official Journal typography, which is the point of reusing it.
    with open(os.path.join(ROOT, "exports", "eu-ai-act-current.html"), encoding="utf-8") as f:
        export_html = f.read()
    # The export styles its own <body> with `padding:0 8%` to inset the legal
    # text. Our chrome lives in that same body, so it inherits the inset and
    # stops being full-bleed. Clear the body padding and move the gutter onto
    # the text container instead: max-width 868 - 2x24 gutter = the 820px
    # measure the export was designed around (box-sizing:border-box applies).
    # These are literal-string splices into a file we do not control. If a
    # future export changes shape (e.g. emits `<body class=...>`), a silent
    # no-op replace would ship the page with no chrome at all - so require
    # each anchor rather than letting it pass.
    def splice(doc, anchor, replacement):
        if anchor not in doc:
            raise SystemExit(
                f"export HTML has no {anchor!r} anchor - cannot wrap it in the "
                f"site chrome; update scripts/build_site.py to match the new export")
        return doc.replace(anchor, replacement, 1)

    injected = splice(
        export_html, "</head>",
        f"<style>{CSS_TOKENS}{CSS_CHROME}\n"
        "body{margin:0;padding:0}\n"
        ".eli-container{max-width:868px;padding:24pt 24px 48pt}\n"
        "</style>\n"
        # This page has no .md twin of its own: the Markdown export IS the
        # whole text, so point at it rather than publishing a second copy.
        f'<link rel="alternate" type="text/markdown" '
        f'href="{SITE_URL}/exports/eu-ai-act-current.md">\n'
        f'<link rel="alternate" type="application/json" '
        f'href="{SITE_URL}/exports/eu-ai-act-current.json">\n'
        "</head>")
    injected = splice(injected, "<body>", "<body>\n" + nav_html("../"))
    injected = splice(injected, "</body>", footer_html(root_hash) + "\n</body>")
    PAGES["current-text/index.html"] = injected

    # ---- article index + pages
    toc = ['<h1>Articles</h1>', '<ul class="toc">']
    for ch, sec, a in ARTICLES:
        tail = article_tail(a["id"])
        toc.append(f'<li><a href="{tail}/index.html">{esc(a["label"])}</a> '
                   f'{esc(a.get("heading") or "")}{rollup_badges(a["id"])}</li>')
    toc.append("</ul>")
    toc.append('<h2>Annexes</h2><ul class="toc">')
    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        toc.append(f'<li><a href="../annexes/{tail}/index.html">{esc(ax["label"])}</a> '
                   f'{esc(ax.get("heading") or "")}{rollup_badges(ax["id"])}</li>')
    toc.append("</ul>")
    add_page("articles/index.html", "Articles - EU AI Act current text", "\n".join(toc), root_hash)

    for ch, sec, a in ARTICLES:
        tail = article_tail(a["id"])
        rel = f"articles/{tail}/index.html"
        add_page(rel, f'{a["label"]} - {a.get("heading") or ""} - EU AI Act current text',
                 article_body(ch, sec, a, "../../"), root_hash)
        add_twin(rel, article_md(ch, sec, a, rel), article_json(ch, sec, a, rel))

    for ax in ANNEXES:
        tail = article_tail(ax["id"])
        rel = f"annexes/{tail}/index.html"
        add_page(rel, f'{ax["label"]} - {ax.get("heading") or ""} - EU AI Act current text',
                 annex_body(ax, "../../"), root_hash)
        add_twin(rel, annex_md(ax, rel), annex_json(ax, rel))

    # ---- derivation index + pages
    didx = ['<h1>Derivation pages &mdash; one per amendment instruction</h1>',
            '<p>Each page shows the original text, the exact amending instruction (quoted official '
            'text, hash-anchored to the OJ Formex bytes), the result, and a word-level diff. '
            'A checker verifies one paragraph against one instruction in about two minutes.</p>',
            '<div class="tablewrap"><table><tr><th>instruction</th><th>op</th><th>level</th><th>instruction text</th></tr>']
    for ins in G2:
        slug = inst_slug(ins["id"])
        didx.append(f'<tr><td><a href="{slug}.html"><code>{esc(ins["id"])}</code></a></td>'
                    f'<td>{esc(ins["op"])}</td><td>{esc(ins["level"])}</td>'
                    f'<td>{esc(ins["instruction_text"])}</td></tr>')
    didx.append("</table></div>")
    add_page("derivations/index.html", "Derivations - EU AI Act current text", "\n".join(didx), root_hash)

    for ins in G2:
        slug = inst_slug(ins["id"])
        rel = f"derivations/{slug}.html"
        add_page(rel, f'Derivation {ins["id"]} - EU AI Act current text',
                 instruction_page_body(ins, "../"), root_hash)
        add_twin(rel, derivation_md(ins, rel), derivation_json(ins, rel))

    # ---- downloads
    dl = ['<h1>Downloads</h1>',
          '<p>Seven formats, one artefact: a lawyer files the PDF, a reader browses the HTML, '
          'a repository absorbs the Markdown, a tool queries the graph &mdash; and an agent reads '
          'any of them without a scraper. Every file is sha256-anchored; verify what you downloaded.</p>',
          '<div class="tablewrap"><table><tr><th>format</th><th>file</th><th>bytes</th><th>sha256</th></tr>']
    for key, meta in EXPORTS_MANIFEST["files"].items():
        name = os.path.basename(meta["path"])
        dl.append(f'<tr><td>{esc(key)}</td><td><a href="../exports/{esc(name)}">{esc(name)}</a></td>'
                  f'<td>{meta["bytes"]}</td><td class="hash">{esc(meta["sha256"])}</td></tr>')
    dl.append("</table></div>")
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
         '<div class="tablewrap"><table><tr><th>gate</th><th>what it proves</th><th>result</th></tr>']
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
    v.append("</table></div>")
    v.append('<p><b>The standing claim:</b> the day EUR-Lex publishes its official consolidation, this '
             'differ machine-diffs our composition against it &mdash; the differ is already proven by '
             'self-test. <a href="../data/graph/gate6-self-test.json">Full self-test data.</a></p>')
    v.append('<h2>Corrigenda</h2>')
    v.append(f'<p>{esc(G1_MANIFEST["finding"])} '
             f'(<a href="../data/graph/g1-manifest.json">evidence</a>)</p>')
    v.append('<h2>Composition inputs (raw OJ bytes, hash-anchored)</h2>')
    v.append('<div class="tablewrap"><table><tr><th>input</th><th>sha256</th></tr>')
    for key, meta in G3_MANIFEST.get("inputs", {}).items():
        v.append(f'<tr><td><code>{esc(meta.get("path", key))}</code></td>'
                 f'<td class="hash">{esc(meta.get("sha256", ""))}</td></tr>')
    v.append("</table></div>")
    v.append('<p>The raw OJ zips stay in the authoring vault; the site cites their hashes rather than '
             'hosting them. Anyone can fetch the same CELEX documents from EUR-Lex and compare.</p>')
    v.append('<h2>Review register</h2>')
    v.append(f'<p>Human verification is openly incomplete &mdash; that incompleteness is the invitation. '
             f'All {len(TOUCHED)} touched provisions launch as <b>not-reviewed</b>. Check one and '
             f'file the result; this register moves as issues are resolved.</p>')
    v.append('<div class="tablewrap"><table><tr><th>provision</th><th>status</th><th>review</th><th>derivation</th><th>act</th></tr>')
    for base_id in sorted(TOUCHED):
        st = provision_status(base_id)
        insts = sorted(TOUCHED[base_id]["instructions"])
        dlink = f'<a href="../derivations/{inst_slug(insts[0])}.html">derivation</a>' if insts else ""
        v.append(f'<tr><td><code>{esc(base_id)}</code></td><td>{esc(st or "")}</td>'
                 f'<td>not-reviewed</td><td>{dlink}</td>'
                 f'<td><a href="{issue_url(base_id)}">check &amp; report</a></td></tr>')
    v.append("</table></div>")
    add_page("verify/index.html", "Verify - EU AI Act current text", "\n".join(v), root_hash)

    # ---- other versions
    ov = ['<h1>The other public versions</h1>',
          f'<p>Surveyed {OTHER_VERSIONS_DATE}; states as found on that date (links may since have '
          'been updated &mdash; that would be good news). Most of these are free public goods and '
          'this page links to them gladly; the point is only that a reader should know which text '
          'they are reading. Ours is derived and says so; theirs are listed with the state each '
          'was in.</p>',
          '<div class="tablewrap"><table><tr><th>source</th><th>state (as of survey date)</th></tr>']
    for name, url, state_desc in OTHER_VERSIONS:
        ov.append(f'<tr><td><a href="{esc(url)}">{esc(name)}</a></td><td>{esc(state_desc)}</td></tr>')
    ov.append("</table></div>")
    ov.append('<p>Only the Official Journal publications are authentic. Even official consolidated '
              'texts state that they have documentary value only &mdash; and ours sits further from '
              'authority still, which is exactly why it publishes its derivation and asks to be checked.</p>')
    add_page("other-versions/index.html", "Other public versions - EU AI Act current text",
             "\n".join(ov), root_hash)

    # ---- version / release notes
    add_page("version/index.html", f"Version {VERSION} - EU AI Act current text",
             version_body(root_hash), root_hash)

    # ---- twins for the landing and summary pages
    add_twin("index.html", landing_md("index.html"), landing_json("index.html"))
    add_twin("articles/index.html", index_md("articles/index.html"),
             index_json("articles/index.html"))
    add_twin("derivations/index.html", derivations_index_md("derivations/index.html"),
             derivations_index_json("derivations/index.html"))
    for rel, builder in (("downloads/index.html", downloads_md),
                         ("verify/index.html", verify_md),
                         ("other-versions/index.html", other_versions_md),
                         ("version/index.html", version_md)):
        add_twin(rel, builder(rel), simple_json(rel, builder(rel)))

    # ---- agent entry points
    MD_PAGES["llms.txt"] = llms_txt(root_hash)
    MD_PAGES["sitemap.xml"] = sitemap_xml()
    MD_PAGES["robots.txt"] = robots_txt()

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

    # gate E: every page carries the full chrome. The current-text page is
    # built by splicing into an export we do not control, so "chrome silently
    # missing" is a real failure mode, not a hypothetical one.
    for rel, content in PAGES.items():
        for needle, what in ((BRAND_URL, "Risk Mandate banner link"),
                             ('class="top"', "site header"),
                             ('class="foot"', "footer"),
                             (root_hash, "root hash in footer")):
            if needle not in content:
                failures.append(f"gate E: {what} missing from {rel}")

    # gate F: if the changelog claims a root hash / text version for the
    # version being built, it must be THIS build's. Release notes that
    # misstate which text was published would undermine the one thing a
    # checker relies on, so this is a hard failure rather than a warning.
    entry = next((r for r in CHANGELOG if r["version"] == VERSION), None)
    if entry is not None:
        claimed = entry.get("provisions_root_hash")
        if claimed and claimed != root_hash:
            failures.append(
                f"gate F: changelog.json says {VERSION} has root hash {claimed}, "
                f"but this build produced {root_hash}")
        claimed_tv = entry.get("text_version")
        if claimed_tv and claimed_tv != TEXT_VERSION:
            failures.append(
                f"gate F: changelog.json says {VERSION} is text version "
                f"{claimed_tv}, but this build is {TEXT_VERSION}")
    else:
        print(f"note: no changelog.json entry for {VERSION} - the version page "
              f"will say so", file=sys.stderr)

    # gate G: the machine-readable twins must stay in lockstep with the pages.
    # An agent that finds a page but no .md is back to scraping HTML, which is
    # the thing this layer exists to avoid.
    NO_TWIN = {"current-text/index.html"}   # its twin is the Markdown export
    for rel in PAGES:
        if rel in NO_TWIN:
            continue
        base = twin_base(rel)
        if base + ".md" not in MD_PAGES:
            failures.append(f"gate G: no .md twin for {rel}")
        if base + ".llm.json" not in JSON_PAGES:
            failures.append(f"gate G: no .llm.json twin for {rel}")
    for rel, content in PAGES.items():
        if rel not in NO_TWIN and md_url(rel) not in content:
            failures.append(f"gate G: {rel} does not advertise its .md twin")
    for rel, text in MD_PAGES.items():
        if rel.endswith(".md") and DISCLAIMER not in text:
            failures.append(f"gate G: {rel} is missing the disclaimer")

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
    for rel, content in MD_PAGES.items():
        p = os.path.join(site_root, rel)
        os.makedirs(os.path.dirname(p) or site_root, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    for rel, data in JSON_PAGES.items():
        p = os.path.join(site_root, rel)
        os.makedirs(os.path.dirname(p) or site_root, exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    print(f"site: {len(PAGES)} pages "
          f"({len(ARTICLES)} articles, {len(ANNEXES)} annexes, {len(G2)} derivations)")
    print(f"machine-readable: {len(MD_PAGES)} .md/.txt/.xml, {len(JSON_PAGES)} .llm.json")
    print(f"provisions: {len(prov_files)} files, root hash {root_hash}")
    print("gates: A, B, C, D, E, F, G all passed")

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
