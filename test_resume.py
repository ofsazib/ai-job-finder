from io import BytesIO
from unittest.mock import patch

import pytest
from pypdf import PdfWriter


def blank_pdf() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)
    return stream.getvalue()


def test_resolve_resume_prefers_existing_markdown(tmp_path, monkeypatch):
    import resume
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("existing")
    (tmp_path / "resume.pdf").write_bytes(blank_pdf())
    assert resume.resolve_resume() == tmp_path / "resume.md"
    assert (tmp_path / "resume.md").read_text() == "existing"


def test_resolve_resume_discovers_root_pdf(tmp_path, monkeypatch):
    import resume
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cv.pdf").write_bytes(blank_pdf())
    with patch.object(resume, "extract_pdf_text", return_value="Jane Doe\nPython Engineer"), \
         patch.object(resume, "clean_resume_markdown", return_value="# Jane Doe\n\nPython Engineer"):
        path = resume.resolve_resume()
    assert path == tmp_path / "resume.md"
    assert path.read_text() == "# Jane Doe\n\nPython Engineer\n"


def test_generate_resume_rejects_scanned_pdf(tmp_path, monkeypatch):
    import resume
    monkeypatch.chdir(tmp_path)
    with pytest.raises(resume.ResumeError, match="scanned"):
        resume.generate_resume_md(blank_pdf())


def test_generate_resume_does_not_overwrite_without_replace(tmp_path, monkeypatch):
    import resume
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("keep me")
    with pytest.raises(resume.ResumeError, match="already exists"):
        resume.generate_resume_md(blank_pdf())
    assert (tmp_path / "resume.md").read_text() == "keep me"


def test_generate_resume_falls_back_when_ai_cleanup_fails(tmp_path, monkeypatch):
    import resume
    monkeypatch.chdir(tmp_path)
    with patch.object(resume, "extract_pdf_text", return_value="Jane Doe\nPython Engineer"), \
         patch.object(resume, "clean_resume_markdown", side_effect=RuntimeError("AI down")):
        resume.generate_resume_md(blank_pdf())
    assert (tmp_path / "resume.md").read_text() == "Jane Doe\nPython Engineer\n"
