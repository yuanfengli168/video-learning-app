# OCR Strategy — MVP0.2 followup

## The problem

`pypdf` extracts text from the **native text layer** of a PDF. Many
real-world PDFs don't have a native text layer — they're literally
a stack of images:

- Scanned textbooks (the user photographed the page)
- `jsPDF`-generated invoices (jsPDF by default renders text as paths)
- Image-only PDF receipts
- Pre-rendered PDFs from design tools (Figma, Sketch export)

For these PDFs, `pypdf` returns 0 chars and the user-uploaded
material becomes silently useless to the tutor — the LLM has no
context to answer questions about the PDF.

## The fix: 3-tier OCR pipeline

```
   PDF uploaded
        ↓
   pypdf.extract_text()  ──→ native text? ──yes──→ done (method: "pypdf")
        │ no chars
        ↓
   macOS Vision (Swift CLI)  ──→ text? ──yes──→ done (method: "vision")
        │ empty / error
        ↓
   Ollama vision (llava:13b) ──→ text? ──yes──→ done (method: "ollama_vision")
        │ empty / error
        ↓
   Tesseract (pytesseract) ──→ text? ──yes──→ done (method: "tesseract")
        │
        ↓
   (None, None) — material marked failed
```

### Layer 1: macOS Vision (primary on Mac)

- **Speed**: ~1–3s/page on Apple Silicon Neural Engine
- **Quality**: Excellent for printed text, Chinese, English, mixed
- **Cost**: Free, runs locally
- **Limit**: 50 pages max per call (configurable via
  `materials_ocr_macos_vision_max_pages`)
- **Implementation**: Swift CLI (`bin/material_ocr.swift`) that
  wraps PDFKit + `VNRecognizeTextRequest`. The binary lives at
  `bin/material_ocr`. Built via `bin/build_ocr.sh`.
- **Why a Swift CLI?** PyObjC exposes Objective-C methods on
  Apple's PDFKit (e.g. `PDFPage.thumbnailOfSize_forBox_()`), but
  cannot bind the C functions on opaque types like `CGContext`
  (`translateCTM_`, `scaleCTM_`, `fillRect_`). The Swift CLI does
  all the rendering + Vision work natively and emits JSON to
  stdout for Python to consume.

### Layer 2: Ollama vision (Linux deploy / Vision unavailable)

- **Speed**: ~5–10s/page (depends on the model)
- **Quality**: Good for figures, diagrams, mixed layouts; can
  "see" the page rather than just OCR'ing text
- **Cost**: Local compute, ~8 GB RAM for `llava:13b`
- **Implementation**: `app/services/pdf_ocr.py:ocr_ollama_vision()`.
  Renders each page to a PNG via PyObjC's
  `PDFPage.thumbnailOfSize_forBox_()`, then POSTs to Ollama
  `POST /api/generate` with the image attached.
- **Model**: `llava:13b` (default, configurable via
  `materials_ocr_ollama_vision_model`).

### Layer 3: Tesseract (final fallback)

- **Speed**: ~10–30s/page on CPU
- **Quality**: Excellent for clean scans, weaker for complex layouts
- **Cost**: Free, local
- **Implementation**: `app/services/pdf_ocr.py:ocr_tesseract()`. Renders
  each page to PNG via PyObjC, then runs `pytesseract.image_to_string`
  with `chi_sim+eng` langs (configurable via
  `materials_ocr_tesseract_lang`).
- **Limitation**: CPU-only on most Macs. Slow on big PDFs.

## Configuration

```python
# app/config.py
materials_ocr_enabled: bool = True
materials_ocr_macos_vision_max_pages: int = 50
materials_ocr_ollama_vision_model: str = "llava:13b"
materials_ocr_tesseract_lang: str = "chi_sim+eng"
materials_ocr_timeout_seconds: int = 600
```

Setting `materials_ocr_enabled = False` reverts to the pre-MVP0.2
behaviour (pypdf only, image-only PDFs return 0 chars).

## Building the Swift CLI

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app
./bin/build_ocr.sh
# compiles bin/material_ocr.swift to bin/material_ocr
# (uses System PDFKit + Vision + AppKit frameworks, no extra deps)
```

The binary is gitignored (`bin/material_ocr`,
`bin/material_ocr.dSYM/`). Re-run the build script whenever the
Swift source changes.

## JSON output format

`bin/material_ocr` reads a PDF path from argv and writes JSON:

```json
{
  "ok": true,
  "pageCount": 84,
  "elapsedSeconds": 117.5,
  "pages": [
    {"index": 0, "text": "Recognized text...", "confidence": 0.92, "charCount": 1234},
    ...
  ]
}
```

On failure (corrupt PDF, Vision error):

```json
{
  "ok": false,
  "error": "Failed to open PDF: ..."
}
```

The wrapper (`ocr_macos_vision`) parses this and maps it to the
`OCRResult` dataclass:

```python
@dataclass
class OCRResult:
    text: str
    method: str | None          # "vision" | "ollama_vision" | "tesseract" | None
    page_count: int
    elapsed_seconds: float
    error: str | None = None
```

## Tutor prompt integration

The extractor's `extract()` returns `(text, method)`. The
method is stored on the `PocketMaterial.extraction_method` column
(string, 32 chars max). The picker UI shows it as a small badge:

| Method    | Badge                | When                                              |
|-----------|----------------------|---------------------------------------------------|
| `pypdf`   | (none)               | Native PDF text layer found                       |
| `vision`  | 🍎 Vision            | macOS Vision OCR succeeded                        |
| `ollama_vision` | 👁 Ollama       | Ollama vision model succeeded                     |
| `tesseract` | 🔤 Tesseract      | Tesseract succeeded (Vision + Ollama both failed) |

When the user selects an OCR'd material, the materials picker shows
a yellow banner reminding them to switch to a vision-capable tutor
(llava, qwen2-vl, kimi-k3) for visual reasoning over the original
PDF layout. The tutor machine still works — it just reads the
OCR'd text equivalent.

## Model capability probe

`app/services/model_capabilities.py` queries Ollama's `/api/show`
endpoint to determine whether the user's primary tutor model can
accept image inputs. Caches results 5 min per model name.

```python
caps = probe_model_capabilities("glm-5.2:cloud")
# caps.is_vision = False
# caps.family = "glm5.2"
# caps.context_length = 1_000_000

caps = probe_model_capabilities("llava:13b")
# caps.is_vision = True
# caps.family = "llama"

vision_model = find_available_vision_model()
# Returns "llava:13b" if any vision-capable model is installed
```

Used by:

- `MaterialContext.vision_required_hint` — UI hint when the user
  has OCR'd materials and a text-only tutor
- The OCR chain's last-resort fallback (currently not auto-wired;
  Vision + Ollama + Tesseract cover the common cases)

## When to add a new OCR backend

Add a new layer when:

1. A new platform needs support (e.g. Linux without Ollama → add
   `ocrmypdf` after Tesseract)
2. A new image type is common (e.g. handwritten notes → add a
   handwriting-specific model)
3. Throughput is too slow on existing layers (e.g. add a GPU
   acceleration backend)

The interface is `OCRResult` + the orchestrator in
`app/services/pdf_ocr.py:ocr_pdf_chain()`. Add a new function
(returning `OCRResult`), append it to the chain. The DB column
`extraction_method` is `VARCHAR(32)` so we have room for new
method names.

## Trade-offs and known limitations

- **No layout preservation.** All OCR layers return plain text.
  Tables, figures, and formatting are lost. The user gets the
  *content* but not the *visual layout*. A vision-capable tutor
  (llava, qwen2-vl) could in theory be fed the rendered page as
  an image, but that's not yet wired.
- **macOS Vision is fastest but Mac-only.** On Linux, the chain
  automatically skips Vision and goes Ollama → Tesseract.
- **Tesseract is slow on CPU.** A 200-page textbook can take
  30+ minutes. The `materials_ocr_timeout_seconds = 600` cap
  protects against runaway jobs.
- **Vision returns confidence scores** (per character) but we
  don't currently use them to reject low-quality results. A future
  improvement could filter pages with avg confidence < 0.5 and
  fall through to the next layer.

## Verification

Reproduce the OCR pipeline on a real image-only PDF:

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app
source venv/bin/activate

# Direct Swift CLI test
./bin/material_ocr uploads/some-upload/data/some-image-only.pdf | head -c 500

# End-to-end via the extractor
python3 -c "
from app.services.material_extractor import extract
with open('uploads/test-image-only.pdf', 'rb') as f:
    text, method = extract('test.pdf', f.read())
print(f'Method: {method}')
print(f'Length: {len(text) if text else 0} chars')
print(text[:200] if text else '(None)')
"

# Vision capability probe
python3 -c "
from app.services.model_capabilities import probe_model_capabilities, find_available_vision_model
for m in ['glm-5.2:cloud', 'llava:13b', 'qwen2.5:14b']:
    caps = probe_model_capabilities(m)
    print(f'  {m:25s} vision={caps.is_vision} family={caps.family or \"-\"}')
print(f'  find_available_vision_model() -> {find_available_vision_model()}')
"
```

## File reference

| File | Purpose |
|------|---------|
| `bin/material_ocr.swift` | Swift source for the macOS Vision CLI |
| `bin/material_ocr` | Compiled binary (gitignored) |
| `bin/build_ocr.sh` | Build script |
| `app/services/pdf_ocr.py` | 3-tier OCR orchestrator |
| `app/services/model_capabilities.py` | `/api/show` probe |
| `app/services/material_extractor.py` | `extract()` entry point |
| `app/services/material_context.py` | Tutor prompt integration |
| `app/config.py` | `materials_ocr_*` settings |
| `tests/test_model_capabilities.py` | 10 probe tests |
| `tests/test_ocr_extraction_chain.py` | 6 chain contract tests |