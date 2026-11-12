"""Page shell for the HTML report: design tokens / themes / print CSS, masthead, section nav, hero, footer.

Design: light by default, dark via ``prefers-color-scheme`` and a pure-CSS manual toggle (hidden checkbox
``#theme-toggle`` placed first in <body> + ``:root:has(#theme-toggle:checked)``), responsive, printable.
No JavaScript, no external resources, no SVG namespaces. Section modules add their own styles through a
``CSS_EXTRA`` string that report_html appends after ``CSS`` (so they may override anything here).
"""
from benchmark import baseline_source, build_rows
from html_components import (
    NO_SPEC_TEXT,
    is_num,
    fmt,
    esc,
    esc_raw,
    lab,
    val_html,
    dig,
    items_of,
    find_item,
    verdict_class,
)

# --------------------------------------------------------------------------- CSS

_LIGHT_VARS = """
  --bg:#f4f4f4; --surface:#ffffff; --surface-2:#f4f4f4; --text:#161616; --muted:#525252;
  --line:#e0e0e0; --accent:#0f62fe; --accent-bg:#e0eaff;
  --ok:#198038; --warn:#8e6a00; --bad:#da1e28;
  --ok-bg:#defbe6; --warn-bg:#fcf4d6; --bad-bg:#fff1f1;
  --shadow:none;
  color-scheme:light;
"""

_DARK_VARS = """
  --bg:#161616; --surface:#262626; --surface-2:#1c1c1c; --text:#f4f4f4; --muted:#c6c6c6;
  --line:#393939; --accent:#78a9ff; --accent-bg:#1c2b4a;
  --ok:#42be65; --warn:#f1c21b; --bad:#ff8389;
  --ok-bg:#0e3a1a; --warn-bg:#3a2d0c; --bad-bg:#4a1a1d;
  --shadow:none;
  color-scheme:dark;
"""

_CSS_BODY = r"""
:root{
  --mono:ui-monospace,"Cascadia Code","SF Mono",Consolas,"Liberation Mono",Menlo,monospace;
  --font:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei UI","Microsoft YaHei",
    "Noto Sans CJK SC","Noto Sans SC",sans-serif;
  --radius:0; --radius-sm:0;
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-5:24px; --space-6:32px; --space-7:48px;
  --mast-h:56px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);font-size:15px;line-height:1.6;
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,p,ul,ol,dl,dd{margin:0}
ul,ol{padding-left:20px}
a{color:var(--accent)}
code,pre,kbd,samp{font-family:var(--mono)}
small{font-size:12px}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:3px}
.theme-toggle{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;overflow:hidden}
body:has(.theme-toggle:focus-visible) .theme-label{outline:3px solid var(--accent);outline-offset:2px}
.skip-link{position:absolute;left:-9999px;top:0;background:var(--accent);color:var(--surface);padding:8px 14px;z-index:60}
.skip-link:focus{left:8px;top:8px}

/* ---- masthead (sticky) ---- */
.masthead{position:sticky;top:0;z-index:50;background:var(--surface);border-bottom:1px solid var(--line);
  border-top:3px solid var(--accent);box-shadow:var(--shadow)}
.mast-inner{max-width:1440px;margin:0 auto;padding:0 var(--space-5);min-height:var(--mast-h);display:flex;
  align-items:center;gap:var(--space-4)}
.wordmark{font-size:16px;font-weight:600;color:var(--text);text-decoration:none;white-space:nowrap;min-width:0;overflow:hidden;text-overflow:ellipsis}
.mast-label{font-size:12px;letter-spacing:.4px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mast-spacer{flex:1}
.model-chip{font-family:var(--mono);font-size:13px;background:var(--surface-2);border:1px solid var(--line);
  border-radius:999px;padding:2px 12px;max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.theme-label{display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none;font-size:13px;
  border:1px solid var(--line);border-radius:999px;padding:4px 12px;background:var(--surface);color:var(--text);
  white-space:nowrap}
.theme-label:hover{background:var(--surface-2)}
.theme-label .ti{font-size:15px;line-height:1}

/* ---- layout: left TOC >= 1100px, chip row below ---- */
.layout{max-width:1440px;margin:0 auto;padding:var(--space-5);display:grid;grid-template-columns:200px minmax(0,1fr);
  gap:var(--space-6);align-items:start}
.toc{position:sticky;top:calc(var(--mast-h) + 16px);align-self:start;font-size:14px}
.toc-title{font-size:12px;letter-spacing:.6px;color:var(--muted);margin:0 0 var(--space-2) var(--space-3)}
.toc ol{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.toc a{display:flex;gap:var(--space-2);align-items:baseline;padding:6px var(--space-3);border-radius:var(--radius-sm);
  color:var(--text);text-decoration:none;border-left:3px solid transparent}
.toc a:hover{background:var(--surface-2);border-left-color:var(--accent)}
.toc .n{font-family:var(--mono);font-size:12px;color:var(--accent)}
.page{min-width:0}
.section-heading,.hero{scroll-margin-top:calc(var(--mast-h) + 64px)}

/* ---- hero ---- */
.hero{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:var(--space-5);align-items:stretch;
  margin-bottom:var(--space-5)}
.hero-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);
  padding:var(--space-5);display:flex;flex-direction:column;gap:var(--space-3)}
.eyebrow{font-size:12px;letter-spacing:.8px;color:var(--muted)}
.hero h1{font-size:30px;font-weight:700;line-height:1.25;letter-spacing:-.2px;overflow-wrap:anywhere}
.verdict-line{display:flex;flex-wrap:wrap;gap:var(--space-2) var(--space-3);align-items:center;font-size:14px;color:var(--muted)}
.vbadge{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:15px;border-radius:999px;
  padding:3px 14px;background:var(--surface-2);color:var(--text);border:1px solid var(--line)}
.vbadge.green{background:var(--ok-bg);color:var(--ok)}
.vbadge.amber{background:var(--warn-bg);color:var(--warn)}
.vbadge.red{background:var(--bad-bg);color:var(--bad)}
.model-line{display:flex;flex-wrap:wrap;gap:var(--space-2) var(--space-3);align-items:center;font-size:14px;color:var(--muted)}
.model{font-family:var(--mono);font-size:16px;font-weight:600;color:var(--text);overflow-wrap:anywhere}
.divider{width:1px;height:16px;background:var(--line)}
.chips{display:flex;flex-wrap:wrap;gap:var(--space-2)}
.chip{background:var(--surface-2);border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:12px;
  padding:3px 12px;line-height:1.5;overflow-wrap:anywhere}
.chip b{color:var(--text);font-weight:600;margin-left:6px}
.stats{display:grid;grid-template-columns:1fr;gap:var(--space-3)}
.stat{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:var(--space-3) var(--space-4);display:grid;grid-template-columns:minmax(0,1fr) auto;
  align-items:center;column-gap:var(--space-4)}
.stat.green{border-left-color:var(--ok)}
.stat.amber{border-left-color:var(--warn)}
.stat.red{border-left-color:var(--bad)}
.stat-label{font-size:14px;font-weight:600}
.stat-caption{grid-column:1;font-size:12px;color:var(--muted);line-height:1.45}
.stat-caption.green{color:var(--ok)}
.stat-caption.amber{color:var(--warn)}
.stat-caption.red{color:var(--bad)}
.stat-value{grid-column:2;grid-row:1/span 2;font-size:44px;font-weight:700;line-height:1;letter-spacing:-1px;text-align:right;white-space:nowrap}
.stat-value small{font-size:14px;font-weight:400;color:var(--muted);margin-left:4px;letter-spacing:0}

/* ---- sections ---- */
.section-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:var(--space-4);flex-wrap:wrap;
  margin:var(--space-6) 0 var(--space-3);padding-bottom:var(--space-2);border-bottom:2px solid var(--line)}
.heading-left{display:flex;align-items:center;gap:var(--space-3)}
.section-index{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--accent);background:var(--accent-bg);
  border-radius:var(--radius-sm);padding:1px 8px}
.section-heading h2{font-size:22px;font-weight:700;line-height:1.3}
.section-note{font-size:14px;color:var(--muted)}
.subhead{display:flex;align-items:baseline;flex-wrap:wrap;gap:var(--space-2) var(--space-3);margin:var(--space-4) 0 0;
  font-size:15px;font-weight:600}
.subhead span{font-size:12px;color:var(--muted);font-weight:400}
.na{color:var(--muted);font-style:italic}
.empty{background:var(--surface);border:1px dashed var(--line);border-left:4px solid var(--muted);border-radius:var(--radius-sm);
  padding:var(--space-4) var(--space-5);font-size:14px;color:var(--muted)}
.empty b{color:var(--text)}
.empty-detail{display:block;font-size:13px;margin-top:var(--space-1)}

/* ---- tables ---- */
.table-wrap{overflow:auto;max-width:100%;max-height:72vh;background:var(--surface);border:1px solid var(--line);
  border-radius:var(--radius-sm);margin-top:var(--space-3)}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:14px}
caption{caption-side:top;text-align:left;font-size:13px;color:var(--muted);padding:var(--space-2) var(--space-3)}
th,td{padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--line);text-align:left;vertical-align:top;
  overflow-wrap:anywhere}
th{position:sticky;top:0;z-index:1;background:var(--surface-2);color:var(--muted);font-size:12px;font-weight:600;letter-spacing:.3px;
  white-space:nowrap;border-bottom:2px solid var(--line)}
tbody tr:nth-child(even) td{background:color-mix(in srgb,var(--surface-2) 55%,var(--surface))}
tbody tr:hover td{background:var(--accent-bg)}
tbody tr:last-child td{border-bottom:0}
td.r,th.r{text-align:right}
td.c,th.c{text-align:center}
td small{color:var(--muted);margin-left:2px}
td.note{color:var(--muted);font-size:13px}
.table-wrap table td:not(.note){white-space:normal}

/* ---- official / measured ---- */
.ov{white-space:nowrap;font-variant-numeric:tabular-nums}
.ov-o{color:var(--muted)}
.ov-sep{color:var(--muted);padding:0 2px}
.ov-m{color:var(--accent);font-weight:700}
.ov small{font-size:12px;font-weight:400;color:var(--muted);margin-left:2px}
.dlt{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:600;line-height:1.5;border-radius:999px;
  padding:1px 10px;white-space:nowrap;background:var(--surface-2);color:var(--muted);
  border:1px solid color-mix(in srgb,currentColor 35%,transparent)}
.dlt.ok{background:var(--ok-bg);color:var(--ok)}
.dlt.warn{background:var(--warn-bg);color:var(--warn)}
.dlt.bad{background:var(--bad-bg);color:var(--bad)}
.dlt.unknown{background:var(--surface-2);color:var(--muted)}
.barpair{display:block;min-width:96px;width:100%}
.barpair-track{display:flex;flex-direction:column;gap:3px;padding:3px 0}
.barpair-track i{display:block;height:6px;min-width:2px;border-radius:3px}
.barpair-o{background:color-mix(in srgb,var(--muted) 60%,var(--surface))}
.barpair-m{background:var(--accent)}

/* ---- pills, details ---- */
.pill{display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;line-height:1.5;
  padding:1px 10px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.pill.pass{color:var(--ok);background:var(--ok-bg)}
.pill.partial{color:var(--warn);background:var(--warn-bg)}
.pill.fail{color:var(--bad);background:var(--bad-bg)}
.pill.skip{color:var(--muted);background:var(--surface-2);border-color:var(--line)}
details>summary{position:relative;cursor:pointer;list-style:none;padding-left:30px;min-height:36px}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"+";position:absolute;left:8px;top:50%;transform:translateY(-50%);width:18px;height:18px;
  line-height:16px;text-align:center;font:600 14px/16px var(--mono);color:var(--accent);border:1px solid var(--line);
  border-radius:4px;background:var(--surface)}
details[open]>summary::before{content:"\2212"}
details>summary:hover{background:var(--surface-2)}
pre{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius-sm);padding:var(--space-2) var(--space-3);
  font-size:12.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word;margin:0;max-height:260px;overflow:auto;
  color:var(--text)}

/* ---- legacy section building blocks (themed; section CSS_EXTRA may override) ---- */
.core-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:var(--space-3)}
.core{background:var(--surface);border:1px solid var(--line);border-top:4px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:var(--space-4)}
.core.pass{border-top-color:var(--ok)}
.core.partial{border-top-color:var(--warn)}
.core.fail{border-top-color:var(--bad)}
.core-title{font-size:16px;font-weight:600;line-height:1.35}
.core-en{font-size:12px;color:var(--muted);margin-top:2px}
.core-value{font-size:34px;font-weight:700;letter-spacing:-.8px;margin:var(--space-2) 0 var(--space-1);line-height:1.15;white-space:nowrap}
.core-value small{font-size:16px;margin-left:4px;font-weight:400}
.core.fail .core-value{color:var(--bad)}
.core.partial .core-value{color:var(--warn)}
.core-status{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600}
.core.pass .core-status{color:var(--ok)}
.core.partial .core-status{color:var(--warn)}
.core.fail .core-status{color:var(--bad)}
.dot{width:8px;height:8px;border-radius:50%;background:currentColor;display:inline-block;flex:none}
.core-mini{display:flex;flex-wrap:wrap;gap:3px 10px;font-size:12px;color:var(--muted);margin-top:var(--space-2)}
.core-mini b{color:var(--text);font-weight:600}
.core-target,.core-delta{font-size:12px;color:var(--muted);line-height:1.55}
.core-target{border-top:1px solid var(--line);padding-top:var(--space-2);margin-top:var(--space-2)}
.data-grid,.totals{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--space-3);margin-top:var(--space-3)}
.metric,.total{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-sm);padding:var(--space-3) var(--space-4)}
.total{background:var(--surface-2)}
.metric-label{font-size:12px;color:var(--muted)}
.metric-value{font-size:22px;font-weight:700;letter-spacing:-.3px;margin-top:var(--space-1);line-height:1.2;overflow-wrap:anywhere}
.metric-value small{font-size:12px;font-weight:400;color:var(--muted);margin-left:3px}
.metric-name{font-weight:600}
.metric-en{font-size:12px;color:var(--muted);margin-left:6px}
.bars,.chart,.notes-box,.spec-list,.matrix,.rounds,.ttl-card{background:var(--surface);border:1px solid var(--line);
  border-radius:var(--radius-sm)}
.bars{padding:var(--space-2) var(--space-4);margin-top:var(--space-3)}
.bar-row{display:grid;grid-template-columns:minmax(110px,160px) minmax(0,1fr) minmax(70px,100px);gap:var(--space-3);
  align-items:center;padding:5px 0}
.bar-label{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.bar-track,.cbar-track{height:10px;background:var(--surface-2);border-radius:5px;overflow:hidden;position:relative}
.bar-fill{height:100%;background:var(--accent);border-radius:5px}
.bar-fill.warn{background:var(--bad)}
.bar-value,.cbar-val{font-size:13px;text-align:right;font-weight:600}
.baseline-layout{display:grid;grid-template-columns:minmax(0,1.06fr) minmax(0,.94fr);gap:var(--space-5);align-items:start}
.baseline-note{font-size:13px;color:var(--muted);margin:0 0 var(--space-2)}
.chart{padding:var(--space-4)}
.chart-title{font-size:15px;font-weight:600}
.chart-sub{font-size:12px;color:var(--muted);margin:2px 0 var(--space-3)}
.cbar-row{display:grid;grid-template-columns:minmax(80px,130px) minmax(0,1fr) 64px;gap:var(--space-2);align-items:center;padding:4px 0}
.cbar-label{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.cbar-track{overflow:visible}
.cbar-fill{height:100%;background:var(--ok);border-radius:5px}
.cbar-fill.bad{background:var(--bad)}
.cbar-fill.na{background:var(--muted)}
.cbar-marker{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--text)}
.chart-legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--muted);margin-top:var(--space-3);
  border-top:1px solid var(--line);padding-top:var(--space-2)}
.chart-evidence,.note-line{font-size:13px;color:var(--muted);line-height:1.65;margin-top:var(--space-3)}
.chart-evidence b,.note-line b{color:var(--text);font-weight:600}
.spec-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) 1.3fr;gap:var(--space-2);margin-bottom:var(--space-3)}
.spec-overview>div{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-sm);padding:var(--space-2) var(--space-3);
  display:flex;align-items:baseline;justify-content:space-between;gap:var(--space-2)}
.spec-overview span{font-size:12px;color:var(--muted)}
.spec-overview b{font-size:20px}
.spec-overview .spec-score-cell{background:var(--accent-bg)}
.spec-list{overflow:hidden}
.spec-item{border-bottom:1px solid var(--line)}
.spec-item:last-child{border-bottom:0}
.spec-item summary{display:grid;grid-template-columns:34px minmax(0,1fr) 84px 92px;gap:var(--space-3);align-items:center;
  padding:var(--space-3) var(--space-4) var(--space-3) 36px}
.spec-idx{font-family:var(--mono);font-size:12px;color:var(--muted)}
.spec-main{min-width:0;display:flex;align-items:baseline;gap:var(--space-3);flex-wrap:wrap}
.spec-title{font-size:15px;font-weight:600}
.spec-note{font-size:13px;color:var(--muted);min-width:0;overflow-wrap:anywhere}
.spec-weight{font-size:12px;color:var(--muted);text-align:right;white-space:nowrap}
.spec-body{padding:var(--space-3) var(--space-4) var(--space-4) 36px;background:var(--surface-2);border-top:1px solid var(--line)}
.kv-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--space-3);margin-top:var(--space-2)}
.kv h4,.evidence h4{font-size:12px;letter-spacing:.3px;color:var(--muted);margin-bottom:var(--space-1)}
.evidence{margin-top:var(--space-3)}
.evidence ul{font-size:13px;color:var(--muted);line-height:1.65}
.ttl-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-4);margin-top:var(--space-3)}
.ttl-card{border-top:4px solid var(--ok);padding:var(--space-3) var(--space-4)}
.ttl-card.na{border-top-color:var(--muted)}
.ttl-head{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3)}
.ttl-head h3{font-size:15px;font-weight:600}
.dl{display:grid;grid-template-columns:minmax(90px,130px) minmax(0,1fr);gap:var(--space-1) var(--space-3);margin-top:var(--space-2);font-size:13px}
.dl dt{color:var(--muted)}
.dl dd{font-weight:600;overflow-wrap:anywhere}
.rounds,.matrix{margin-top:var(--space-3);overflow:hidden}
.rounds summary,.matrix summary{padding:var(--space-2) var(--space-3) var(--space-2) 36px;font-size:14px}
.matrix summary{font-weight:600}
.rounds .table-wrap,.matrix .table-wrap{border:0;border-top:1px solid var(--line);border-radius:0;margin-top:0}
.verdict-banner{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:var(--radius-sm);
  padding:var(--space-4) var(--space-5);display:flex;justify-content:space-between;gap:var(--space-5);align-items:center;flex-wrap:wrap}
.verdict-banner.red{border-left-color:var(--bad)}
.verdict-banner.amber{border-left-color:var(--warn)}
.verdict-score{font-size:36px;font-weight:700;letter-spacing:-1px;line-height:1.1}
.verdict-score small{font-size:14px;color:var(--muted);letter-spacing:0;margin-left:4px;font-weight:400}
.verdict-word{font-size:16px;font-weight:600;margin-top:var(--space-1)}
.verdict-word .zh{font-size:13px;color:var(--muted);font-weight:400;margin-left:var(--space-2)}
.verdict-meta{font-size:13px;color:var(--muted);text-align:right;line-height:1.75}
.verdict-meta b{color:var(--text);font-weight:600}
.verdict-meta .alert{color:var(--bad)}
.mini-bar{height:6px;background:var(--surface-2);width:58px;display:inline-block;vertical-align:middle;margin-left:5px;border-radius:3px;overflow:hidden}
.mini-bar span{display:block;height:100%;background:var(--accent)}
.notes-box{margin-top:var(--space-3);padding:var(--space-3) var(--space-4);font-size:13px;color:var(--muted);line-height:1.7}
.notes-box b{color:var(--text);font-weight:600}

/* ---- footer ---- */
.footer{display:flex;justify-content:space-between;gap:var(--space-4);align-items:center;flex-wrap:wrap;
  border-top:1px solid var(--line);padding-top:var(--space-3);margin-top:var(--space-6);font-size:12px;color:var(--muted)}
.footer .right{white-space:nowrap}

/* ---- responsive ---- */
@media (max-width:1280px){
  .core-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .data-grid,.totals{grid-template-columns:repeat(2,minmax(0,1fr))}
  .baseline-layout{grid-template-columns:1fr}
  .kv-grid{grid-template-columns:1fr}
  .spec-overview{grid-template-columns:repeat(2,minmax(0,1fr)) 1.3fr}
}
@media (max-width:1100px){
  .layout{grid-template-columns:minmax(0,1fr);gap:0;padding:var(--space-4)}
  .toc{position:sticky;top:var(--mast-h);z-index:40;margin:0 calc(var(--space-4) * -1) var(--space-4);padding:var(--space-2) var(--space-4);
    background:var(--bg);border-bottom:1px solid var(--line)}
  .toc-title{display:none}
  .toc ol{flex-direction:row;overflow-x:auto;gap:var(--space-2);-webkit-overflow-scrolling:touch;scrollbar-width:thin}
  .toc a{flex:none;white-space:nowrap;background:var(--surface);border:1px solid var(--line);border-radius:999px;
    border-left-width:1px;padding:4px 14px}
  .hero{grid-template-columns:1fr}
  .stats{grid-template-columns:repeat(3,minmax(0,1fr))}
  .section-heading,.hero{scroll-margin-top:calc(var(--mast-h) + 44px)}
  .mast-label{display:none}
}
@media (max-width:760px){
  body{font-size:15px}
  .mast-inner{padding:0 var(--space-3);gap:var(--space-2)}
  .model-chip{display:none}
  .layout{padding:var(--space-3)}
  .wordmark{font-size:14px;flex:1}
  .mast-spacer{display:none}
  .theme-label{flex:none;padding:4px 10px}
  .toc{margin:0 calc(var(--space-3) * -1) var(--space-3);padding:var(--space-2) var(--space-3)}
  .hero h1{font-size:24px}
  .hero-card{padding:var(--space-4)}
  .stats{grid-template-columns:1fr}
  .stat-value{font-size:36px}
  .core-grid,.data-grid,.totals,.ttl-grid{grid-template-columns:1fr}
  .spec-item summary{grid-template-columns:30px minmax(0,1fr) auto}
  .spec-weight{display:none}
  .spec-body{padding-left:var(--space-4)}
  .bar-row,.cbar-row{grid-template-columns:minmax(80px,110px) minmax(0,1fr) auto}
  .section-heading h2{font-size:19px}
  .verdict-meta{text-align:left}
  .footer{flex-direction:column;align-items:flex-start}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{animation:none!important;transition:none!important}
}
"""

_CSS_PRINT = r"""
@page{size:A4;margin:14mm 12mm}
.bm-table,.sc-quant,.check-table{min-width:42rem}
.bm-table th:first-child,.bm-table td:first-child,.sc-quant th:first-child,.sc-quant td:first-child,
.check-table th:first-child,.check-table td:first-child{min-width:7.5em}
.bm-table th:first-child,.bm-table td:first-child{min-width:11em}
.bm-table th:nth-child(2),.bm-table td:nth-child(2){min-width:9em;white-space:nowrap}
.bm-table td.c,.bm-table td.r{white-space:nowrap}
.barpair i,.pill,.dlt,.fr-fill{-webkit-print-color-adjust:exact;print-color-adjust:exact}
@media print{
  html,body{background:#fff!important;color:#000!important;font-size:11pt}
  .masthead,.toc,.theme-label,.theme-toggle,.skip-link{display:none!important}
  .layout{display:block;padding:0;max-width:none}
  .hero,.stats{display:block}
  .hero-card,.stat,.core,.metric,.total,.chart,.bars,.notes-box,.spec-item,.ttl-card,.matrix,.rounds,.verdict-banner,.empty{
    box-shadow:none!important;background:#fff!important;break-inside:avoid;page-break-inside:avoid}
  .hero-card,.stat{margin-bottom:8px}
  .section-heading{break-after:avoid;page-break-after:avoid}
  tr,figure,.barpair{break-inside:avoid;page-break-inside:avoid}
  thead{display:table-header-group}
  .table-wrap,pre{overflow:visible!important;max-height:none!important}
  th{position:static;background:#eee!important;color:#000!important}
  tbody tr:nth-child(even) td,tbody tr:hover td{background:#fff!important}
  .core-grid,.data-grid,.totals,.ttl-grid,.baseline-layout,.kv-grid{display:block}
  .core,.metric,.total,.ttl-card,.chart{margin-bottom:8px}
  .spec-overview{display:flex;flex-wrap:wrap}
  .spec-item summary{display:block;padding-left:0}
  details>summary::before{display:none}
  details>summary{padding-left:0;background:none!important}
  details>*{display:block!important}
  details:not([open])>summary~*{display:block!important}
  a{color:inherit;text-decoration:none}
  .stat-value{font-size:26pt}
}
@media print{
  details::details-content{content-visibility:visible!important;display:block!important;height:auto!important}
}
@media print{
  :root,:root:not(:has(#theme-toggle:checked)),:root:has(#theme-toggle:checked){__LIGHT__}
}
"""

CARBON = r"""
/* ---- carbon: reference-report look (square, thin numerals, 1px grid lines, black masthead) ---- */
.masthead{background:#161616;border:0;border-top:4px solid #0f62fe;box-shadow:none}
.mast-inner{max-width:1760px;min-height:64px}
.wordmark{color:#fff;font-weight:400;font-size:19px}
.mast-label{color:#a8a8a8;letter-spacing:2px;text-transform:uppercase}
.model-chip{background:#262626;border-color:#393939;color:#f4f4f4;border-radius:0}
.theme-label{background:#262626;border-color:#393939;color:#f4f4f4;border-radius:0}
.theme-label:hover{background:#393939}
.layout{max-width:1760px;grid-template-columns:minmax(0,1fr);gap:0;padding:40px 48px 28px}
.toc{position:static;margin:0 0 var(--space-5)}
.toc-title{display:none}
.toc ol{flex-direction:row;flex-wrap:wrap;gap:0;border-bottom:1px solid var(--line)}
.toc a{border-left:0;border-bottom:2px solid transparent;padding:8px 16px;border-radius:0}
.toc a:hover{background:transparent;border-bottom-color:var(--accent)}
.hero{grid-template-columns:minmax(0,1fr) 480px;gap:48px;margin-bottom:var(--space-5)}
.hero-card{background:transparent;border:0;padding:0;gap:var(--space-2)}
.hero h1{font-size:44px;font-weight:300;letter-spacing:-.6px}
.chip{border-radius:0;background:var(--surface);border-color:var(--line)}
.vbadge{border-radius:0}
.stats{grid-template-columns:1fr 1fr;gap:0;background:var(--surface);border-top:3px solid var(--accent);align-self:start}
.stat{border:0;border-right:1px solid var(--line);border-bottom:1px solid var(--line);border-left:3px solid transparent;
  display:block;padding:var(--space-4) var(--space-4) var(--space-3)}
.stat-value{display:block;grid-row:auto;text-align:left;font-size:46px;font-weight:300;letter-spacing:-1px;margin:6px 0 4px}
.stat-label{font-size:15px;font-weight:400;color:var(--muted)}
.profile{display:flex;flex-wrap:wrap;gap:8px 26px;padding:0 0 var(--space-4);font-size:15px;color:var(--muted)}
.profile b{font-weight:500;color:var(--text);margin-left:5px;overflow-wrap:anywhere}
.section-heading{border-bottom:0;margin:var(--space-6) 0 var(--space-3)}
.section-index{background:transparent;color:var(--muted);padding:0;font-weight:400;letter-spacing:1px}
.section-heading h2{font-size:25px;font-weight:400}
.core-grid{gap:1px;grid-template-columns:repeat(5,minmax(0,1fr));background:var(--line);border:1px solid var(--line)}
.core{border:0;border-top:3px solid transparent;padding:22px 24px 18px;min-height:0}
.core.pass{border-top-color:transparent}
.core.fail{border-top-color:var(--bad)}
.core-title{font-size:18px;font-weight:500}
.core-en{font-size:12px;color:var(--muted)}
.core-value{font-weight:300;font-size:46px;letter-spacing:-1.2px;margin:14px 0 10px}
.core-value small{font-size:20px}
.core-status{font-size:14px}
.core-target,.core-delta{font-size:13px}
.core-target{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
.data-grid{grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin-top:0}
.metric{border:0;padding:18px 22px 14px}
.metric-label{font-size:14px;color:var(--muted)}
.metric-value{font-size:28px;font-weight:500;letter-spacing:-.5px;margin-top:8px}
.metric-value small,.core-value small,.stat-value small{white-space:nowrap}
.stat:last-child:nth-child(odd){grid-column:1/-1}
.core-title{font-size:16px}
.totals{grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:#525252;margin-top:16px}
.total{background:#262626;border:0;color:#fff;padding:20px 24px}
.total .metric-label{color:#c6c6c6}
.total .metric-value{font-weight:300;font-size:36px;color:#fff}
.table-wrap,.bars,.chart,.notes-box,.spec-list,.matrix,.rounds,.ttl-card,.kv-grid>*{border-radius:0}
.notes-box{background:var(--accent-bg);border:0;border-left:3px solid var(--accent);color:var(--text)}
.pill{border-radius:0}
.table-wrap thead th{background:var(--surface-2);font-weight:600}
@media (min-width:1280px){.core-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
@media (max-width:1279px){.core-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .hero{grid-template-columns:1fr}}
@media (max-width:760px){.core-grid,.data-grid,.totals,.stats{grid-template-columns:1fr}.layout{padding:16px}.hero h1{font-size:30px}}
"""

HIERARCHY = r"""
/* ---- hierarchy: findings first, secondary sections folded, calm items compacted ---- */
.findings{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);padding:16px 24px;margin:0 0 var(--space-5)}
.findings-title{margin:0 0 8px;font-size:16px;font-weight:600}
.findings ol{list-style:none;margin:0;padding:0;display:grid;gap:4px}
.findings li a{display:flex;gap:12px;align-items:baseline;color:inherit;text-decoration:none;padding:4px 0}
.findings li a:hover span:last-child{text-decoration:underline}
.findings li a .pill{flex:none}
.findings .ok,.findings .more{color:var(--muted)}
.fold{border-top:1px solid var(--line);margin-top:var(--space-4)}
.fold>summary{display:flex;align-items:baseline;gap:12px;cursor:pointer;list-style:none;padding:14px 0}
.fold>summary::-webkit-details-marker{display:none}
.fold>summary::before{content:"▸";color:var(--muted);flex:none}
.fold[open]>summary::before{content:"▾"}
.fold>summary,.sp-passed>summary{padding-left:0}
.sp-passed>summary{display:flex;gap:.5rem;padding:10px 16px}
.sp-passed>summary::before{content:"▸"}
.sp-passed[open]>summary::before{content:"▾"}
.sp-item>summary::before,.sp-matrix>summary::before,.bl-rounds>summary::before,.au-raw>summary::before,.fold>summary::before,.sp-passed>summary::before{position:static;transform:none;left:auto;top:auto;width:auto;height:auto;background:none;border:0;border-radius:0;color:var(--muted)}
.fold>summary .section-index,.sp-idx{flex:none;min-width:2ch;white-space:nowrap}
.au-raw>summary{white-space:normal;min-width:0}
.fold>summary h2{margin:0;font-size:20px;font-weight:400}
.fold>summary .section-note{margin-left:auto;color:var(--muted);font-size:14px;text-align:right}
.fold>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.fold-body{padding-bottom:var(--space-4)}
.section-heading{margin:var(--space-5) 0 var(--space-3)}
.kpi-grid{grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}
.kpi{border:0;border-top:3px solid transparent;padding:18px 20px;gap:8px}
.kpi.pass{border-top-color:var(--ok)}
.kpi.partial{border-top-color:var(--warn)}
.kpi.fail{border-top-color:var(--bad)}
.kpi-title{font-size:15px;font-weight:500}
.kpi-value{font-weight:300;font-size:40px;letter-spacing:-1px}
.kpi-value small{white-space:nowrap}
.kpi-ovline{border-top:0;padding-top:0;font-size:14px;color:var(--muted)}
.sp-counts{display:flex;flex-wrap:wrap;gap:6px 24px;margin:0 0 var(--space-3)}
.sp-count{display:inline-flex;flex-direction:row;align-items:baseline;gap:6px;padding:0;background:transparent;border:0;color:var(--muted)}
.sp-count.partial,.sp-count.fail{background:transparent}
.sp-count-num{font-size:20px;font-weight:500}
.sp-passed{margin-top:var(--space-3);background:var(--surface);border:1px solid var(--line)}
.sp-passed>summary{padding:10px 16px;cursor:pointer;color:var(--muted)}
.sp-passed ul{list-style:none;margin:0;padding:0 16px 12px;display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:4px 24px}
.sp-passed li{display:flex;gap:10px;align-items:baseline;min-width:0}
.sp-passed .sp-title{font-weight:400;min-width:0}
.sp-passed .pill{margin-left:auto;flex:none}
.sp-body .au-raw{margin-top:0}
.fr-stack{display:flex;height:12px;background:var(--surface-2);overflow:hidden}
.fr-seg.s-stop,.fr-dot.s-stop{background:var(--ok)}
.fr-seg.s-length,.fr-dot.s-length{background:var(--warn)}
.fr-seg.s-tool_calls,.fr-dot.s-tool_calls{background:var(--accent)}
.fr-seg.s-other,.fr-dot.s-other{background:var(--muted)}
.fr-seg.s-empty,.fr-dot.s-empty{background:var(--bad)}
.fr-legend{display:flex;flex-wrap:wrap;gap:6px 20px;margin-top:8px;font-size:14px}
.fr-dot{display:inline-block;width:10px;height:10px;margin-right:6px}
.au-raws h4{margin:12px 12px 4px;font-size:.9rem;color:var(--muted)}
.bl-rounds{margin-top:var(--space-3)}
@media (max-width:1279px){.kpi-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:760px){.kpi-grid{grid-template-columns:1fr}.fold>summary{flex-wrap:wrap}.fold>summary .section-note{margin-left:0;text-align:left;flex-basis:100%}}
@media print{details.fold::details-content,.sp-passed::details-content,.bl-rounds::details-content{content-visibility:visible;display:block}}
"""

CSS = (
    ":root{" + _LIGHT_VARS + "}"
    + "@media (prefers-color-scheme: dark){:root:not(:has(#theme-toggle:checked)){" + _DARK_VARS + "}}"
    + "@media not (prefers-color-scheme: dark){:root:has(#theme-toggle:checked){" + _DARK_VARS + "}}"
    + _CSS_BODY
    + CARBON
    + _CSS_PRINT.replace("__LIGHT__", _LIGHT_VARS)
)


# --------------------------------------------------------------------------- shell

# Section ids are generated by html_components.section("0X", ...); titles are fixed Chinese labels.
NAV = (
    ("01", "核心标准"),
    ("02", "规范符合度"),
    ("03", "全部指标"),
    ("04", "官方基准对标"),
    ("05", "计费与缓存"),
    ("06", "脚本得分与渠道"),
)

_VCLS_TO_STAT = {"green": "green", "amber": "amber", "red": "red", "": ""}


def render_theme_toggle_input():
    """Hidden checkbox; must be the first element of <body> so ``:root:has()`` and focus styling work."""
    return ('<input type="checkbox" id="theme-toggle" class="theme-toggle" '
            'aria-label="切换深色 / 浅色主题">')


def render_masthead(audit, spec_doc, title, profile=None):
    model = dig(spec_doc, "model") or dig(audit, "model") or ""
    chip = f'<span class="model-chip" title="{esc_raw(model)}">{esc_raw(model)}</span>' if model else ""
    return f"""
<a class="skip-link" href="#main">跳到正文</a>
<header class="masthead">
  <div class="mast-inner">
    <a class="wordmark" href="#top">{lab("Conformance Report")}</a>
    <span class="mast-label">{lab("MODEL CONFORMANCE ASSESSMENT")}</span>
    <span class="mast-spacer"></span>
    {chip}
    <label class="theme-label" for="theme-toggle" title="切换深色 / 浅色主题"><span class="ti" aria-hidden="true">&#9680;</span>深 / 浅色</label>
  </div>
</header>"""


def render_nav():
    links = "".join(
        f'<li><a href="#sec-{esc_raw(i)}"><span class="n">{esc_raw(i)}</span>{esc_raw(t)}</a></li>'
        for i, t in NAV
    )
    return (f'<nav class="toc" aria-label="章节导航"><div class="toc-title">目录</div>'
            f'<ol>{links}</ol></nav>')


def _bench_row_count(spec_doc, profile):
    """Number of benchmark rows carrying an official or measured value; None when unavailable."""
    if spec_doc is None or not isinstance(profile, dict):
        return None
    rows = build_rows(spec_doc, profile)
    n = sum(1 for r in rows if r.get("official") is not None or r.get("measured") is not None)
    return n or None


def _ratio_cls(n, total):
    if not total:
        return ""
    if n >= total:
        return "green"
    return "red" if n == 0 else "amber"


def _stat(label, value_html, caption, cls=""):
    return (
        f'<div class="stat {cls}"><div class="stat-label">{label}</div>'
        f'<div class="stat-value">{value_html}</div>'
        f'<div class="stat-caption {cls}">{caption}</div></div>'
    )


def render_hero(audit, spec_doc, title, profile=None):
    model = dig(spec_doc, "model") or dig(audit, "model") or "未测"
    host = dig(spec_doc, "host") or "未测"
    tested_at = dig(spec_doc, "tested_at") or "未测"
    score = audit.get("score")
    verdict = audit.get("verdict") or "未测"
    vcls = verdict_class(audit.get("verdict"))

    items = items_of(spec_doc)
    pass_n = sum(1 for it in items if it.get("status") == "pass")
    partial_n = sum(1 for it in items if it.get("status") == "partial")
    spec_score = dig(spec_doc, "spec", "score")
    if spec_doc is None or not items:
        spec_value, spec_cap, spec_cls = "—", NO_SPEC_TEXT, ""
    else:
        spec_value = f'{fmt(pass_n)}<small>/ {fmt(len(items))} 项</small>'
        spec_cap = f"规范符合分 {fmt(spec_score)} / 100 · {fmt(partial_n)} 项部分通过"
        spec_cls = _ratio_cls(pass_n, len(items))

    b_n = _bench_row_count(spec_doc, profile)
    if b_n is None:
        if spec_doc is None:
            b_cap = NO_SPEC_TEXT
        elif not isinstance(profile, dict):
            b_cap = "官方基准：未提供（未指定 --profile）"
        else:
            b_cap = "无可判定的基准项"
        b_value, b_cls = "—", ""
    else:
        b_value = f'{fmt(b_n)}<small>项</small>'
        b_cap = "官方 / 实测 对照，仅展示数值"
        b_cls = ""

    auth_value = (f'{fmt(score)}<small>/ 100</small>' if is_num(score) else "未测")
    auth_cap = (f'{lab(verdict)} · {lab("model_audit")} v{esc(dig(audit, "scoring_version") or "2")}')

    return f"""
<section class="hero" id="top" aria-labelledby="report-title">
  <div class="hero-card">
    <h1 id="report-title">{esc_raw(title)}</h1>
    <div class="verdict-line"><span class="vbadge {vcls}">结论：{lab(verdict)}</span></div>
    <div class="model-line">
      <span class="model">{esc_raw(model)}</span><span class="divider"></span>
      <span>{esc_raw(host)}</span><span class="divider"></span>
      <span>{esc_raw(tested_at)}</span>
    </div>
  </div>
  <div class="stats">
    {_stat("脚本得分", auth_value, auth_cap, _VCLS_TO_STAT.get(vcls, ""))}
    {_stat("规范符合分", spec_value, spec_cap, spec_cls)}
    {_stat("官方基准对标", b_value, b_cap, b_cls)}
  </div>
</section>"""


def render_footer(spec_doc, profile=None):
    host = dig(spec_doc, "host") or "未测"
    src = baseline_source(profile) if isinstance(profile, dict) else None
    base = f"官方基准：{esc_raw(src)}（存档）" if src else "官方基准：未提供"
    return (
        '<footer class="footer">'
        f'<span>数据来源：{esc_raw(host)} 实测 · {base}</span>'
        f'<span class="right">{lab("Single-page report")}</span>'
        '</footer>'
    )
