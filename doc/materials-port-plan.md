# TODO — Port Materials Feature to mvp2 (PAID feature, week-2 of launch)

> **Created**: 2026-09-12, during launch prep
> **Status**: 📋 STAGED — execute AFTER the 9/15 launch + soft-launch stabilizes
> **Source**: `mvp-mobile-pocket-v0.1` branch (MVP0.2, shipped there 2026-07-30)
> **Destination**: `mvp2-production-patches` (or its post-launch merge into main)
> **User decision (2026-09-12)**: materials is the flagship week-2 improvement
> for the trial cohort — NOT in the launch.

---

## Why this is a big win, staged deliberately

The mobile branch shipped a mature "bring your own materials" feature:
users upload PDF/.md/.txt/.zip, the system extracts text (3-layer OCR
fallback for image-only PDFs: macOS Vision Swift CLI → Ollama vision
models → Tesseract), the user selects which materials each video's tutor
can see, and the discussion/tutor prompt inlines up to 200K chars of
selected material text.

**Why it converts trial users:** "upload your lecture slides, ask the
tutor about them" is the retention hook for exactly our audience —
students with course PDFs. It's also naturally tier-aligned: PAID chain
(glm-5.2:cloud, 1M context) holds the 200K material budget comfortably;
FREE (Groq compound-mini) cannot — so gating materials to PAID is not
artificial, it's technical reality.

**Why NOT in the launch (settled 2026-09-12):**
- Touches the chat prompt path — the most fragile pipeline, just
  hardened for rate limits; no new variables 3 days before go-live
- New upload attack surface (50MB files, ZIP walking) needs a security
  pass in context of the public internet
- Swift OCR binary must be built on the Mac Studio (new host, launch week)
- A week-2 feature launch gives the trial cohort a visible "always
  improving" moment and a second A/B datapoint

---

## Port checklist (in execution order)

### Phase 1 — read-only survey (30 min, no code)
- [ ] Re-read `doc/MVP0.2-materials.md` + `doc/ocr-strategy.md` on the
      mobile branch (decisions: inline-not-RAG, per-video selection,
      honest truncation, origin-gating)
- [ ] List every file: `git diff main...mvp-mobile-pocket-v0.1 --stat`
      and identify iOS-only vs portable
- [ ] Check drift: mobile branch forked ~2026-07-30; mvp2 gained
      channels/LLM-chains/analytics since — note any conflicts
      (especially chat.py prompt assembly)

### Phase 2 — backend port (~1 day)
- [ ] Cherry-pick: `app/models/material.py` (+ migration in
      `app/database.py` — two new tables, PocketMaterial +
      PocketVideoMaterial; consider renaming Pocket→ prefix on port)
- [ ] `app/schemas/material.py`, `app/routers/materials.py`,
      `app/routers/video_materials.py`,
      `app/services/material_extractor.py`,
      `app/services/material_context.py`
- [ ] `app/services/pdf_ocr.py` + `bin/material_ocr.swift` +
      `bin/build_ocr.sh` (Swift binary is NOT in git — build on host)
- [ ] requirements.txt: `pypdf`, `pytesseract` (+ PyObjC bits if
      pulled separately)
- [ ] **Wire the context builder into mvp2's chat path**: mvp2's
      `build_video_system_prompt` (app/services/chat.py) is where the
      materials section gets appended — the mobile branch's tutor
      prompt differs; port the *builder*, adapt the *call site*
- [ ] **Capability gate**: PAID-only per the decision — either
      require_capability(Capability.UPLOAD_VIDEO)-style (reuse
      UPLOAD_VIDEO) or add a dedicated Capability.MANAGE_MATERIALS to
      the roles matrix; PAID+ADMIN yes, FREE no
- [ ] **Channel-content question (settle at port time)**: can users
      attach materials to admin-curated catalog videos? User-scoped
      ownership (already the branch's design) suggests YES is safe —
      materials are private to the uploader; but ratify with user

### Phase 3 — Mac web UI (~half day)
- [ ] Course-page materials panels (upload + list + status badges) —
      from the branch's `course.html` section; adapt to mvp2's
      current course page (it gained rename buttons etc. since fork)
- [ ] Video-page discussion picker: which materials in context +
      "📄 N materials · XK chars / M more not in context" badge
- [ ] Capability-gate all upload/delete UI for FREE users
- [ ] Skip iOS mirror entirely (mobile branch keeps its own)

### Phase 4 — launch to trial cohort
- [ ] Tests port (the branch has 200+: test_materials.py,
      test_ocr_extraction_chain.py, test_material_context if separate)
- [ ] Build Swift OCR binary on the Mac Studio: `bash bin/build_ocr.sh`
- [ ] Live-test the OCR chain on a scanned PDF on the prod host
- [ ] Announce to the 20 trial users ("new: bring your own materials")
- [ ] Watch: chat latency with 200K chars inline (measure!),
      token burn vs the 50/7h + 100/wk PAID quota display

### Open questions for the user (ratify at port time)
1. Rename `PocketMaterial` → `Material` on port? (Pocket prefix is
   mobile-branding; mvp2 web users never saw "Pocket")
2. Materials on admin-curated videos: allowed (uploader-scoped) or
   personal-course-only?
3. Include ZIP uploads at launch of the feature, or PDF/md/txt first
   (ZIP adds the vendored-dir-walking complexity)?

## Measurement plan (ties into admin analytics)

Before announcing, add to the structure-analytics dashboard:
- materials per user, chars per video-chat, OCR-vs-text extraction ratio
- discussion engagement (sessions, msgs) with vs without materials
  attached — this is the A/B: "does grounded discussion feel smarter?"
- If the data says yes → materials becomes the PAID-tier headline in
  the pricing page copy (replaces generic "more powerful AI")