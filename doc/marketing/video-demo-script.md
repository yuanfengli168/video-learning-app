# Video Demo Script

> **Format:** Screen recording + voiceover (or text overlay)
> **Target length:** 3-5 minutes (sweet spot for LinkedIn/Twitter; RedNote can be 1-2 min cut)
> **Audience:** Self-learners, students, educators, devs
> **Goal:** Make them say "I need this" within 30 seconds

---

## 🎬 Pre-Recording Checklist

- [ ] Have a **2-hour sample lecture** ready (something with a clear topic — e.g. "Intro to Neural Networks")
- [ ] Pre-generate the materials so the demo is smooth (don't show "generating" wait times in the main cut)
- [ ] Have a **second video** ready that's only been **partially processed** (for the "in-progress" demo)
- [ ] Light/dark theme toggle on, decide which to use (recommend **light** for first 60% then **dark** for the "study at night" feel)
- [ ] Browser zoom at 110% for readability
- [ ] Notifications off, clean desktop
- [ ] Practice the click-through 2-3 times before recording

---

## 🎯 Story Arc (5 acts)

```
HOOK  →  PROBLEM  →  SOLUTION (the app)  →  "WOW" MOMENTS  →  CTA
 5s       15s            60s                    90s              20s
```

---

## ACT 1: HOOK (0:00 – 0:05)

**On-screen text (big, centered):**
> **You watched a 2-hour lecture. You remember 20%.**

**On-screen text:**
> **What if your videos could teach you back?**

**Cut to:** app dashboard

**Voiceover (if any):**
> "I built a tool that turns any lecture into a complete study kit. Watch."

---

## ACT 2: THE PROBLEM (0:05 – 0:20)

**Show 3 quick cuts (2-3 seconds each):**

1. **Screenshot of YouTube** with 47 tabs open and a 2-hour lecture paused at 1:23:00
2. **Screenshot of messy notes** (Apple Notes, Notion, whatever) — barely readable
3. **Screenshot of a quiz** you failed because you "studied" but didn't actually test recall

**Voiceover:**
> "We all do this. We watch, we nod, we forget. Notes don't help. Re-watching the whole thing doesn't help. We need to *test* ourselves — but who has time to make flashcards for a 2-hour lecture?"

**Cut to black. Beat.**

> "So I made an AI do it."

---

## ACT 3: THE SOLUTION — UPLOAD + TRANSCRIBE (0:20 – 0:50)

**Screen recording starts.** Click into a course → section → "Upload".

**Show:**
1. Drag-and-drop the 2-hour lecture file
2. Click "Upload"
3. **Fast-forward** through the processing (or show a 2-second "generating" spinner with the time counter ticking)
4. Cut to the **finished video page** — the transcript is on the left

**Voiceover:**
> "Drop in any video. The app transcribes it locally with Whisper — no cloud, no data leaks. In 30 seconds you have a searchable transcript with click-to-seek."

**Click on a word in the transcript.** Video jumps to that moment.

**On-screen callout:**
> "Click any line → video jumps to that moment. Try doing that on YouTube."

---

## ACT 4: THE "WOW" MOMENTS (0:50 – 2:30)

This is the meat. Show **4 killer features** in order, each ~20 seconds.

### ⭐ WOW #1: Mindmap (0:50 – 1:10)

**Click the Mindmap tab.**

**Show:**
- The mindmap rendering in real-time (zoom out so the full tree is visible)
- Click on a node → video jumps, topic banner appears at top
- Click a leaf node (which doesn't have a timestamp) → it walks up the tree to find the nearest ancestor timestamp

**On-screen callout:**
> "Every node is a clickable timestamp. Even leaf nodes — the AI finds the closest parent."

### ⭐ WOW #2: Quiz + Flashcards (1:10 – 1:30)

**Click the Quiz tab.**

**Show:**
- A multiple-choice question
- Click an answer → green/red feedback
- The question references a specific moment in the video (and you can click "jump to source")

**Click the Flashcards tab.**

**Show:**
- 5-10 flashcards with the "Teach me real-world usage" button
- Click it → the AI opens a chat that explains with concrete examples

**On-screen callout:**
> "Quiz tests recall. Flashcards link to a tutor chat. Stop re-reading — start testing."

### ⭐ WOW #3: Discuss (1:30 – 1:50)

**Click the Discuss tab.**

**Show:**
- Type: "What did the professor mean by 'gradient descent' at 23:15?"
- AI answers with a clear explanation + a clickable timestamp citation

**On-screen callout:**
> "Ask questions in plain English. The AI has the full transcript + summary + mindmap as context."

### ⭐ WOW #4: Tools — WebM → MP4 (1:50 – 2:10)

**Click the Tools tab.**

**Show:**
- The "WebM → MP4" plugin card
- Click "Run"
- Show the **indigo "Currently running…"** state (the new 2.1.0.3 fix!)
- Cut to the **green "Last successful output"** box
- Click "Re-Upload with MP4"
- Confirm the swap
- The video file size in the DB updates correctly (you can show the course page if needed)

**On-screen callout:**
> "Browser-recorded WebM? Convert to MP4 in 1 click. The app handles the format hell so you don't have to."

---

## ACT 5: THE "I MADE THIS FOR YOU" CTA (2:10 – 2:30)

**Cut to a clean screen showing the GitHub repo.**

**On-screen text (line by line, animated):**
> ✅ 100% open source (Apache 2.0)
> ✅ Runs on your laptop (M1 Mac, 16GB RAM — works)
> ✅ No subscriptions, no rate limits
> ✅ 633 tests, 92% coverage — production ready
> ✅ 5-minute install

**On-screen text (big):**
> **github.com/yuanfengli168/video-learning-app**

**On-screen text (smaller, below):**
> ⭐ Star it if it helps. 🐛 Issues welcome. 🤝 PRs even more welcome.

**End card** (3 seconds, fades in):
> Built by [@yourhandle] · Apache 2.0 · 2026

---

## 🎙️ Voiceover Script (full text, ready to record)

```
You watched a two-hour lecture. You remember twenty percent.

We all do this. We watch, we nod, we forget. Notes don't help.
Re-watching the whole thing doesn't help. We need to TEST
ourselves — but who has time to make flashcards for a two-hour
lecture? So I made an AI do it.

[show upload]

Drop in any video. The app transcribes it locally with Whisper
— no cloud, no data leaks. In thirty seconds you have a
searchable transcript with click-to-seek.

[show mindmap]

Click any node — the video jumps. Every node is a clickable
timestamp. Even leaf nodes — the AI finds the closest parent
if the leaf doesn't have its own.

[show quiz + flashcards]

Quiz tests recall. Flashcards link to a tutor chat. Stop
re-reading — start testing.

[show discuss]

Ask questions in plain English. The AI has the full transcript,
summary, and mindmap as context.

[show tools]

Browser-recorded WebM? Convert to MP4 in one click. The app
handles the format hell so you don't have to.

[end card]

It's open source. Apache 2.0. Runs on your laptop. No
subscriptions. Five-minute install. Star it if it helps.
```

**Total runtime:** ~2:30 (perfect for LinkedIn native video, Twitter, RedNote)

---

## 📐 Shot List (for the editor / yourself)

| Timestamp | Shot | Notes |
|---|---|---|
| 0:00 | Big text on black | "You watched a 2-hour lecture. You remember 20%." |
| 0:05 | YouTube screenshot w/ 47 tabs | Screenshot, blur edges |
| 0:08 | Messy notes | Apple Notes screenshot |
| 0:12 | Failed quiz | Generic quiz screenshot |
| 0:15 | Black screen + "So I made an AI do it." | 1.5s beat |
| 0:20 | Drag-and-drop upload | Screen record, real speed |
| 0:28 | Transcribe progress | Speed up 4x |
| 0:32 | Transcript ready | Real speed, click a word → video jumps |
| 0:50 | Click Mindmap tab | Show full tree, then click a node |
| 1:10 | Click Quiz tab | Show 1 question, click answer |
| 1:18 | Click Flashcard "Teach me…" | Show AI chat opens |
| 1:30 | Click Discuss tab | Type a question, show answer |
| 1:50 | Click Tools tab | Click "Run", show in-progress state |
| 2:00 | Show success state, click "Re-Upload" | Confirm swap |
| 2:10 | GitHub repo screen | End card text |
| 2:25 | Fade to logo + handle | 3s |

---

## 🎞️ Cut-Down Versions

### For Twitter / X (1:30, vertical or square)
- 0:00–0:20: Hook + problem (compressed)
- 0:20–0:50: Upload + transcript
- 0:50–1:10: Mindmap (the "wow")
- 1:10–1:30: End card

### For RedNote (1:00, vertical, fast cuts)
- 0:00–0:10: Hook (text only)
- 0:10–0:30: Upload + transcript (real speed)
- 0:30–0:50: Mindmap click-through
- 0:50–1:00: End card + handle

### For LinkedIn (2:30, 16:9, full version)
- Use the full script as-is

### For GitHub README (GIF loop, 15s)
- Just the mindmap click-through looped
- Add as a GIF in the README

---

## 💡 Pro Tips

1. **Show, don't tell.** The first 5 seconds need movement, not text.
2. **Subtitles are mandatory.** 80% of social media is watched on mute.
3. **End with one clear CTA** — "Star the repo" is the easiest ask.
4. **Add the GitHub URL on screen for the entire last 10 seconds.** Not just at the end — it gets clipped.
5. **Record at 1440p / 60fps** if possible. YouTube + LinkedIn downscale, but the source quality matters.
6. **Use a real lecture from a famous course** (Andrew Ng, 3Blue1Brown) — instant credibility.
7. **B-roll**: cut to your face for 1-2 seconds at the start, then back to screen. Builds trust.
8. **Background music**: lo-fi / chill, ~20% volume. Don't compete with voiceover.
