from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUTPUT = "/Users/mung-i/Crypto_Bot_Copier/Text_Filtering_Regex_Guide.docx"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_borders(cell, color="D9D9D9", size="6"):
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = qn(f"w:{edge}")
        node = borders.find(tag)
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), size)
        node.set(qn("w:color"), color)


def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def set_table_widths(table, widths):
    for row in table.rows:
        for idx, width in enumerate(widths):
            row.cells[idx].width = Inches(width)


def format_table(table, widths):
    table.autofit = False
    set_table_widths(table, widths)
    set_repeat_table_header(table.rows[0])
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            set_cell_borders(cell)
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if row_index == 0:
                set_cell_shading(cell, "1F4E78")
            elif row_index % 2 == 0:
                set_cell_shading(cell, "EAF2F8")
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = 1.05
                for run in paragraph.runs:
                    run.font.size = Pt(9.5)
                    if row_index == 0:
                        run.font.bold = True
                        run.font.color.rgb = RGBColor(255, 255, 255)


def add_code_run(paragraph, text):
    run = paragraph.add_run(text)
    run.font.name = "Courier New"
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Courier New")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Courier New")
    run.font.size = Pt(10.5)
    return run


doc = Document()
section = doc.sections[0]
section.page_width = Inches(8.5)
section.page_height = Inches(11)
section.top_margin = Inches(0.7)
section.bottom_margin = Inches(0.7)
section.left_margin = Inches(0.75)
section.right_margin = Inches(0.75)

styles = doc.styles
styles["Normal"].font.name = "Aptos"
styles["Normal"]._element.rPr.rFonts.set(qn("w:ascii"), "Aptos")
styles["Normal"]._element.rPr.rFonts.set(qn("w:hAnsi"), "Aptos")
styles["Normal"].font.size = Pt(11)
styles["Normal"].font.color.rgb = RGBColor(32, 32, 32)
styles["Normal"].paragraph_format.space_after = Pt(6)
styles["Normal"].paragraph_format.line_spacing = 1.08

for style_name, size, before, after in (
    ("Title", 25, 0, 10),
    ("Heading 1", 16, 15, 6),
    ("Heading 2", 12, 10, 4),
):
    style = styles[style_name]
    style.font.name = "Aptos Display" if style_name != "Heading 2" else "Aptos"
    style._element.rPr.rFonts.set(qn("w:ascii"), style.font.name)
    style._element.rPr.rFonts.set(qn("w:hAnsi"), style.font.name)
    style.font.size = Pt(size)
    style.font.bold = True
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.paragraph_format.space_before = Pt(before)
    style.paragraph_format.space_after = Pt(after)
    style.paragraph_format.keep_with_next = True

title = doc.add_paragraph(style="Title")
title.add_run("Simple Text Filtering With Regular Expressions")

intro = doc.add_paragraph()
intro.add_run("Use this guide when a filter supports regular expressions or regex. ").bold = True
intro.add_run(
    "The symbols tell the filter where a word may appear and what may come before or after it. "
    "For the word cat, the most useful patterns are exact match, starts with, ends with, contains, and whole word."
)

doc.add_heading("The four patterns to remember", level=1)
table = doc.add_table(rows=1, cols=4)
headers = ["Goal", "Pattern", "Matches", "Does not match"]
for idx, text in enumerate(headers):
    table.rows[0].cells[idx].text = text
rows = [
    ("Only cat", "^cat$", "cat", "black cat, cats, cat food"),
    ("Nothing before cat", "^cat", "cat, cats, cat food", "black cat"),
    ("Nothing after cat", "cat$", "cat, black cat", "cats, cat food"),
    ("Cat anywhere", "cat", "cat, black cat, category", "dog"),
    ("Cat as a whole word", r"\bcat\b", "cat, black cat, cat food", "cats, category, copycat"),
]
for item in rows:
    cells = table.add_row().cells
    for idx, text in enumerate(item):
        cells[idx].text = text
        if idx == 1:
            for run in cells[idx].paragraphs[0].runs:
                run.font.name = "Courier New"
                run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Courier New")
                run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Courier New")
format_table(table, [1.6, 1.1, 2.15, 2.15])

doc.add_heading("What the symbols mean", level=1)
symbols = doc.add_table(rows=1, cols=3)
for idx, text in enumerate(["Symbol", "Meaning", "Example"]):
    symbols.rows[0].cells[idx].text = text
symbol_rows = [
    ("^", "Beginning of the text", "^cat means the text must begin with cat"),
    ("$", "End of the text", "cat$ means the text must end with cat"),
    (r"\b", "Boundary between a word and a nonword character", r"\bcat\b finds cat as a separate word"),
    (".", "Any single character", "c.t matches cat, cot, and cut"),
    ("*", "Zero or more of the item before it", "ca*t matches ct, cat, and caaat"),
    ("+", "One or more of the item before it", "ca+t matches cat and caaat, but not ct"),
    ("?", "The item before it is optional", "colou?r matches color and colour"),
    ("[ ]", "One character from a set", "c[au]t matches cat and cut"),
    ("( )", "Groups parts together", "^(cat|dog)$ matches only cat or dog"),
    ("|", "Or", "cat|dog matches cat or dog"),
]
for item in symbol_rows:
    cells = symbols.add_row().cells
    for idx, text in enumerate(item):
        cells[idx].text = text
        if idx == 0:
            for run in cells[idx].paragraphs[0].runs:
                run.font.name = "Courier New"
                run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Courier New")
                run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Courier New")
format_table(symbols, [0.9, 2.55, 3.5])

doc.add_page_break()
doc.add_heading("Useful filtering examples", level=1)
examples = doc.add_table(rows=1, cols=3)
for idx, text in enumerate(["What you want", "Pattern", "Explanation"]):
    examples.rows[0].cells[idx].text = text
example_rows = [
    ("Only cat or dog", "^(cat|dog)$", "The complete value must be cat or dog"),
    ("Starts with cat or dog", "^(cat|dog)", "Either word must appear at the beginning"),
    ("Ends with cat or dog", "(cat|dog)$", "Either word must appear at the end"),
    ("Contains cat but not category", r"\bcat\b", "Cat must appear as a separate word"),
    ("One or more spaces", r"\s+", "Finds a run of spaces, tabs, or line breaks"),
    ("Empty value", "^$", "There are no characters at all"),
    ("Not empty", ".+", "There is at least one character"),
    ("Digits only", r"^\d+$", "Every character must be a digit"),
    ("A simple email shape", r"^[^\s@]+@[^\s@]+\.[^\s@]+$", "Checks for text, an at sign, a domain, and a dot"),
]
for item in example_rows:
    cells = examples.add_row().cells
    for idx, text in enumerate(item):
        cells[idx].text = text
        if idx == 1:
            for run in cells[idx].paragraphs[0].runs:
                run.font.name = "Courier New"
                run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Courier New")
                run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Courier New")
format_table(examples, [2.1, 2.1, 2.75])

doc.add_heading("Uppercase and lowercase", level=1)
p = doc.add_paragraph()
p.add_run("Some filters treat ")
add_code_run(p, "cat")
p.add_run(" and ")
add_code_run(p, "Cat")
p.add_run(" as different text. If the filter supports inline options, use ")
add_code_run(p, "(?i)^cat$")
p.add_run(" to match cat in any letter case. Some tools provide a separate case sensitive switch instead.")

doc.add_heading("Spaces can change the result", level=1)
p = doc.add_paragraph()
p.add_run("The exact pattern ")
add_code_run(p, "^cat$")
p.add_run(" does not match a value such as ")
add_code_run(p, " cat ")
p.add_run(" because that value has spaces. If supported, ")
add_code_run(p, r"^\s*cat\s*$")
p.add_run(" allows optional spaces before and after cat.")

doc.add_heading("Special characters", level=1)
p = doc.add_paragraph()
p.add_run("Characters such as ")
add_code_run(p, ". ^ $ * + ? ( ) [ ] { } | \\")
p.add_run(" have special meanings in regex. Add a backslash when you want the literal character. For example, ")
add_code_run(p, r"example\.com")
p.add_run(" matches example.com, and ")
add_code_run(p, r"\$10")
p.add_run(" matches $10.")

doc.add_heading("How to build a filter", level=1)
for text in (
    "Write one example that should match and one that should not match.",
    "Choose the location rule: beginning, end, exact value, anywhere, or whole word.",
    "Add ^, $, or \\b only where needed.",
    "Test uppercase letters, extra spaces, punctuation, and plural words.",
    "Confirm that the filter actually supports regex and that regex mode is enabled.",
):
    doc.add_paragraph(text, style="List Number")

doc.add_heading("Quick decision guide", level=1)
decision = doc.add_table(rows=1, cols=2)
decision.rows[0].cells[0].text = "Question"
decision.rows[0].cells[1].text = "Use"
decision_rows = [
    ("Must the entire value be cat", "^cat$"),
    ("May text come after cat", "^cat"),
    ("May text come before cat", "cat$"),
    ("May cat appear anywhere", "cat"),
    ("Must cat be a separate word", r"\bcat\b"),
]
for item in decision_rows:
    cells = decision.add_row().cells
    cells[0].text, cells[1].text = item
    for run in cells[1].paragraphs[0].runs:
        run.font.name = "Courier New"
        run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Courier New")
        run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Courier New")
format_table(decision, [5.5, 1.4])

note = doc.add_paragraph()
note.paragraph_format.space_before = Pt(9)
note.add_run("Important  ").bold = True
note.add_run("Regex features vary between applications. If a pattern does not work, check whether regex mode is enabled and whether that application uses a different regex engine.")

doc.core_properties.title = "Simple Text Filtering With Regular Expressions"
doc.core_properties.subject = "A beginner guide to regex text filters"
doc.core_properties.author = ""
doc.save(OUTPUT)
print(OUTPUT)
