"""Generate the fictional Maya Shah PDF and DOCX demo resumes.

The PDF writer uses only the standard library.  The DOCX sample uses the same
``python-docx`` dependency as the application parser.
"""

from __future__ import annotations

from pathlib import Path


OUTPUT_DIR = Path(__file__).resolve().parent

RESUME_LINES = [
    "Maya Shah",
    "maya.shah@example.com | +91 90000 00000",
    "PROFESSIONAL SUMMARY",
    "Junior analyst who enjoys turning local business data into clear reports.",
    "SKILLS",
    "SQL, Python, Git, JS",
    "EDUCATION",
    "Bachelor of Science in Computer Science, Fictional City College, 2024",
    "WORK EXPERIENCE",
    "Data Intern, Example Analytics Lab, 2024 - Present",
    "Prepared SQL reports and maintained small Python data-cleaning scripts.",
]


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_minimal_pdf(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "50 760 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -20 Td")
        commands.append(f"({_pdf_escape(line)}) Tj")
    commands.append("ET")
    stream = ("\n".join(commands) + "\n").encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(pdf)


def write_docx(path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("Maya Shah", level=0)
    document.add_paragraph("maya.shah@example.com | +91 90000 00000")
    document.add_heading("Professional Summary", level=1)
    document.add_paragraph(
        "Junior analyst who enjoys turning local business data into clear reports."
    )
    document.add_heading("Skills", level=1)
    skills_table = document.add_table(rows=2, cols=2)
    skills_table.cell(0, 0).text = "SQL"
    skills_table.cell(0, 1).text = "Python"
    skills_table.cell(1, 0).text = "Git"
    skills_table.cell(1, 1).text = "JS"
    document.add_heading("Education", level=1)
    document.add_paragraph(
        "Bachelor of Science in Computer Science, Fictional City College, 2024"
    )
    document.add_heading("Work Experience", level=1)
    experience_table = document.add_table(rows=2, cols=2)
    experience_table.cell(0, 0).text = "2024 - Present"
    experience_table.cell(0, 1).text = "Data Intern, Example Analytics Lab"
    experience_table.cell(1, 0).text = "Highlights"
    experience_table.cell(1, 1).text = (
        "Prepared SQL reports and maintained small Python data-cleaning scripts."
    )
    document.save(path)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "maya_shah.pdf").write_bytes(build_minimal_pdf(RESUME_LINES))
    write_docx(OUTPUT_DIR / "maya_shah.docx")
    print("Generated maya_shah.pdf and maya_shah.docx")


if __name__ == "__main__":
    main()
