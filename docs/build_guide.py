#!/usr/bin/env python3
"""Assemble the section fragments in docs/sections/ into one styled HTML guide."""
import glob, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SECTIONS_DIR = os.path.join(HERE, "sections")
OUT = os.path.join(HERE, "ORA_Navigator_Complete_Guide.html")

# (id, short title for the table of contents) in order
TOC = [
    ("overview",    "What ORA Navigator Is — Overview, Architecture & Cloud"),
    ("rag",         "How an Answer Is Made — the Chat & RAG Pipeline"),
    ("memory",      "The Memory System — How It Remembers You"),
    ("research",    "The Self-Healing Research Pipeline & Ticket System"),
    ("agents",      "The AI Agents & Proposals / Forms"),
    ("database",    "The Database, Caching & Authentication"),
    ("crons",       "Cron Jobs & All Scheduled Work"),
    ("kb",          "The Knowledge Base & Faithfulness Eval"),
    ("frontend",    "The Frontend, How Everything Connects & Changelog"),
    ("where-saved", "“Where Is It Saved?” Master Reference Table"),
    ("glossary",    "Glossary — Every Term in One Sentence"),
]

files = sorted(glob.glob(os.path.join(SECTIONS_DIR, "*.html")))
assert len(files) == len(TOC), f"expected {len(TOC)} sections, found {len(files)}"

body_parts = []
for i, f in enumerate(files):
    with open(f, encoding="utf-8") as fh:
        body_parts.append('<section class="chapter">\n' + fh.read() + "\n</section>")
body = "\n".join(body_parts)

toc_rows = "\n".join(
    f'<li><span class="tnum">{i+1}</span>'
    f'<a href="#{cid}">{title}</a></li>'
    for i, (cid, title) in enumerate(TOC)
)

DATE = "June 8, 2026"

CSS = """
:root{
  --navy:#0a1f44; --navy2:#13315c; --orange:#f7941e; --ink:#1a1f29;
  --muted:#5b6472; --line:#d9dee6; --soft:#f3f6fb; --callout:#fff7ec;
}
*{box-sizing:border-box;}
html{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }
body{
  font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  color:var(--ink); line-height:1.5; font-size:11pt; margin:0;
}
@page{ size:Letter; margin:18mm 16mm; }

/* ---------- cover ---------- */
.cover{
  height:100vh; background:linear-gradient(160deg,var(--navy),var(--navy2));
  color:#fff; display:flex; flex-direction:column; justify-content:center;
  padding:0 22mm; page-break-after:always; position:relative;
}
.cover .kicker{ letter-spacing:.28em; text-transform:uppercase; font-size:11pt;
  color:var(--orange); font-weight:700; margin-bottom:14px;}
.cover h1{ font-size:40pt; line-height:1.05; margin:0 0 10px; font-weight:800;}
.cover h2{ font-size:15pt; font-weight:400; color:#cdd8ec; margin:0 0 36px; border:0; padding:0;}
.cover .rule{ width:70px; height:5px; background:var(--orange); border-radius:3px; margin:0 0 28px;}
.cover .meta{ font-size:10.5pt; color:#aebbd4; }
.cover .meta b{ color:#fff; font-weight:600; }
.cover .tag{ position:absolute; bottom:20mm; left:22mm; right:22mm;
  font-size:9.5pt; color:#8ea0c2; border-top:1px solid #2b4470; padding-top:10px;}

/* ---------- table of contents ---------- */
.toc{ page-break-after:always; padding-top:6mm; }
.toc h2{ border:0; }
.toc ol, .toc ul{ list-style:none; padding:0; margin:0; }
.toc li{ display:flex; align-items:baseline; gap:12px; padding:9px 0;
  border-bottom:1px solid var(--line); font-size:12pt;}
.toc .tnum{ color:var(--orange); font-weight:800; width:26px; flex:0 0 26px; }
.toc a{ color:var(--ink); text-decoration:none; }

/* ---------- chapters ---------- */
.chapter{ page-break-before:always; }
h2{
  color:var(--navy); font-size:20pt; font-weight:800; margin:0 0 14px;
  padding-bottom:8px; border-bottom:3px solid var(--orange); line-height:1.15;
}
h3{ color:var(--navy2); font-size:14pt; margin:22px 0 8px; font-weight:700; }
h4{ color:var(--ink); font-size:11.5pt; margin:16px 0 6px; font-weight:700; }
p{ margin:8px 0; }
ul,ol{ margin:8px 0 8px 4px; padding-left:22px; }
li{ margin:4px 0; }
strong{ color:#11203b; }

/* code + diagrams */
code{ font-family:"SF Mono",Menlo,Consolas,monospace; font-size:9.2pt;
  background:var(--soft); padding:1px 5px; border-radius:4px; color:#1f3a63;
  word-break:break-word; }
pre{ background:#0d1b33; color:#dce6f5; font-family:"SF Mono",Menlo,Consolas,monospace;
  font-size:8.2pt; line-height:1.35; padding:12px 14px; border-radius:8px;
  overflow:hidden; white-space:pre; page-break-inside:avoid; margin:12px 0; }
pre code{ background:none; color:inherit; padding:0; }

/* tables */
table{ border-collapse:collapse; width:100%; margin:12px 0; font-size:9.6pt; }
th,td{ border:1px solid var(--line); padding:6px 9px; text-align:left;
  vertical-align:top; word-break:normal; overflow-wrap:anywhere; }
thead th{ background:var(--navy); color:#fff; font-weight:600; }
tbody tr:nth-child(even){ background:var(--soft); }
tr{ page-break-inside:avoid; }

/* callouts */
.callout{ background:var(--callout); border-left:4px solid var(--orange);
  padding:10px 14px; margin:12px 0; border-radius:0 6px 6px 0; font-size:10pt;
  page-break-inside:avoid; }
.callout strong{ color:#9a5b00; }

a{ color:#1f5fae; }
"""

html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>ORA Navigator — Complete Technical Guide</title>
<style>{CSS}</style></head>
<body>

<div class="cover">
  <div class="kicker">Technical Reference</div>
  <h1>ORA Navigator</h1>
  <div class="rule"></div>
  <h2>A complete, plain-English guide to every feature, pipeline,<br>
      cron job, AI agent, and data store — and where everything is saved.</h2>
  <div class="meta">
    AI Assistant for the Office of Research Administration<br>
    <b>Morgan State University</b> &nbsp;&middot;&nbsp; ora.inavigator.ai<br><br>
    Compiled from the live codebase &nbsp;&middot;&nbsp; <b>{DATE}</b>
  </div>
  <div class="tag">Three services (Frontend / Backend / ADK Agent) &nbsp;&middot;&nbsp;
     Google Cloud (Cloud Run, Cloud SQL, Vertex AI Search) &nbsp;&middot;&nbsp;
     11 chapters</div>
</div>

<div class="toc">
  <h2>Contents</h2>
  <ul>
    {toc_rows}
  </ul>
</div>

{body}

</body></html>
"""

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(html)
print("Wrote", OUT, f"({len(html):,} bytes, {len(files)} sections)")
