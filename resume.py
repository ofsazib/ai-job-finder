"""Create the pipeline's resume.md from a text-based PDF."""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from ai_cli import run_text

MAX_PDF_BYTES = 10 * 1024 * 1024
ROOT_PDFS = ("resume.pdf", "cv.pdf")


class ResumeError(RuntimeError):
    pass


def extract_pdf_text(data: bytes) -> str:
    if not data or len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF-"):
        raise ResumeError("Upload a valid PDF no larger than 10 MB.")
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    except Exception as exc:
        raise ResumeError("The PDF could not be read.") from exc
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(text) < 40:
        raise ResumeError("This looks like a scanned PDF. OCR is required; upload a text-based PDF.")
    return text


def clean_resume_markdown(text: str) -> str:
    return run_text(
        """Convert the extracted resume text below into clean Markdown.
Preserve every fact exactly. Do not invent, infer, summarize, or embellish anything.
Use simple headings and bullets where helpful. Return only Markdown.""",
        context="---EXTRACTED RESUME---\n" + text,
    )


def generate_resume_md(data: bytes, replace: bool = False) -> Path:
    destination = Path.cwd() / "resume.md"
    if destination.exists() and not replace:
        raise ResumeError("resume.md already exists; use replace=true to replace it.")
    extracted = extract_pdf_text(data)
    try:
        markdown = clean_resume_markdown(extracted).strip() or extracted
    except Exception:
        markdown = extracted
    temporary = destination.with_suffix(".md.tmp")
    temporary.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def resolve_resume() -> Path:
    markdown = Path.cwd() / "resume.md"
    if markdown.exists():
        return markdown
    for filename in ROOT_PDFS:
        pdf = Path.cwd() / filename
        if pdf.exists():
            return generate_resume_md(pdf.read_bytes())
    raise ResumeError(
        "Missing resume.md: add resume.md, resume.pdf, or cv.pdf, or upload a PDF in the dashboard."
    )
