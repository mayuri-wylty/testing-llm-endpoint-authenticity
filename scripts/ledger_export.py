"""台账 md -> csv / docx 导出（仅标准库）。md 是唯一数据源。"""
import csv
import os
import re
import time
import zipfile
from xml.sax.saxutils import escape


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, buf, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def parse_ledger(md_text):
    """Parse the ledger table (header starting 时间|站点; else the first table). Same table ledger.upsert edits."""
    tables, cur = [], None
    for line in md_text.lstrip("﻿").splitlines():
        if not line.strip().startswith("|"):
            cur = None
            continue
        if cur is None:
            cur = {"headers": _split_row(line), "rows": []}
            tables.append(cur)
            continue
        if _SEP.match(line):
            continue
        cells = _split_row(line)
        cells += [""] * (len(cur["headers"]) - len(cells))
        cur["rows"].append(cells)
    if not tables:
        return [], []
    t = next((t for t in tables if t["headers"][:2] == ["时间", "站点"]), tables[0])
    return t["headers"], t["rows"]


def _atomic(path, writer):
    tmp = path + ".tmp"
    try:
        writer(tmp)
        for n in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows: target held open (Excel/WPS) or briefly locked
                if n == 19:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _safe_cell(v):
    """Neutralise spreadsheet formula injection (= + - @ and control prefixes) so Google Sheets treats it as text."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def write_csv(headers, rows, path):
    def w(tmp):
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(headers)
            cw.writerows([_safe_cell(c) for c in r] for r in rows)

    _atomic(path, w)


_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_CONTENT_TYPES = (
    _HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
_RELS = (
    _HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_DOC_RELS = (
    _HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)
_STYLES = (
    _HEAD + f"<w:styles {_NS}>"
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体" w:cs="Calibri"/>'
    '<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/>'
    "</w:rPr></w:rPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:after="200"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>'
    "</w:styles>"
)


def _clean(s):
    return re.sub("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\ufffe\\uffff]", "", str(s))


def _para(text, bold=False, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    parts = _clean(text).split("\n")
    runs = "<w:br/>".join(
        f'<w:t xml:space="preserve">{escape(p, {chr(34): "&quot;"})}</w:t>' for p in parts
    )
    return f"<w:p>{ppr}<w:r>{rpr}{runs}</w:r></w:p>"


def _cell(text, width, bold=False, shade=False):
    tcpr = f'<w:tcW w:w="{width}" w:type="dxa"/>'
    if shade:
        tcpr += '<w:shd w:val="clear" w:color="auto" w:fill="E7E6E6"/>'
    return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{_para(text, bold)}</w:tc>"


def write_docx(headers, rows, path, title):
    ncol = max(len(headers), 1)
    page_w, margin = 16838, 720
    avail = page_w - 2 * margin
    cw = avail // ncol
    border = "".join(
        f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
        for s in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    tbl = (
        f'<w:tbl><w:tblPr><w:tblW w:w="{cw * ncol}" w:type="dxa"/>'
        f"<w:tblBorders>{border}</w:tblBorders><w:tblLayout w:type=\"fixed\"/></w:tblPr>"
        "<w:tblGrid>" + f'<w:gridCol w:w="{cw}"/>' * ncol + "</w:tblGrid>"
    )
    tbl += (
        "<w:tr><w:trPr><w:tblHeader/></w:trPr>"
        + "".join(_cell(h, cw, True, True) for h in headers)
        + "</w:tr>"
    )
    for r in rows:
        r = list(r) + [""] * (len(headers) - len(r))
        tbl += "<w:tr>" + "".join(_cell(c, cw) for c in r) + "</w:tr>"
    tbl += "</w:tbl>"
    sect = (
        '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
        f'<w:pgMar w:top="{margin}" w:right="{margin}" w:bottom="{margin}" w:left="{margin}" '
        'w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>'
    )
    doc = (
        _HEAD + f"<w:document {_NS}><w:body>"
        + _para(title, style="Title") + tbl + _para("") + sect
        + "</w:body></w:document>"
    )

    def w(tmp):
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _CONTENT_TYPES)
            z.writestr("_rels/.rels", _RELS)
            z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
            z.writestr("word/styles.xml", _STYLES)
            z.writestr("word/document.xml", doc)

    _atomic(path, w)
