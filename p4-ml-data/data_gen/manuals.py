"""Mock equipment manuals as PDFs for the AI's search (ML-6), plus corpus/manuals/index.json.

MAN-SPINDLE-200 is a full ~70-page manual so that the search fixture (fixtures/story/docsearch_bearing.json)
is literally true: section 7.4 starts on page 58 and 7.6 on page 61, with the fixture's sentences in them.
Page numbers in the footer are the physical PDF page numbers.

A copy of the press manual with a hidden instruction goes to fixtures/security/ (security demo only).
"""
from __future__ import annotations

import io
import json
import re

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, ListFlowable, ListItem, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

from .manual_text import MANUALS, POISON
from .plant import P4, REPO, load_assets

rl_config.invariant = 1          # no timestamps/ids in the PDF: same input -> byte-identical file
OUT = P4 / "corpus" / "manuals"
SECURITY = REPO / "fixtures" / "security"

_ss = getSampleStyleSheet()
ST = {
    "body": ParagraphStyle("body", parent=_ss["BodyText"], fontName="Helvetica", fontSize=10, leading=14,
                           spaceAfter=5),
    "h1": ParagraphStyle("h1", parent=_ss["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20,
                         spaceBefore=4, spaceAfter=10, textColor=colors.HexColor("#1D3557")),
    "h2": ParagraphStyle("h2", parent=_ss["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15,
                         spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#1D3557")),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=12),
    "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=9, leading=12),
    "toc": ParagraphStyle("toc", fontName="Helvetica", fontSize=9, leading=12),
    "cover": ParagraphStyle("cover", fontName="Helvetica-Bold", fontSize=26, leading=32, spaceAfter=14,
                            textColor=colors.HexColor("#1D3557")),
    "coversub": ParagraphStyle("coversub", fontName="Helvetica", fontSize=13, leading=18, spaceAfter=6),
}


class _Doc(BaseDocTemplate):
    def __init__(self, target, man):
        super().__init__(target, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm,
                         bottomMargin=20 * mm, title=man["title"], author=man["maker"], subject=man["doc_id"])
        self.man = man
        self.marks: dict[str, int] = {}
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=self._decor)])

    def _decor(self, canv, doc):
        n = canv.getPageNumber()
        if n == 1:
            return
        canv.saveState()
        canv.setFont("Helvetica", 7.5)
        canv.setFillColor(colors.HexColor("#5B6770"))
        canv.drawString(20 * mm, A4[1] - 13 * mm, f"{self.man['title']}")
        canv.drawRightString(A4[0] - 20 * mm, A4[1] - 13 * mm, f"{self.man['doc_id']} · {self.man['rev']}")
        canv.line(20 * mm, A4[1] - 15 * mm, A4[0] - 20 * mm, A4[1] - 15 * mm)
        canv.drawString(20 * mm, 11 * mm, self.man["maker"])
        canv.drawRightString(A4[0] - 20 * mm, 11 * mm, f"Page {n}")
        canv.restoreState()

    def afterFlowable(self, f):
        key = getattr(f, "_mark", None)
        if key and key not in self.marks:
            self.marks[key] = self.page


def _p(text, style="body"):
    return Paragraph(text, ST[style])


def _table(rows, widths=None, header=True):
    data = [[_p(str(c), "cellb" if (header and i == 0) else "cell") for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E3E8EF")))
    t.setStyle(TableStyle(style))
    return t


def _box(label, text, color):
    t = Table([[_p(f"<b>{label}</b>", "cell"), _p(text, "cell")]], colWidths=[22 * mm, None], hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(color)),
                           ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#5B6770")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _notes_page():
    rows = [[""] for _ in range(34)]
    t = Table(rows, colWidths=[170 * mm], rowHeights=[6.6 * mm] * 34)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#C5CDD6"))]))
    return [PageBreak(), _p("Notes", "h2"), t]


def _flow(man, toc_pages, pads):
    out = []
    # cover
    out += [Spacer(1, 50 * mm), _p(man["title"], "cover"), _p(man.get("subtitle", ""), "coversub"),
            Spacer(1, 10 * mm), _p(f"Document {man['doc_id']} · {man['rev']}", "coversub"),
            _p(man["maker"], "coversub"), Spacer(1, 70 * mm),
            _p("Read this manual before installing, operating or servicing the equipment. Keep it at the machine.",
               "body")]
    toc = [(b[1], b[2]) for b in man["blocks"] if b[0] in ("h1", "h2")]
    if man.get("toc", True):
        out.append(PageBreak())
        out.append(_p("Contents", "h1"))
        rows = []
        for num, title in toc:
            pg = toc_pages.get(num, 0)
            indent = "&nbsp;" * (0 if "." not in num else 6)
            rows.append([_p(f"{indent}{num}&nbsp;&nbsp;{title}", "toc"), _p(str(pg) if pg else "", "toc")])
        t = Table(rows, colWidths=[150 * mm, 20 * mm], hAlign="LEFT")
        t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 0.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5)]))
        out.append(t)
    newpage_ch = set(man.get("h2_newpage", ()))         # chapters where every section starts on a new page
    chapter, first_in_chapter = None, True
    for b in man["blocks"]:
        kind = b[0]
        if kind == "h1":
            out.append(PageBreak())
            h = _p(f"{b[1]}&nbsp;&nbsp;{b[2]}", "h1")
            h._mark = b[1]
            out.append(h)
            chapter, first_in_chapter = b[1], True
        elif kind == "h2":
            if (len(b) > 3 and b[3] == "newpage") or (chapter in newpage_ch and not first_in_chapter):
                out.append(PageBreak())
            first_in_chapter = False
            h = _p(f"{b[1]}&nbsp;&nbsp;{b[2]}", "h2")
            h._mark = b[1]
            out.append(h)
        elif kind == "p":
            out.append(_p(b[1]))
        elif kind in ("ul", "ol"):
            items = [ListItem(_p(x), leftIndent=12) for x in b[1]]
            out.append(ListFlowable(items, bulletType="bullet" if kind == "ul" else "1", start=None if kind == "ul" else 1,
                                    leftIndent=14, bulletFontSize=8))
            out.append(Spacer(1, 4))
        elif kind == "table":
            out.append(_table(b[1], b[2] if len(b) > 2 else None))
            out.append(Spacer(1, 6))
        elif kind == "note":
            out += [_box("Note", b[1], "#EEF4FB"), Spacer(1, 6)]
        elif kind == "caution":
            out += [_box("Caution", b[1], "#FFF4E0"), Spacer(1, 6)]
        elif kind == "warning":
            out += [_box("Warning", b[1], "#FDECEA"), Spacer(1, 6)]
        elif kind == "keep":
            out.append(KeepTogether([_p(x) for x in b[1]]))
        elif kind == "pb":
            out.append(PageBreak())
        elif kind == "pad":
            for _ in range(pads.get(b[1], 0)):
                out += _notes_page()
    return out


def render(man: dict) -> tuple[bytes, dict]:
    """Build the PDF; insert 'Notes' pages where needed so pinned sections land on their pinned pages."""
    pins = man.get("pins", {})
    pads = {k: 0 for b in man["blocks"] if b[0] == "pad" for k in [b[1]]}
    marks: dict = {}
    for _ in range(8):
        for _toc in range(2):                                     # second pass fills the contents page numbers
            buf = io.BytesIO()
            doc = _Doc(buf, man)
            doc.build(_flow(man, marks, pads))
            marks = doc.marks
        off = {k: pins[k] - marks.get(k, 0) for k in pins}
        if all(v == 0 for v in off.values()):
            return buf.getvalue(), marks
        changed = False
        for key in pins:                                          # fix the earliest pin first
            if off[key] != 0:
                pad_keys = [b[1] for b in man["blocks"] if b[0] == "pad" and b[1].startswith(key + "@")]
                if off[key] < 0 or not pad_keys:
                    raise ValueError(f"{man['doc_id']}: section {key} is on page {marks.get(key)}, "
                                     f"pinned to {pins[key]} (content too long or no pad point)")
                for i in range(off[key]):
                    pads[pad_keys[i % len(pad_keys)]] += 1
                changed = True
                break
        if not changed:
            break
    raise ValueError(f"{man['doc_id']}: could not place pinned sections {pins} (got {marks})")


def write_all() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    assets = load_assets()
    index = []
    for man in MANUALS:
        pdf, marks = render(man)
        (OUT / man["file"]).write_bytes(pdf)
        applies = [a.id for a in assets if a.type in man["types"]]
        index.append({"doc_id": man["doc_id"], "title": man["title"], "file": man["file"], "revision": man["rev"],
                      "applies_to": applies, "pages": _count_pages(pdf),
                      "sections": [{"section": b[1], "title": b[2], "page": marks.get(b[1])}
                                   for b in man["blocks"] if b[0] in ("h1", "h2")]})
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # security demo copy (kept OUT of corpus/ so the AI search never indexes it by accident)
    SECURITY.mkdir(parents=True, exist_ok=True)
    src = next(m for m in MANUALS if m["doc_id"] == POISON["doc_id"])
    bad = dict(src, doc_id=src["doc_id"] + "-COPY", file=POISON["file"], pins={},
               blocks=[b for blk in src["blocks"] for b in ([blk, ("p", POISON["text"])] if blk[:2] == ("h2", POISON["after"]) else [blk])])
    pdf, _ = render(bad)
    (SECURITY / POISON["file"]).write_bytes(pdf)
    return index


def _count_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))
