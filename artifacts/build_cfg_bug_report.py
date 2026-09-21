from __future__ import annotations

from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


OUT = Path("artifacts/Configuration_Bug_Report_CFG07_CFG09.docx")


def text(value: str) -> str:
    return escape(value, {"\"": "&quot;"})


def run(value: str, bold: bool = False, size: int | None = None, color: str | None = None) -> str:
    props = []
    if bold:
        props.append("<w:b/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{text(value)}</w:t></w:r>"


def paragraph(value: str = "", style: str | None = None, bold_lead: str | None = None, spacing_after: int = 160) -> str:
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
    spacing = f'<w:spacing w:after="{spacing_after}" w:line="276" w:lineRule="auto"/>'
    ppr = f"<w:pPr>{style_xml}{spacing}</w:pPr>"
    if bold_lead and value.startswith(bold_lead):
        rest = value[len(bold_lead):]
        content = run(bold_lead, bold=True) + run(rest)
    else:
        content = run(value)
    return f"<w:p>{ppr}{content}</w:p>"


def bullet(value: str) -> str:
    return (
        "<w:p><w:pPr>"
        "<w:spacing w:after=\"80\" w:line=\"276\" w:lineRule=\"auto\"/>"
        "<w:ind w:left=\"360\"/></w:pPr>"
        f"{run('- ' + value)}</w:p>"
    )


def cell(content: str, width: int, fill: str | None = None, center: bool = False) -> str:
    shading = f'<w:shd w:fill="{fill}"/>' if fill else ""
    align = '<w:vAlign w:val="center"/>'
    margin = '<w:tcMar><w:top w:w="100" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tcMar>'
    p_align = '<w:jc w:val="center"/>' if center else ""
    paragraphs = content if content.strip().startswith("<w:p") else f"<w:p><w:pPr>{p_align}</w:pPr>{run(content)}</w:p>"
    return (
        "<w:tc>"
        f"<w:tcPr><w:tcW w:w=\"{width}\" w:type=\"dxa\"/>{shading}{align}{margin}</w:tcPr>"
        f"{paragraphs}"
        "</w:tc>"
    )


def table(headers: list[str], rows: list[list[str]], widths: list[int]) -> str:
    borders = (
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        '</w:tblBorders>'
    )
    grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    out = [
        "<w:tbl>",
        f"<w:tblPr><w:tblW w:w=\"9360\" w:type=\"dxa\"/><w:tblLook w:firstRow=\"1\" w:noHBand=\"0\" w:noVBand=\"1\"/>{borders}</w:tblPr>",
        grid,
        "<w:tr><w:trPr><w:tblHeader/></w:trPr>",
    ]
    for title, width in zip(headers, widths):
        out.append(cell(f"<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>{run(title, bold=True, color='FFFFFF')}</w:p>", width, fill="1F4E79", center=True))
    out.append("</w:tr>")
    for index, row in enumerate(rows):
        fill = "F7FAFC" if index % 2 else None
        out.append("<w:tr>")
        for col_index, (value, width) in enumerate(zip(row, widths)):
            out.append(cell(value, width, fill=fill, center=col_index in {0, 2}))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


summary_table = table(
    ["ID", "Area", "Severity", "Clear bug statement", "Required correction"],
    [
        [
            "CFG 07",
            "Subjects and papers",
            "High",
            "A paper can currently be mapped to a subject and session even when that session is not listed as available for the subject.",
            "Reject paper creation or update unless the selected session is available for the selected subject.",
        ],
        [
            "CFG 09",
            "Programmes and courses",
            "High",
            "Programme regulation is stored as free text, while course regulation is a linked Regulation record, allowing conflicting regulation values.",
            "Change Programme regulation to a Regulation foreign key and require courses to use the programme regulation unless an approved exception exists.",
        ],
    ],
    [900, 1450, 1000, 3100, 2910],
)


doc_body = [
    paragraph("Configuration Module Bug Report", style="Title", spacing_after=120),
    paragraph(f"Prepared {date.today().isoformat()}", style="Subtitle", spacing_after=260),
    paragraph(
        "This report clarifies two high severity configuration defects that affect how subjects, sessions, programmes, courses, and papers are linked. The fixes should prevent invalid academic mappings before they reach downstream evaluation workflows.",
        spacing_after=220,
    ),
    paragraph("Summary Of Findings", style="Heading1", spacing_after=140),
    summary_table,
    paragraph("", spacing_after=220),
    paragraph("CFG 07 Subject Session Validation For Papers", style="Heading1", spacing_after=120),
    paragraph("Area: Subjects and papers", bold_lead="Area:", spacing_after=80),
    paragraph("Severity: High", bold_lead="Severity:", spacing_after=80),
    paragraph(
        "Current issue: A paper can be created for any tenant subject and any tenant exam session. The system does not check whether the selected session is included in the subject Available exam sessions list.",
        bold_lead="Current issue:",
        spacing_after=120,
    ),
    paragraph(
        "Risk: This allows papers to be linked to sessions where the subject should not be offered. That can create incorrect paper catalogs, invalid evaluation setup, and downstream allocation errors.",
        bold_lead="Risk:",
        spacing_after=120,
    ),
    paragraph("Required rule", style="Heading2", spacing_after=80),
    bullet("When a subject has one or more available exam sessions, a paper may use only those sessions."),
    bullet("When a subject has no available exam sessions, paper creation must be blocked until the subject session list is configured."),
    bullet("The same validation must run on both paper creation and paper update."),
    paragraph("Acceptance checks", style="Heading2", spacing_after=80),
    bullet("Creating a paper with a listed subject session succeeds."),
    bullet("Creating a paper with an unlisted subject session returns a validation error."),
    bullet("Creating a paper for a subject with no listed sessions returns a clear validation error."),
    bullet("Existing paper update cannot move the paper to an unavailable session."),
    paragraph("CFG 09 Programme And Course Regulation Consistency", style="Heading1", spacing_after=120),
    paragraph("Area: Programmes and courses", bold_lead="Area:", spacing_after=80),
    paragraph("Severity: High", bold_lead="Severity:", spacing_after=80),
    paragraph(
        "Current issue: Programme regulation is a free text value, but course regulation is linked to a Regulation record. Because the two fields use different data models, a programme and its courses can carry conflicting regulations.",
        bold_lead="Current issue:",
        spacing_after=120,
    ),
    paragraph(
        "Risk: Conflicting regulation data can cause incorrect curriculum rules, paper setup, eligibility checks, and reporting results. The inconsistency is hard to detect because free text can look similar while pointing to no controlled record.",
        bold_lead="Risk:",
        spacing_after=120,
    ),
    paragraph("Required rule", style="Heading2", spacing_after=80),
    bullet("Change Programme regulation from free text to a Regulation foreign key."),
    bullet("Courses must inherit or use the programme Regulation record by default."),
    bullet("A course can use a different regulation only when an approved exception record exists."),
    bullet("Validation must prevent direct course regulation conflicts without an approved exception."),
    paragraph("Acceptance checks", style="Heading2", spacing_after=80),
    bullet("A programme cannot be saved without a valid linked Regulation record."),
    bullet("A course under the programme defaults to the programme regulation."),
    bullet("A course cannot be assigned a different regulation unless an approved exception exists."),
    bullet("Reports and APIs return linked regulation identifiers instead of free text programme regulation values."),
]


styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/><w:sz w:val="22"/><w:color w:val="000000"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="120"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Aptos Display" w:hAnsi="Aptos Display"/><w:sz w:val="36"/><w:b/><w:color w:val="000000"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:basedOn w:val="Normal"/>
    <w:rPr><w:sz w:val="20"/><w:color w:val="595959"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:keepNext/><w:spacing w:before="260" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="000000"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:keepNext/><w:spacing w:before="140" w:after="80"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="000000"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ListBullet">
    <w:name w:val="List Bullet"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="80"/></w:pPr>
  </w:style>
</w:styles>
"""


document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    {''.join(doc_body)}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>
"""


content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""

rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""

doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Configuration Module Bug Report</dc:title>
  <dc:subject>CFG 07 and CFG 09</dc:subject>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{date.today().isoformat()}T00:00:00Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{date.today().isoformat()}T00:00:00Z</dcterms:modified>
</cp:coreProperties>
"""

app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUT, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
    print(OUT.resolve())


if __name__ == "__main__":
    main()
