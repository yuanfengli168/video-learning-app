# Blocker

## blockers
- [july 6 2026] the incorrect fix for authkit, that login worked, but after 4 fixes not working
```
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

