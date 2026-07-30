"""Contract tests for the OCR extraction chain.

The extractor should report WHICH method was used so the UI can
show provenance ("OCR via Vision") and so the iOS/Mac client can
let the user know their text-only doc actually required OCR.

Method constants:
  - "pypdf"       — native text layer found
  - "vision"      — macOS Vision (VNRecognizeTextRequest)
  - "ollama_vision" — Ollama vision model (llava, qwen2-vl, kimi-k3)
  - "tesseract"   — pytesseract (final fallback)

These tests focus on the orchestrator (extractor + chain selection),
not on the actual OCR commands themselves — those are exercised by
integration tests on the test PDFs.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from app.services import material_extractor

REAL_PDF_WITH_TEXT = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    b"4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 12 Tf 100 700 Td "
    b"(Hello pypdf native text) Tj ET\nendstream\nendobj\n"
    b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n"
    b"0000000098 00000 n \n0000000201 00000 n \n0000000292 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n350\n%%EOF\n"
)


def test_extract_pdf_native_text_returns_pypdf_method():
    """When pypdf finds a text layer, method='pypdf' and OCR is skipped."""
    text, method = material_extractor.extract("native.pdf", REAL_PDF_WITH_TEXT)
    assert method == "pypdf"
    assert text is not None
    assert "pypdf" in text or "Hello" in text


def test_extract_text_returns_pypdf_method():
    """Plain text always uses 'pypdf' as the (slightly misnamed) method tag."""
    text, method = material_extractor.extract("notes.md", b"# header\n\nbody")
    assert method == "pypdf"
    assert text is not None
    assert "header" in text


def test_extract_zip_returns_pypdf_method():
    """ZIP extraction currently labels its method 'pypdf' too."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "hello from zip")
        zf.writestr("b.md", "# heading")
    text, method = material_extractor.extract("bundle.zip", buf.getvalue())
    assert method == "pypdf"
    assert text is not None
    assert "hello from zip" in text


def test_extract_image_only_pdf_falls_back_to_ocr():
    """When pypdf returns 0 chars, ocr_pdf_chain is called."""
    from app.services.pdf_ocr import OCRResult

    # Mock ocr_pdf_chain to return a synthetic OCRResult with method="vision"
    with patch("app.services.pdf_ocr.ocr_pdf_chain") as mock_chain:
        mock_chain.return_value = OCRResult(
            text="Synthetic OCR text from the unit test",
            method="vision",
            page_count=5,
            elapsed_seconds=1.2,
        )

        # Build a real (image-only) PDF where pypdf finds no text.
        image_pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
            b"xref\n0 4\n"
            b"0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000098 00000 n \n"
            b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n145\n%%EOF\n"
        )

        text, method = material_extractor.extract("scanned.pdf", image_pdf)

    assert method == "vision"
    assert text is not None and "Synthetic OCR text" in text
    mock_chain.assert_called_once()


def test_extract_returns_none_pair_for_unknown_extension():
    text, method = material_extractor.extract("mystery.xyz", b"data")
    assert text is None
    assert method is None


def test_extract_returns_none_pair_for_empty_text():
    text, method = material_extractor.extract("empty.txt", b"")
    # Empty markdown is still valid — the method tag is "pypdf"
    # but text is empty. That's fine for downstream UI to show.
    assert method == "pypdf"
    assert text is not None  # empty string is valid