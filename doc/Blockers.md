# Blocker

## blockers
- [july 9 2026] transcript panel showing wrong scroll position on page load (MVP2.0 #2)
- [july 6 2026] the incorrect fix for authkit, that login worked, but after 4 fixes not working

---

### ✅ RESOLVED [july 9 2026] — Transcript panel showing 00:10 instead of 00:00 on page load

**Symptom:** On page load, the transcript panel was scrolled to show ~00:10 at the top instead of 00:00. The active highlight was on the correct line (00:00), but it was not visible because the panel was scrolled past it.

**Root cause: `lineEl.offsetTop` was measured from the wrong ancestor.**

The `scrollToTop()` function used `lineEl.offsetTop` to compute the scroll target. `offsetTop` is relative to the element's `offsetParent` — the nearest ancestor with `position != static`. The transcript container (`overflow-y-auto`) has `position: static`, so the transcript lines' `offsetParent` was the page `<body>` (or the main layout div), **not** the container.

For line 0, `offsetTop ≈ 500px` (the height of the header + video player + banner above the transcript). Setting `container.scrollTop = 500 - 4 = 496` scrolled the panel to content offset ~496px, which corresponded to line ~16 (≈ 00:10), not line 0 (00:00).

**Why the unit tests didn't catch it:**
The `FakeLine` mock manually set `offsetTop = 0, 30, 60` — as if the container were the `offsetParent`. In the real browser, the same values would be 500, 530, 560. The tests passed because the mock bypassed the exact thing that was broken.

**Why multiple fix attempts failed:**
1. **"Hover gate"** — I suspected `isHovered=true` was preventing the scroll. Fixed it to force-scroll on init. The panel still scrolled to the wrong line because the underlying position calculation was wrong.
2. **"`pendingForce` stale closure"** — I fixed the rAF's force flag being captured at schedule time. Still showed 00:10 because the scroll target itself was wrong.
3. **"Event handler passes event object as forceScroll"** — Fixed the handler wrapping. Still showed 00:10 for the same reason.
All three were real bugs, but none was the root cause of the scrolling to 00:10.

**Actual fix: use `getBoundingClientRect()` instead of `offsetTop`.**

```js
// Before (broken):
const desired = lineEl.offsetTop - 4;

// After (correct):
const containerRect = container.getBoundingClientRect();
const lineRect = lineEl.getBoundingClientRect();
const lineInContent = container.scrollTop + (lineRect.top - containerRect.top);
const desired = lineInContent - 4;
```

`getBoundingClientRect()` always returns viewport-relative positions. Subtracting the container's top from the line's top gives the line's position relative to the container's visible area. Adding `container.scrollTop` converts that to the absolute position in the scrollable content.

**Files changed:**
- `app/static/js/transcript-follow.js` — `scrollToTop()` rewritten to use `getBoundingClientRect`; plus the 3 secondary fixes (init force-scroll, `pendingForce` variable, event handler wrapping)
- `tests/test_transcript_follow.mjs` — `FakeLine` and `FakeContainer` mocks updated to use `getBoundingClientRect` with the container at viewport y=500; two new regression tests added

**Commit:** `05525ee` (MVP2.0 branch, part C)

---

still not working, but I can choose account on pop up now, but after I choosed 1 account, the link in pop up is this url: "https://video-learning-app-3cf41.firebaseapp.com/__/auth/handler?state=AMbdmDkpfJiJZY7FdIr0qBSD7kACNRacQvgsdOuhttUvvRamuSeRHD1JB9SToANTs6lQXHYfmx3MO2GSoiLthuXNP8pX86HGpPyqe63hzp_edRPROpwQUGjBmdbq71xbS3w4CUEeUfsqijJ4m6VwoWGL0cJdyu-AoOfVyY_C1j2yEuoee0AdtOMiJ2tOZGqr4B05rB-_VitWhleYeqP9YCR1CRkgzreyo34_FRZgKRi5NWRkosc_bJnU4n__XTxk_7zOxH9gcHHmvVMc19NUrqrwdXA1wH9hHqpMg5h4kEVof92TCmyt_YiL-67xjKN7NUYW8pN7AMtHb6LjBEAZn80ypA&iss=https%3A%2F%2Faccounts.google.com&code=4%2F0AdkVLPz5xv1F6bjs6mAu6_2iLuF70CKuFGpAcyBcggsAokbOaGcgJYL9u2bbpk4wXCmriw&scope=email+profile+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.profile+openid+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email&authuser=0&prompt=none" and it loads loads, there are nothing but white on popup, and few seconds later the popup disappeared, and I am not loggedin.
```

### ✅ RESOLVED — Real root cause and real fix

**Why it looked like the code was fine but login never worked:**
The session cookie from a login *before* the security middleware was added was still valid, so the app appeared to work. Once that cookie expired or was cleared, every fresh login attempt silently failed. Nobody noticed because the failure was invisible in the server logs.

**Two broken security headers in `app/middleware.py`:**

#### 1. `Cross-Origin-Opener-Policy: same-origin` → fixed to `same-origin-allow-popups`

Firebase popup auth works like this:
1. Parent page (`localhost:8000`) opens a popup → `accounts.google.com`
2. User picks their Google account ✅
3. Google redirects popup to `firebaseapp.com/__/auth/handler?code=...`
4. That handler calls `window.opener.postMessage(result, 'http://localhost:8000')` to return the token

With `COOP: same-origin`, the browser **severs `window.opener`** for all cross-origin popups. So step 4's `postMessage` target was `null`. The popup hung blank for ~5 seconds then closed. The parent window never received the token, so the user stayed on `/login`.

**Fix:** `COOP: same-origin-allow-popups` — keeps `window.opener` intact for popups *you* open, while still blocking other origins from navigating into your context.

#### 2. `Cross-Origin-Embedder-Policy: credentialless` on `/login` → fixed to *absent*

Firebase Auth popup mode also creates a hidden iframe at `firebaseapp.com/__/auth/iframe` on the *parent* page to relay auth state. That iframe has no `Cross-Origin-Resource-Policy` header. Under `COEP: credentialless`, Chrome blocked it with `ERR_BLOCKED_BY_RESPONSE (reason: "origin")`. Without the iframe, the popup flow could never complete even after fixing COOP.

**Fix:** Skip `COEP` on the `/login` route only (`app/middleware.py` checks `request.url.path != "/login"`). All other pages still get `credentialless`. The login page is the only page that runs Firebase Auth.

**Files changed:**
- `app/middleware.py` — two header changes above
- `tests/test_security_headers.py` — updated/added tests that document both bugs so they can never silently regress

**Why so many incorrect intermediate fixes:**
The real errors (`window.opener is null`, `ERR_BLOCKED_BY_RESPONSE`) only appear in the *popup window's* DevTools console — not on the parent page. The parent page only sees `auth/popup-closed-by-user`, which looks like a user action. The wrong hypothesis ("authorized domain missing", "needs signInWithPopup directly") sent the investigation in circles.

