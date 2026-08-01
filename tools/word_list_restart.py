"""Restart Word's built-in List Number without replacing its abstract definition."""

from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def new_number_list(doc) -> int:
    base_num_id = int(doc.styles["List Number"]._element.pPr.numPr.numId.val)
    numbering = doc.part.numbering_part.element
    base = next(
        item
        for item in numbering.findall(qn("w:num"))
        if int(item.get(qn("w:numId"))) == base_num_id
    )
    abstract_id = int(base.find(qn("w:abstractNumId")).get(qn("w:val")))
    num_id = max(int(item.get(qn("w:numId"))) for item in numbering.findall(qn("w:num"))) + 1
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    ref = OxmlElement("w:abstractNumId")
    ref.set(qn("w:val"), str(abstract_id))
    num.append(ref)
    override = OxmlElement("w:lvlOverride")
    override.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:startOverride")
    start.set(qn("w:val"), "1")
    override.append(start)
    num.append(override)
    numbering.append(num)
    return num_id


def apply_num_id(paragraph, num_id: int) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "0")
    num = OxmlElement("w:numId")
    num.set(qn("w:val"), str(num_id))
    num_pr.extend((ilvl, num))
    ppr.append(num_pr)
