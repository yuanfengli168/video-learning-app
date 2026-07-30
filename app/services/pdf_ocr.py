"""MVP0.2 followup: OCR chain for image-only PDFs (jsPDF, scans).

When pypdf returns 0 chars for a PDF, the file is "image-only" — the
PDF was either generated from images (jsPDF, scanned, or converted
from a screenshot). We try these in order:

  1. macOS Vision framework (via the bin/material_ocr Swift CLI)
     Preinstalled on every Mac running macOS 12.0+. Uses the
     Neural Engine. Fast (1-3s/page), excellent Chinese accuracy.
     This is the SAME OCR engine macOS Preview uses for "Live Text".

  2. Ollama vision model (llava:13b / qwen2-vl / etc)
     Used when (1) is unavailable (Linux deploy, old macOS) or
     the Vision pass returns 0 chars. Talks to the user's local
     Ollama server which already runs the tutor LLM.

  3. Tesseract (Homebrew)
     Last-resort CPU-only OCR. Slow (10-30s/page) but handles huge
     docs and very low-quality scans. Requires `brew install
     tesseract tesseract-lang`.

Each layer returns:
  (text, method, error)  where method is one of:
  - "vision": macOS Vision succeeded
  - "ollama_vision": Ollama llava/etc succeeded
  - "tesseract": Tesseract succeeded
  - None: every path failed (text is empty, error explains why)

Layer 2 and 3 share the same rendering path: PDFKit's
`thumbnailOfSize_forBox_` (exposed via PyObjC) renders each page to
NSImage → PNG bytes. Layer 1 (macOS Vision) uses the Swift CLI which
internally does the same rendering.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Apple frameworks (PyObjC) — used for PDFKit rendering when
# Vision is unavailable. Lazy-loaded so Linux deploys (no PyObjC)
# don't crash at import time.
_PDFKit = None
_Quartz = None
_AppKit = None


def _load_pyobjc_frameworks():
    """Lazy-load PDFKit/Quartz/AppKit via PyObjC.

    Returns True if all three loaded, False if PyObjC isn't installed
    or we're not on macOS. Used by the Tesseract path to render PDF
    pages to PNG before feeding to Tesseract (since we can't use
    pdf2image without installing Poppler).
    """
    global _PDFKit, _Quartz, _AppKit
    if _PDFKit is not None:
        return True
    if not shutil.which("python3"):
        return False
    try:
        import objc  # noqa: F401

        if objc is None:
            return False
    except ImportError:
        return False
    try:
        _PDFKit = objc.loadBundle(
            "PDFKit",
            bundle_path="/System/Library/Frameworks/PDFKit.framework",
            module_globals=globals(),
        )
        _Quartz = objc.loadBundle(
            "Quartz",
            bundle_path="/System/Library/Frameworks/Quartz.framework",
            module_globals=globals(),
        )
        _AppKit = objc.loadBundle(
            "AppKit",
            bundle_path="/System/Library/Frameworks/AppKit.framework",
            module_globals=globals(),
        )
        return True
    except Exception as exc:
        log.warning("PDFKit/Quartz/AppKit load failed: %s", exc)
        return False


@dataclass
class OCRResult:
    text: str
    method: Optional[str]  # "vision" | "ollama_vision" | "tesseract" | None
    page_count: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────
# Layer 1: macOS Vision framework (via bin/material_ocr Swift CLI)
# ─────────────────────────────────────────────────────────────────────────


def ocr_macos_vision(pdf_path: Path, max_pages: int, langs: list[str], timeout_seconds: int) -> OCRResult:
    """Run Apple's macOS Vision framework on the PDF.

    Calls the bin/material_ocr Swift CLI which uses PDFKit to render
    each page, then VNRecognizeTextRequest to extract text. Runs on
    the Neural Engine. No model download, no third-party deps.

    Args:
        pdf_path: path to the PDF file
        max_pages: cap on number of pages to OCR (safety)
        langs: language hints for Vision (e.g. ["zh-Hans", "en-US"])
        timeout_seconds: subprocess timeout

    Returns:
        OCRResult with text + method="vision" on success, or
        method=None + error on failure.
    """
    from app.config import settings

    # Project root = upload_path.parent (e.g. ./uploads -> .)
    binary = settings.upload_path.parent / "bin" / "material_ocr"
    if not binary.exists():
        return OCRResult(
            text="", method=None, error="macOS Vision OCR binary not built (run bin/build_ocr.sh)"
        )

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                str(binary),
                "--pdf", str(pdf_path),
                "--max-pages", str(max_pages),
                "--langs", "+".join(langs),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return OCRResult(
            text="", method=None,
            error=f"macOS Vision OCR timed out after {timeout_seconds}s",
        )
    except Exception as exc:
        return OCRResult(text="", method=None, error=f"macOS Vision OCR failed: {exc}")

    if proc.returncode != 0:
        return OCRResult(
            text="", method=None,
            error=f"macOS Vision OCR failed (exit {proc.returncode}): {proc.stderr[:200]}",
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return OCRResult(text="", method=None, error=f"macOS Vision OCR returned invalid JSON: {exc}")

    if not data.get("ok"):
        return OCRResult(
            text="", method=None,
            error=f"macOS Vision OCR error: {data.get('error', 'unknown')}",
        )

    pages = data.get("pages", [])
    parts = []
    for page in pages:
        if page.get("text"):
            parts.append(f"\n--- Page {page['index'] + 1} ---\n{page['text']}")
    text = "\n".join(parts).strip()

    return OCRResult(
        text=text,
        method="vision" if text else None,
        page_count=len(pages),
        elapsed_seconds=time.monotonic() - start,
        error=None if text else "macOS Vision OCR returned 0 chars for all pages",
    )


# ─────────────────────────────────────────────────────────────────────────
# Layer 2: Ollama vision model
# ─────────────────────────────────────────────────────────────────────────


def _render_pdf_pages_to_pngs(pdf_path: Path, scale: float = 2.0, max_pages: int = 50) -> list[bytes]:
    """Render each PDF page to PNG bytes via Apple PDFKit.

    Returns a list of PNG-encoded page images (one per page, up to
    max_pages). Returns [] if PyObjC/PDFKit isn't available.

    Uses PDFPage.thumbnailOfSize_forBox_ which is exposed by PyObjC
    (verified working — see commit history for the test script).
    """
    if not _load_pyobjc_frameworks():
        raise RuntimeError(
            "PDFKit not available (PyObjC not installed or not on macOS). "
            "Install pypyobjc or pdf2image + poppler to render PDFs."
        )

    doc = _PDFKit.PDFDocument.alloc().initWithURL_(
        _Quartz.NSURL.fileURLWithPath_(str(pdf_path.absolute()))
    )
    if not doc:
        raise RuntimeError(f"PDFKit failed to open {pdf_path}")

    total = doc.pageCount()
    limit = min(total, max_pages)
    pngs = []
    for i in range(limit):
        page = doc.pageAtIndex_(i)
        if not page:
            continue
        origin_size = page.boundsForBox_(0)
        # boundsForBox_ returns ((originX, originY), (w, h))
        origin = origin_size[0]
        size = origin_size[1]
        render_size = (float(size.width) * scale, float(size.height) * scale)
        ns_image = page.thumbnailOfSize_forBox_(render_size, 0)  # 0 = mediaBox
        if not ns_image:
            continue
        tiff = ns_image.TIFFRepresentation()
        bmp = _AppKit.NSBitmapImageRep.imageRepWithData_(tiff)
        if not bmp:
            continue
        png = bmp.representationUsingType_properties_(4, None)  # NSPNGFileType = 4
        if png:
            pngs.append(png)
    return pngs


def ocr_ollama_vision(pdf_path: Path, model: str, max_pages: int, langs: list[str], timeout_seconds: int) -> OCRResult:
    """Run an Ollama-hosted vision model on the PDF.

    Renders each page to PNG via PDFKit (PyObjC), sends each PNG to
    Ollama's /api/generate with `images=[...]`. Falls back if Ollama
    isn't reachable or the model isn't pulled.

    Args:
        pdf_path: path to the PDF
        model: Ollama model name (e.g. "llava:13b", "qwen2-vl:7b")
        max_pages: cap on pages (Ollama vision is slower)
        langs: language hints (passed in prompt, not as Vision param)
        timeout_seconds: per-request timeout

    Returns:
        OCRResult with text + method="ollama_vision" on success.
    """
    import base64
    import httpx

    from app.config import settings

    start = time.monotonic()
    try:
        pngs = _render_pdf_pages_to_pngs(pdf_path, max_pages=max_pages)
    except Exception as exc:
        return OCRResult(text="", method=None, error=f"PDF rendering failed: {exc}")

    if not pngs:
        return OCRResult(text="", method=None, error="PDF rendering produced 0 images")

    ollama_base = f"http://localhost:{settings.ollama_port}"
    lang_hint = " ".join(langs) if langs else "auto-detect"

    parts = []
    for i, png_bytes in enumerate(pngs):
        b64 = base64.b64encode(png_bytes).decode("ascii")
        prompt = (
            f"Extract all text visible on this document page. "
            f"The document contains text in these languages: {lang_hint}. "
            "Output the raw text only — no commentary, no markdown "
            "formatting, no translation. If a region is a heading, table "
            "cell, or list item, preserve that structure with line breaks. "
            "If the page is blank, output just the single word 'BLANK'."
        )
        try:
            resp = httpx.post(
                f"{ollama_base}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                },
                timeout=min(timeout_seconds, 120),  # per-page cap
            )
            if resp.status_code != 200:
                log.warning("Ollama vision %s page %d: HTTP %d", model, i, resp.status_code)
                continue
            text = resp.json().get("response", "").strip()
            if text and text != "BLANK":
                parts.append(f"\n--- Page {i + 1} ---\n{text}")
        except Exception as exc:
            log.warning("Ollama vision %s page %d failed: %s", model, i, exc)
            continue

    full = "\n".join(parts).strip()
    return OCRResult(
        text=full,
        method="ollama_vision" if full else None,
        page_count=len(pngs),
        elapsed_seconds=time.monotonic() - start,
        error=None if full else f"Ollama vision ({model}) returned 0 chars for all pages",
    )


# ─────────────────────────────────────────────────────────────────────────
# Layer 3: Tesseract (Homebrew)
# ─────────────────────────────────────────────────────────────────────────


def ocr_tesseract(pdf_path: Path, langs: str, max_pages: int, timeout_seconds: int) -> OCRResult:
    """Run Tesseract on each rendered PDF page.

    Requires:
      - `brew install tesseract tesseract-lang` (binary + lang data)
      - Python: pytesseract + Pillow (in requirements.txt)
      - PyObjC: PDFKit for rendering (since pdftoppm isn't shipped)

    Args:
        pdf_path: path to the PDF
        langs: Tesseract lang string (e.g. "chi_sim+eng")
        max_pages: cap on pages
        timeout_seconds: per-page timeout

    Returns:
        OCRResult with text + method="tesseract" on success.
    """
    if not shutil.which("tesseract"):
        return OCRResult(
            text="", method=None,
            error="tesseract binary not found (brew install tesseract tesseract-lang)",
        )
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        return OCRResult(text="", method=None, error=f"pytesseract/Pillow not installed: {exc}")

    start = time.monotonic()
    try:
        pngs = _render_pdf_pages_to_pngs(pdf_path, max_pages=max_pages)
    except Exception as exc:
        return OCRResult(text="", method=None, error=f"PDF rendering failed: {exc}")

    if not pngs:
        return OCRResult(text="", method=None, error="PDF rendering produced 0 images")

    parts = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for i, png_bytes in enumerate(pngs):
            page_file = tmp / f"page-{i:03d}.png"
            page_file.write_bytes(png_bytes)
            try:
                text = pytesseract.image_to_string(
                    Image.open(page_file),
                    lang=langs,
                    timeout=min(timeout_seconds, 120),
                )
                text = (text or "").strip()
                if text:
                    parts.append(f"\n--- Page {i + 1} ---\n{text}")
            except Exception as exc:
                log.warning("tesseract page %d failed: %s", i, exc)
                continue

    full = "\n".join(parts).strip()
    return OCRResult(
        text=full,
        method="tesseract" if full else None,
        page_count=len(pngs),
        elapsed_seconds=time.monotonic() - start,
        error=None if full else "Tesseract returned 0 chars for all pages",
    )


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator: try each layer in order
# ─────────────────────────────────────────────────────────────────────────


def ocr_pdf_chain(pdf_path: Path, settings_obj, timeout_seconds: Optional[int] = None) -> OCRResult:
    """Run the OCR chain on the PDF. Returns the first non-empty result.

    Order (cheapest + best-quality first):
      1. macOS Vision (Neural Engine, no model download)
      2. Ollama vision LLM (llava:13b, GPU via Ollama)
      3. Tesseract (CPU, slowest)

    Each layer is independent — a failure in one doesn't break the
    chain. The first layer to return non-empty text wins; later
    layers are NOT called.

    Args:
        pdf_path: path to the PDF
        settings_obj: app.config.settings (for material_ocr_* knobs)
        timeout_seconds: total wall-clock cap (defaults to settings)

    Returns:
        OCRResult with method set to whichever layer succeeded.
        If all layers fail, text is empty + error explains the last
        failure (which is usually the most informative).
    """
    if not settings_obj.materials_ocr_enabled:
        return OCRResult(text="", method=None, error="OCR disabled in settings")

    budget = timeout_seconds or settings_obj.materials_ocr_timeout_seconds
    budget_per_layer = max(60, budget // 3)  # give each layer a fair share

    # Decide language hints
    # Vision framework takes BCP-47 tags; Tesseract takes ISO 639-2/3 codes.
    # We use a sensible default for mixed Chinese/English docs.
    vision_langs = ["zh-Hans", "en-US"]
    tesseract_langs = settings_obj.materials_ocr_tesseract_lang  # e.g. "chi_sim+eng"

    # Layer 1: macOS Vision (best on Apple Silicon)
    log.info("OCR layer 1: macOS Vision")
    res = ocr_macos_vision(pdf_path, settings_obj.materials_ocr_macos_vision_max_pages, vision_langs, budget_per_layer)
    if res.method:
        return res
    log.info("  macOS Vision failed: %s", res.error)

    # Layer 2: Ollama vision model
    log.info("OCR layer 2: Ollama vision (%s)", settings_obj.materials_ocr_ollama_vision_model)
    res = ocr_ollama_vision(pdf_path, settings_obj.materials_ocr_ollama_vision_model, 50, vision_langs, budget_per_layer)
    if res.method:
        return res
    log.info("  Ollama vision failed: %s", res.error)

    # Layer 3: Tesseract
    log.info("OCR layer 3: Tesseract (%s)", tesseract_langs)
    res = ocr_tesseract(pdf_path, tesseract_langs, 100, budget_per_layer)
    if res.method:
        return res
    log.info("  Tesseract failed: %s", res.error)

    return OCRResult(
        text="",
        method=None,
        error=f"All OCR paths failed. Last error: {res.error}",
    )