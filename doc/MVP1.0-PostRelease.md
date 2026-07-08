# MVP1.0 Post Release
- this one mainly focused on creating todos on the errors, I found on functions from MVP1.0, 
- but can also generate new idea for future, though this must be in mvp<future.versionNumber>.md as well



## MVP1.0 Functionalities: 

> Mirror of what `doc/MVP1.0-successfullyFinished.md` says is shipped, broken into what users see + what the backend does. Use this as the "as built" reference when triaging issues below.

| # | Layer | Feature | Status | Where it lives | Notes |
|---|---|---|---|---|---|
| 1 | Auth | AuthKit (Google + Email) on login page | ✅ Shipped | `app/templates/login.html` | Frontend uses Firebase Auth UI; backend uses Firebase Admin SDK |
| 2 | Auth | httpOnly session cookie (`fb_token`) | ✅ Shipped | `app/auth/session.py` | 1-hour max-age, `samesite=lax`; expires silently — see Optimization #3 |
| 3 | Auth | `get_current_user` dependency on every protected route | ✅ Shipped | `app/auth/dependencies.py` | 401 → frontend redirect handled in `base.html` |
| 4 | Course | Create / list / rename / delete courses | ✅ Shipped | `app/routers/courses.py` | Owner-scoped by `uid` |
| 5 | Section | Create / list / rename / delete sections inside a course | ✅ Shipped | `app/routers/courses.py` | |
| 6 | Video | Local upload (mp4/avi/mov/mkv/webm/m4v, ≤2 GB) | ✅ Shipped | `app/routers/videos.py` | Filename is stored, file goes to `uploads/` |
| 7 | Video | Whisper transcription (tiny/base/small/medium/large-v3) | ✅ Shipped | `app/services/transcription.py` | Background job, progress + ETA via `app/jobs.py` |
| 8 | Video | Per-sentence timestamped transcript | ✅ Shipped | `app/services/transcription.py:78-85` | `{start, end, text}` segments, see Optimization #1 (no auto-scroll) |
| 9 | Video | Click-to-seek from transcript timestamps | ✅ Shipped | `video.html` (`seekTo`) | Plays after seek |
| 10 | Video | Transcript search w/ live highlight + prev/next nav | ✅ Shipped | `video.html` (`searchTranscript`, `searchNavigate`) | Match counter, scrollIntoView center |
| 11 | Generation | One-click "Generate Materials" (summary, mindmap, quiz, flashcards, topic_timestamps) | ✅ Shipped | `app/services/llm.py` | `temperature=0`, `seed=42` → deterministic per transcript |
| 12 | Generation | Ollama at `http://localhost:11434`, model `glm-5.2:cloud` | ✅ Shipped | `app/config.py` | See Optimization #4 (needs zh-CN/zh-TW default) |
| 13 | UI | Tabbed right pane: Summary / Flashcards / Quiz / Mindmap | ✅ Shipped | `video.html` (`switchTab`) | Cached in `contentCache` to avoid re-fetches |
| 14 | UI | Summary tab auto-loads on page open | ✅ Shipped | `video.html:137` | See Optimization #2 (currently re-shows "Generate" on re-login) |
| 15 | UI | Clickable mindmap (inline + fullscreen) with ancestor fallback | ✅ Shipped | `video.html` (`jumpToTopic`, `findTopicTimestampWithAncestors`) | Toast on no-timestamp |
| 16 | UI | Mindmap controls: zoom ±, fit, drag-pan, scroll-zoom, Ctrl+0 reset | ✅ Shipped | `video.html` (`attachMindmapInteraction`) | markmap d3-zoom disabled in `0eb3878` |
| 17 | UI | Sidebar search (real-time filter, "No matches" placeholder) | ✅ Shipped | `base.html` (`filterSidebarCourses`) | |
| 18 | UI | Mobile sidebar hamburger / close toggle | ✅ Shipped | `base.html` | |
| 19 | Chat | "💡 Teach me real-world usage" on flashcards | ✅ Shipped | `video.html`, `app/routers/chat.py`, `app/services/chat.py` | Creates a `ChatSession` per click |
| 20 | Chat | Chat history page (`/chat-history`) with search, continue, delete | ✅ Shipped | `app/templates/chat_history.html` | |
| 21 | Infra | Local SQLite + SQLAlchemy 2.0; `Base.metadata.create_all()` | ✅ Shipped | `app/database.py` | No Alembic yet (deferred to MVP2) |
| 22 | Infra | Local FS storage (`uploads/`, `storage/`, both gitignored) | ✅ Shipped | `app/config.py` | |
| 23 | Infra | 218 tests, 96% coverage | ✅ Shipped | `tests/`, `scripts/test.sh` | Floor is 96% — see commit-message rule in Optimization header |
| 24 | Deferred | YouTube / URL downloader (`yt-dlp`) | ⏭️ Deferred to MVP2 | — | Marked "(Future)" in `design.md` §5 |



## Optimization: 
- before you start: for each topic, please do following, a) discuss and articulate your approach; b) typically use at least 2 commits for each, first is to implement the changes, second is to add tests and maintain 96% overall code base test coverage, but as high as possible coverage for this topic. c)if any tests failed, fix it; so a and b have to have, c depends on whether any tests need to be fixed, and if other thing also needed, can add d) e) etc.
- [optional]when you have a, b, c ... commits for same topic follow the pattern like: a) "Implementation of Transcript scrolling and highlighting functions with video - part A, implementation"; b) "Implementation of Transcript scrolling and highlighting functions with video - part B, tests"; c)"Implementation of Transcript scrolling and highlighting functions with video - part C, fix tests";

1. the transcript is not scrolling with the video, when video is at 1 minute, it still at 0 minute. 
   1. few options:
      1. like coursera move and scroll and move highlight for each entence
      2. highlight change from the transcript viewport on each sentence, but only scroll when reaching out the transcript viewport, (if not understand, can ask me)
   2. I think we can have both options with a dropdown selection list so user can select favorite style, but default can be 2nd one. The postion of these list can be somewhere beside: '📜 Transcript', but can discuss
   3. this might be a testing feature, and might be removed in future. so 1st let's think some monitoring way to see which one does users like most, second design it as a component that are easy to remove and not affecting resize other components etc.
2. when I generated materials and I logout and login again, and stay on this page: http://localhost:8000/video/e3025dcd-2342-46b3-98cf-d81352e195ee, the Summary still shows the Generate material button, but it actually needs to show the previous summary that already generated. 
3. so when I leave my laptop open for a really long time, I open I can't see my summary because I somehow logeout, BUT this Log out status is not shown, so might confuse others who not familiar with this, and if re login, then will see summary and video. 
4. I need a default for the characters for Chinese like Simplified chinese or Traditional Chinese, now I think if video had chinese in mandarine the transcript is showed in traditional chinese characters, but I want to have a default option for user to choose, (The UI can be done later for future MVP, but now I need something in Config files before I run the backend, etc.) Not familiar with this, so lets discuss.
5. No loading sign etc after clicking 'Sign in' and before the google popup is shown.
6. no loading sign when clicked on Tabs, summary, flashcards, etc, 

## new idea: (No implementation on these, just discuss, and it should be updated in new markdown file for future mvp docs)
1. I would like the LLM to discuss transcript. and guide us to potential part of video: 
   1. for example I came back to video and there is a new tab after summary and before flashcards
2. Telemetry for the transcript-follow experiment (Optimization #1) — count how many users pick "Smart" vs "Always scroll" and whether they later switch. Candidate design: a single `POST /api/telemetry` endpoint accepting `{event, payload}`; a tiny `telemetry.js` shim that calls it; opt-in localStorage buffer so we can flush on next page load if a request fails. Defer to MVP1.1+; for now the dropdown's `change` handler is the single hook point, so wiring it in later is a one-line change.