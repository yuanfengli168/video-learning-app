# MVP0.2 — Course Materials (PDF / .md / .txt / .zip as LLM context)

> **Status:** Shipped (5 commits, 726 backend tests pass)
> **Branch:** `mvp-mobile-pocket-v0.1`
> **Scope:** Backend + Mac web UI + iOS read-only mirror
> **Author:** brainstormed with GitHub Copilot, decisions ratified by user
> **Last updated:** 2026-07-30

---

## TL;DR

The user can now upload reference materials (PDF / Markdown / plain text /
ZIP) on the **Mac web app**, then choose which ones the AI tutor should see
as additional context for each video. The iOS app is a **strict read-only
mirror** — it shows the user's selection and lets them read the extracted
text, but all authoring happens on the Mac.

## Design decisions

### Why Mac-only authoring, iOS-only reading?

- **Files on phones are fiddly.** A 200-page PDF over the local network on a
  phone-sized screen with no progress UI is a bad experience. The Mac has
  a full file picker, a stable network, and a much larger viewport.
- **The "context budget" decision is a thinking activity.** Choosing which
  PDF belongs with which video is exactly the kind of curation that
  benefits from a real screen and a keyboard.
- **iOS stays a fast, focused learner surface.** No upload UI = no "the
  upload is stuck" support burden. The iOS app just reads.

### Why origin-gating?

iOS native URLSession does NOT send an `Origin` header. Mac browser
requests always include `Origin` (even for simple requests, not just CORS
preflights). So checking `Origin is not None` is a clean browser-vs-native
signal. We use it to reject iOS upload/delete attempts with a 403 +
"upload from your Mac".

### Why per-video selection instead of per-section?

A section can span many videos that cover different sub-topics. A
single reference PDF might be relevant to one video but noise for
another. Per-video selection lets the user be precise. The Mac UI's
"available pool" defaults to:

1. Materials uploaded at the section level (visible to all sibling videos)
2. Materials uploaded directly to a sibling video

So a section-level upload appears in every sibling's picker, but only
the ones the user ticks actually get into the prompt.

### Why inline materials in the tutor prompt (vs RAG / vector search)?

- **MVP cost discipline.** The MVP already has Ollama running with a 1M
  context window (`glm-5.2:cloud`). Inlining up to 200K chars of selected
  materials + the 60K-char transcript + the existing summary/quiz/etc is
  well within budget. RAG adds Pinecone / pgvector / FAISS — overkill for
  v0.2.
- **Citation transparency.** The user can see exactly which files are in
  the prompt via the Mac picker. No "the model must be retrieving this
  from somewhere" mystery.
- **Small corpus, big context.** Most users will have <10 materials per
  video. Even at 50K chars each, that's 500K — still under 1M.
- **Truncation is honest.** When the total exceeds the cap, the materials
  selected first (oldest) are kept, the rest are dropped, and the UI badge
  tells the user. No silent "this material is in there but only the first
  10%" surprises.

If we need RAG later (huge material libraries, per-section indexing),
that's MVP0.3.

### Why a separate `text/plain` endpoint?

`GET /api/materials/{id}/text` returns `Content-Type: text/plain` (not
JSON). Two reasons:

1. The extracted text can be 50K+ chars. JSON-quoting that doubles the
   on-wire bytes and forces both ends to escape/unescape.
2. The iOS viewer uses `String(data: data, encoding: .utf8)` — direct
   text decode, no `JSONDecoder` round-trip.

The test suite pins the `Content-Type` so any future change to JSON is
caught.

---

## Backend surface

### New tables

```python
class PocketMaterial(Base):
    id              str (uuid) PK
    user_id         str FK
    section_id      str FK → sections.id ON DELETE SET NULL  (nullable)
    video_id        str FK → videos.id   ON DELETE SET NULL  (nullable)
    filename        str
    size_bytes      int
    mime_type       str
    storage_path    str          # relative path under settings.upload_dir/materials/<user>/
    status          str          # processing | ready | failed
    extracted_text  text         # nullable; populated after extraction
    char_count      int          # nullable; len(extracted_text)
    error_message   str          # nullable; populated on failed extraction
    created_at      datetime
    updated_at      datetime

class PocketVideoMaterial(Base):
    id           str PK
    video_id     str FK → videos.id         ON DELETE CASCADE
    material_id  str FK → pocket_materials.id ON DELETE CASCADE
    created_at   datetime
    UNIQUE (video_id, material_id)
```

### New endpoints

| Method | Path                                | Auth | Origin-gated | Notes |
|--------|-------------------------------------|------|--------------|-------|
| POST   | `/api/materials`                    | ✅    | ✅ (Mac)     | multipart form: `file`, `section_id?`, `video_id?` |
| GET    | `/api/materials`                    | ✅    | —            | query: `section_id?`, `video_id?`, `status?` |
| GET    | `/api/materials/{id}`               | ✅    | —            | metadata only |
| GET    | `/api/materials/{id}/text`          | ✅    | —            | plain text body |
| DELETE | `/api/materials/{id}`               | ✅    | ✅ (Mac)     | cascades PocketVideoMaterial |
| POST   | `/api/materials/{id}/link`          | ✅    | ✅ (Mac)     | re-link section/video |
| GET    | `/api/videos/{id}/materials`        | ✅    | —            | `{selected_ids, available}` |
| PUT    | `/api/videos/{id}/materials`        | ✅    | —            | body `{material_ids: [...]}` |

### Settings

```python
materials_max_total_bytes_per_user: int = 200 * 1024 * 1024  # 200 MB
materials_max_file_bytes: int = 50 * 1024 * 1024            # 50 MB per file
materials_default_scope: str = "section"                    # future-proofing
```

### Extraction rules

- **`.pdf`** → `pypdf.PdfReader`, concatenated page text.
- **`.md`, `.markdown`, `.txt`** → decoded as UTF-8, returned as-is.
- **`.zip`** → walk the archive, skip vendored dirs (`node_modules`, `.git`,
  `__pycache__`, `target`, `build`, `dist`, `.venv`, `venv`, `.next`,
  `.cache`, `.idea`, `.vscode`) and binary extensions (image / video /
  audio / archive / font). 500-file / 200MB-uncompressed cap. If no text
  is extracted, returns a friendly `"No readable text found in this
  archive."` string so the UI badge still says "ready" (not failed).
- **Anything else** → status=`failed`, error_message="Unsupported file
  format", returns 415 on upload.

### Per-material / per-video char caps

```python
MAX_CHARS_PER_MATERIAL = 50_000        # ~12K tokens
MAX_CHARS_TOTAL_MATERIALS = 200_000    # across all selected for one video
```

When the cap is exceeded, the OLDEST-selected materials are kept, the
rest are dropped. The UI badge shows "📄 3 materials · 150K chars"
(only included) and "5 more not in context" so the user knows the budget
is the issue, not a missing file.

---

## Mac web UI

### Course page (`course.html`)

Each section gets a collapsible **📄 Materials** panel below the video
list:

- Upload button (PDF / .md / .txt / .zip picker)
- Per-material row: filename, size, char count, status badge (ready /
  processing / failed), delete button
- Count badge in the summary (`(0)` → `(3)`)

### Video page (`video.html`)

New **📄 Materials** tab (next to Tools) with:

- "N materials selected · K chars in context" summary line
- Checkbox list of available materials (from the section + sibling
  videos)
- "Save selection" button → `PUT /api/videos/{id}/materials`
- "Clear" button (resets checkboxes; user must still click Save)
- "View" button on each row → inline viewer sheet with the extracted
  text in a scrollable monospaced box (no nav away from the page)

The Materials tab is the **source of truth** for what the iOS tutor will
see. Any change here flows down on the next /m/snapshot.

---

## iOS read-only mirror

### Video model (`SnapshotModels.swift`)

```swift
struct Video: Codable, Identifiable, Hashable {
    // ... existing fields ...
    let selectedMaterials: [String]  // NEW, MVP0.2
    let updatedAt: Date
}
```

The `init(from:)` decoder uses `decodeIfPresent` so an iOS user
upgrading the app **before** their next sync still decodes old
snapshots without the field (defaults to `[]`).

### New models (`MaterialModels.swift`)

```swift
struct VideoMaterialItem: Codable, Identifiable, Hashable {
    let materialId: String
    let filename: String
    let sizeBytes: Int
    let charCount: Int?
    let addedAt: Date
}
struct VideoMaterialsResponse: Codable {
    let videoId: String
    let selectedIds: [String]
    let available: [VideoMaterialItem]
}
```

### New API methods (`APIClient.swift`)

- `fetchVideoMaterials(videoId:) -> VideoMaterialsResponse`
- `fetchMaterialText(materialId:) -> String` (parses text/plain body)

### New view (`MaterialsPanel.swift`)

Shown above the Start button in `TeachMeView`:

- Header: "Materials in context · N · K chars"
- Selected materials: ✓ + filename + size + chars + tap to view
- Unselected materials: hidden under a `DisclosureGroup` ("N more not in
  context") so the user knows the pool exists but isn't distracted

`MaterialViewerSheet`: scrollable monospaced extracted-text view, copy /
paste enabled (`.textSelection(.enabled)`).

### Badge on the video detail page

The "Teach me" button shows a small "📄 N" pill when materials are
selected, so the user knows materials are in scope without opening
TeachMe first.

---

## Tutor prompt integration

`app/pocket/jobs.py::_do_generate()` now calls
`build_materials_section(db, video_id, user_id)` before invoking
`tutor.generate_chunks(...)`. The returned string is appended to the
existing user prompt after the transcript / summary / quiz / flashcards /
mindmap slots. Example appended section:

```
USER-UPLOADED MATERIALS (selected for this video):
These are the user's reference materials. Use them as authoritative
context alongside the video transcript. If the materials mention a
concept that's NOT in the transcript, surface it in your teaching.

--- paper.pdf ---

[page 1 text]
[page 2 text]
...

--- notes.md ---

[markdown content]
```

If `materials_section` is empty (nothing selected), the prompt is
unchanged — no overhead.

---

## Test suite

### New tests (30 added in this MVP, 0 removed)

`tests/test_materials.py` — 25 tests covering:
- `detect_kind` / `extract` for PDF / md / txt / zip / unknown
- Upload: success, iOS-blocked, requires section/video, auto-link to
  video, unsupported format (415), per-user total cap (413)
- List, get, get-text, delete (cascade), delete-from-iOS-blocked,
  re-link
- Per-video selection: set, replace, other-user material rejected (403)
- Available pool includes section-level + sibling-video materials
- `material_context.build_materials_section`: empty, with selection,
  truncation metadata
- `/m/snapshot` includes `selected_materials` field

`tests/test_materials_ios_contract.py` — 5 tests pinning the
backend ↔ iOS JSON contract:
- `/api/videos/{id}/materials` returns exactly the keys the iOS
  `VideoMaterialsResponse` / `VideoMaterialItem` expect (rejects
  silently-dropped field renames)
- `/api/materials/{id}/text` returns `Content-Type: text/plain`
- `/m/snapshot` always includes `VideoOut.selected_materials` (even when
  empty) so the iOS `decodeIfPresent` fallback is never masking a bug

### Manual iOS verification

- `xcodegen generate` picks up the new `.swift` files via
  `generateEmptyDirectories: true`
- `xcodebuild -scheme PocketMVP -destination "id=569DFEB9..."` →
  **BUILD SUCCEEDED**
- `xcrun simctl install` + `launch` on iPhone 17 sim (UDID
  `569DFEB9-AF44-4FC9-A755-EEB448FCD4B0`): app launches, no
  error/fault/crash entries in `log show --last 30s --predicate
  process=PocketMVP`
- Screenshot confirms LoginView renders normally

### Regression status

- 726 of 727 backend tests pass
- The 1 failure is **pre-existing** and unrelated (`test_whisper_picker
  ::test_transcribe_endpoint_accepts_smart_turbo_pick` — same failure
  before this MVP started, see `test_whisper_picker.py:708`)

---

## Commits in this MVP

| # | Hash | Subject |
|---|------|---------|
| 1 | `e79ca56` | backend models, endpoints, extraction, tutor integration |
| 2 | `f50ac7e` | Mac web UI for upload + per-video picker |
| 3 | `(iOS)`  | iOS read-only mirror + selection sync |
| 4 | `2d45ba8` | test suite + iOS manual verification |
| 5 | `(this)`  | docs (CHANGELOG + this doc) |

---

## What's out of scope (parked, not built)

- **RAG / vector search.** Current MVP inlines 200K chars of materials per
  video. If a course has 100+ PDFs, we'll need pgvector or FAISS.
  → MVP0.3+
- **iOS upload UI.** The Mac is the authoritative authoring surface for
  v0.2. iOS upload is parked unless user feedback demands it.
- **Material re-extract / re-process on upload.** If a user edits the
  file on disk and re-uploads, that's a new material (new uuid).
  Re-processing the existing one is a v0.3 nice-to-have.
- **Cross-user material sharing.** The owner-only rule is enforced
  server-side; sharing a material to a different account is deferred.
- **Material previews on iOS.** We render the extracted text in a
  monospaced scroll view, not a rendered PDF / Markdown. PDFKit adds
  ~5MB to the binary and the extracted text is the canonical source the
  LLM reads anyway. If user feedback wants a prettier view, MVP0.3.

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Large PDF extraction crashes | Low | `MAX_EXTRACTED_CHARS_PER_FILE = 500_000` cap in `material_extractor.py` |
| User uploads 100 PDFs (5 GB) | Medium | `materials_max_total_bytes_per_user = 200 MB` cap; 413 on overflow |
| Tutor prompt too long (over Ollama context) | Low | `MAX_CHARS_TOTAL_MATERIALS = 200_000` cap with truncation + UI badge |
| iOS offline → stale `selected_materials` | Inherent | The next /m/snapshot picks up changes; manual pull-to-refresh in iOS already exists |
| `Origin` header missing on Mac (e.g. service worker) | Low | Origin check returns 403 with a clear message; user reports the issue |
| `pypdf` version drift | Low | Pinned `pypdf>=5.0.0` in requirements.txt; contract test catches schema breaks |
| iOS app shipped before backend deploy | Medium | `Video.init(from:)` uses `decodeIfPresent` — old snapshots still decode (defaults to `[]`) |

---

## Definition of done — ✅ all met

- [x] User uploads a PDF on Mac → row visible in section's Materials panel
- [x] User selects the PDF for a specific video on Mac → check saves
- [x] iOS user sees "📄 1" badge on Teach me button
- [x] iOS user opens TeachMe → "Materials in context" panel shows the PDF
- [x] User taps the PDF → extracted text shown in a viewer
- [x] User starts a Teach job → the tutor's prompt includes the PDF text
- [x] User deletes the PDF on Mac → iOS badge disappears on next sync
- [x] iOS user tries to upload anything → 403 "Mac web app only" (origin-gated)
- [x] All 726 backend tests pass; iOS BUILD SUCCEEDED; no crash on launch
- [x] CHANGELOG entry + this doc + plan update