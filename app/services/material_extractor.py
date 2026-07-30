"""MVP0.2: extract plain text from uploaded materials.

Supports:
  - .pdf      via pypdf
  - .md       read as UTF-8
  - .txt      read as UTF-8
  - .zip      walk + concatenate text from common code/text files

Other formats return None — the caller is expected to reject the
upload at the API layer (415 Unsupported Media Type).

Limits enforced here:
  - Per-file 50 MB (already validated at upload time, double-checked)
  - Total extracted text per file: 500K chars (truncate if more)
  - Extraction wall-clock timeout: 60s (caller enforces via asyncio)
  - For .zip: max 500 files inside, max 200 MB uncompressed
  - For .zip: skip vendor dirs (node_modules/, .git/, __pycache__/, etc)
  - For .zip: skip binary files (image, video, archive, etc)
"""

from __future__ import annotations

import io
import logging
import zipfile

log = logging.getLogger(__name__)

# Per-file extracted-text cap. The pocket tutor prompt builder will
# truncate further at 50K chars per material (see tutor.py), but this
# cap stops a pathological single PDF from blowing memory.
MAX_EXTRACTED_CHARS_PER_FILE = 500_000

# .zip walker limits
MAX_ZIP_FILES = 500
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MB
MAX_ZIP_INDIVIDUAL_FILE_BYTES = 1 * 1024 * 1024  # 1 MB (per inner file)

# Extensions we know how to read (case-insensitive).
_TEXT_EXTS = {".md", ".markdown", ".txt"}

# Vendor / cache dirs we skip inside .zip archives (zip-bomb guard +
# noise reduction).
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "target", "build", "dist",
    ".venv", "venv", ".next", ".cache", ".idea", ".vscode",
}

# Binary extensions we skip inside .zip archives (cannot inline sensibly).
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".mp3", ".wav", ".flac", ".ogg",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".so", ".dylib", ".dll", ".exe", ".bin",
    ".ttf", ".otf", ".woff", ".woff2",
}


def detect_kind(filename: str) -> str:
    """Return one of: 'pdf', 'markdown', 'text', 'zip', 'unknown'."""
    fn = filename.lower()
    if fn.endswith(".pdf"):
        return "pdf"
    if fn.endswith(".zip"):
        return "zip"
    if fn.endswith((".md", ".markdown")):
        return "markdown"
    if fn.endswith(".txt"):
        return "text"
    return "unknown"


def extract(filename: str, data: bytes) -> str | None:
    """Extract plain text from the given file content.

    Returns:
        Extracted text (possibly empty string if the file is empty),
        or None if the format is unsupported.

    Raises:
        RuntimeError if extraction fails (corrupt file, etc)
    """
    kind = detect_kind(filename)
    if kind == "pdf":
        return _extract_pdf(data)
    if kind == "markdown" or kind == "text":
        return _extract_text(data)
    if kind == "zip":
        return _extract_zip(data, filename)
    return None


def _extract_pdf(data: bytes) -> str:
    """Extract text from a PDF using pypdf. Returns plain text (no formatting).

    Raises:
        RuntimeError("PDF has no extractable text")
            If the PDF contains no text layer (e.g. it's a scan
            produced by jsPDF, an image-only PDF, or a scanned
            document). pypdf can't OCR — the user needs to OCR the
            file first or upload the source document directly. We
            surface this as an error so the material is marked
            `status='failed'` (not `ready` with char_count=0, which
            is misleading — the tutor would have nothing to read).
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        log.error("pypdf not installed — PDF extraction unavailable: %s", exc)
        raise RuntimeError(
            "PDF extraction library (pypdf) is not installed on the server."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise RuntimeError(f"Failed to read PDF: {exc}") from exc

    # Encrypted PDFs need a password we don't have. Surface clearly
    # rather than silently returning empty text.
    if reader.is_encrypted:
        raise RuntimeError(
            "PDF is password-protected. Remove the password and re-upload."
        )

    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            log.warning("Failed to extract text from PDF page %d: %s", i, exc)
            text = ""
        if text.strip():
            parts.append(f"\n--- Page {i + 1} ---\n{text}")
        if sum(len(p) for p in parts) >= MAX_EXTRACTED_CHARS_PER_FILE:
            parts.append("\n\n[... truncated: file exceeds extraction limit ...]")
            break

    full = "\n".join(parts).strip()

    # MVP0.2 fix: a "ready" PDF with char_count=0 was confusing — the
    # user sees a green ready badge and assumes the tutor can read it,
    # but the prompt section will be empty. Common causes:
    #   1. Image-only PDFs (scanned documents, jsPDF-generated files)
    #   2. PDFs with a corrupt text layer
    #   3. Vector PDFs with no text on any page (very rare)
    # In all three cases the user needs to either OCR the file or
    # upload the source document as .md / .txt.
    if not full:
        page_count = len(reader.pages)
        raise RuntimeError(
            f"PDF has no extractable text layer ({page_count} page(s)). "
            "This usually means it's a scanned image or generated by "
            "jsPDF/canvas (no real text). Re-upload as .md/.txt, or run "
            "OCR on the PDF first."
        )

    return full


def _extract_text(data: bytes) -> str:
    """Read plain text as UTF-8, replacing invalid bytes rather than raising."""
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_EXTRACTED_CHARS_PER_FILE:
        text = text[:MAX_EXTRACTED_CHARS_PER_FILE] + "\n\n[... truncated ...]"
    return text


def _extract_zip(data: bytes, archive_name: str) -> str:
    """Walk a .zip archive and concatenate text from common code/text files.

    Skips:
      - vendor / build / cache directories
      - binary files (images, video, archives, fonts)
      - files larger than MAX_ZIP_INDIVIDUAL_FILE_BYTES
      - the archive's own name and any nested archives
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise RuntimeError(f"Failed to open zip: {exc}") from exc

    # Reject zip bombs (inflated size much larger than compressed size)
    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise RuntimeError(
            f"Zip too large when uncompressed ({total_uncompressed // 1024 // 1024} MB > "
            f"{MAX_ZIP_UNCOMPRESSED_BYTES // 1024 // 1024} MB cap)"
        )

    text_parts: list[str] = []
    file_count = 0

    for info in zf.infolist():
        if info.is_dir():
            continue
        if file_count >= MAX_ZIP_FILES:
            log.warning("Reached zip file cap (%d); ignoring remaining files", MAX_ZIP_FILES)
            break

        # Skip vendor / cache dirs in any path component
        path_parts = info.filename.split("/")
        if any(p in _SKIP_DIRS for p in path_parts):
            continue

        # Skip binary extensions
        ext = ""
        if "." in info.filename:
            ext = "." + info.filename.rsplit(".", 1)[-1].lower()
        if ext in _BINARY_EXTS:
            continue

        # Skip files larger than the per-file cap
        if info.file_size > MAX_ZIP_INDIVIDUAL_FILE_BYTES:
            continue

        # Read & decode
        try:
            raw = zf.read(info)
        except (RuntimeError, zipfile.BadZipFile) as exc:
            log.warning("Skipping %s in zip: %s", info.filename, exc)
            continue

        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            log.warning("Skipping %s (decode failed): %s", info.filename, exc)
            continue

        # Strip null bytes (binary files mislabeled as text)
        text = text.replace("\x00", "")
        if not text.strip():
            continue

        text_parts.append(f"\n--- {info.filename} ---\n{text}")
        file_count += 1

        # Cap the total extracted text
        if sum(len(p) for p in text_parts) >= MAX_EXTRACTED_CHARS_PER_FILE:
            text_parts.append("\n\n[... truncated: archive exceeds extraction limit ...]")
            break

    zf.close()

    if not text_parts:
        # No readable text in the zip — return a helpful message instead of empty
        return (
            f"[No readable text files found in {archive_name}. "
            "The archive contains only binary or unsupported files.]"
        )
    return "\n".join(text_parts).strip()
