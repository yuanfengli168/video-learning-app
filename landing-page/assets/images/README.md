# Screenshot Assets

This folder holds all screenshots used by the landing page.

## Required files

Drop the following files here (PNG, 1600×1000 px recommended, 2x for retina):

| Filename | What to capture |
|---|---|
| `og-image.png` | 1200×630 — Open Graph card (used by Twitter, LinkedIn, etc. when shared). Show the logo + tagline + product mockup |
| `screenshot-upload.png` | Upload screen — drag-and-drop zone, file picker, course/section picker |
| `screenshot-transcript.png` | Video page with transcript visible — show click-to-seek (highlight one line) |
| `screenshot-mindmap.png` | Mindmap tab — full tree visible, with cursor hovering over a node |
| `screenshot-quiz.png` | Quiz tab — a question with one answer selected (green) |
| `screenshot-discuss.png` | Discuss tab — user message + AI response with timestamp citation |
| `screenshot-tools.png` | Tools tab — the WebM→MP4 plugin card, ideally with the "Last successful output" green box |
| `screenshot-dashboard.png` | (optional) Course dashboard — list of courses with videos |

## How to capture

1. Run the app locally: `bash scripts/start.sh`
2. Open Chrome DevTools → toggle device toolbar to a 1600×1000 viewport
3. Navigate to the screen you want
4. Hide any sensitive data (email, real video titles) — use generic "Sample Course" / "Lecture 1"
5. Use Cmd+Shift+4 (macOS) or the macOS Screenshot tool to capture
6. Or use a tool like [CleanShot X](https://cleanshot.com/) for nicer framing

## Tips for great screenshots

- **Use a real lecture** — Andrew Ng, 3Blue1Brown, etc. (familiar content is more compelling)
- **Use a clean theme** (light mode usually looks better in screenshots)
- **Crop to the content** — no browser chrome, no personal tabs visible
- **Show one feature per screenshot** — don't try to cram everything in one image
- **Annotate if helpful** — a single arrow or callout box can clarify what to look at (use Figma or Preview's markup tools)

## When you drop the files in

The HTML is already wired to look for these filenames — no code changes needed.
The placeholders will be replaced automatically by the JS in `assets/js/main.js`.

## Optimization

Before committing, run each PNG through:
- [TinyPNG](https://tinypng.com/) — 50-70% size reduction, no visible quality loss
- Or `pngquant --quality=65-80 file.png` on the command line

Target: each screenshot should be under 200 KB.
