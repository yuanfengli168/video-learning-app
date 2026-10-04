# Demo Videos

The promotion page has **two demo slots** (free tier + paid tier) that embed
short walkthroughs of CapySmart. Until the recordings are uploaded, each slot
shows an in-page placeholder card (no broken YouTube embed) — so the page
reads correctly before launch.

## How the slots are wired

Both demo slots live in `index.html` under the `#demo` section. For each slot,
there is:

1. A **placeholder card** (`.demo__placeholder`) — visible by default; shows
   `▶ Recording pending · 10-minute walkthrough`.
2. A **hidden iframe** (`.demo__video-wrap[data-demo-video]`, `hidden`) —
   contains the YouTube `<iframe>` once the video ID is filled in.

To go live with a demo, edit the corresponding slot in `index.html`:

```html
<!-- BEFORE (still showing the placeholder) -->
<div class="demo__placeholder" role="img" aria-label="Free-account demo video coming soon">
  ...
</div>
<div class="demo__video-wrap" data-demo-video hidden>
  <iframe src="https://www.youtube.com/embed/FREE_ACCOUNT_VIDEO_ID?rel=0&modestbranding=1" ...></iframe>
</div>

<!-- AFTER (placeholder removed, iframe unhidden, real ID pasted) -->
<div class="demo__video-wrap" data-demo-video>
  <iframe src="https://www.youtube.com/embed/AbCd1234XYZ?rel=0&modestbranding=1" ...></iframe>
</div>
```

Steps:

1. Upload the recording to YouTube (unlisted is fine — no need to publish publicly).
2. Copy the video ID from the URL `youtube.com/watch?v=XXXXXXXX`.
3. In `index.html`, find the slot (search for `FREE_ACCOUNT_VIDEO_ID` or
   `PAID_ACCOUNT_VIDEO_ID`).
4. Replace the `FREE_ACCOUNT_VIDEO_ID` / `PAID_ACCOUNT_VIDEO_ID` in the iframe
   `src` with the real ID.
5. Delete the entire `.demo__placeholder` block above that iframe.
6. Remove the `hidden` attribute from the iframe's parent `.demo__video-wrap`.

## Per-site recording tips

- **Length**: target ~10 minutes per recording; aim for a clear end-to-end
  walkthrough of one user journey, not a feature tour.
- **Audio**: the product requires audio (Whisper transcription). Include
  narration in the recording, or pair it with a clear voiceover.
- **Aspect**: 16:9, 1080p is fine. The `.demo__player` container auto-scales.
- **Branding**: open CapySmart with a clean account and a real-feeling course
  (Andrew Ng, 3Blue1Brown, etc.) — familiar content is more compelling.
- **Privacy**: hide any personal data — use generic course names, a free Gmail
  account for sign-in if needed, and blur or skip any screens with real emails.

## Optional: self-host the MP4 instead

If you'd rather host the MP4 yourself (no YouTube dependency), the page has
no MP4 slot — you'd need to add a `<video>` element in place of each
`.demo__placeholder` block. The CSS already styles `.demo__player` for this:

```css
.demo__player video { width: 100%; height: auto; display: block; aspect-ratio: 16/9; background: #000; }
```

Drop the MP4s into this folder (`assets/video/`) and reference them as
`assets/video/demo-free.mp4` / `assets/video/demo-paid.mp4`.