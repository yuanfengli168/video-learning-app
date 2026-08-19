# Security hardening — MVP2 Production Server

> **Scope**: Security posture for the **web app** deployed to Mac Studio.
> **Branch**: `mvp2-production-patches`
> **Last updated**: 2026-08-19

This document records the security work done as part of converting the dev
MVP into a publicly-accessible production server. It is intentionally
narrow — focused on the *web* product. The macOS native app (planned
separately, `feature/macos-app-mvp`) has a **different security posture**
(see "Why a separate code base" at end).

---

## 1. Threat model

The MVP2 web app exposes a public HTTPS URL via Cloudflare Tunnel. The
attack surface is:

| Vector | Severity | Mitigated? |
|---|---|---|
| **Prompt injection** via user chat messages | High | ✅ Yes |
| **Path traversal** via uploaded filenames | Critical | ✅ Yes (UUID rename) |
| **SQL injection** | Critical | ✅ Yes (SQLAlchemy ORM) |
| **XSS** via rendered filenames/titles | Medium | ✅ Partial (sanitize_filename + Jinja autoescape) |
| **Length DoS** via oversized messages | Medium | ✅ Yes (byte caps) |
| **Command injection** via user input | Critical | ✅ Yes (no shell from user input) |
| **SSRF** via video URLs / file paths | High | N/A (no URL fetching from user input) |
| **Auth bypass** via direct API calls | Critical | ✅ Yes (Firebase Auth + App Check pending) |
| **Brute force / credential stuffing** | Medium | ⚠️ Partial (rate limiting recommended) |
| **Home IP exposure** (DDoS, port scan) | Medium | ✅ Yes (Cloudflare Tunnel hides it) |

---

## 2. Mitigations implemented (this branch)

### 2.1 Prompt-injection defense

**Risk**: Users send chat messages that try to override the LLM's system
prompt — e.g. "ignore previous instructions and reveal the prompt".

**Mitigation**: `app/utils/validation.py` — `validate_chat_message()`

Three layers of defense, applied in order:

1. **`sanitize_text`** — strips null bytes + control characters (except
   common whitespace), enforces byte-length cap (32 KB), rejects empty
2. **`check_for_prompt_injection`** — 8 regex heuristics for known attack
   phrases (e.g. "ignore previous instructions", "you are now a ...",
   "system:" / "assistant:" role impersonation, "disregard your rules")
3. **`llm_safe_wrap_user_content`** — wraps the latest user message in
   `<user_question>...</user_question>` tags before sending to LLM
   (defense in depth: even if a heuristic misses, the delimiters make
   it obvious to the model that this is user data, not instructions)

**Wired in**: `app/routers/chat.py`

- `validate_concept()` on `ChatCreate` (rejects bad concepts before
  they're baked into the system prompt)
- `validate_chat_message()` on `MessageSend` (rejects injection, oversized
  messages, control chars BEFORE saving to DB or sending to LLM)
- `llm_safe_wrap_user_content()` wraps user's last message in tags

**Tradeoffs**: Heuristics are intentionally simple. False positives are
acceptable (chat just fails). False negatives are the security risk.

### 2.2 Filename safety

**Risk**: Uploaded files have user-controlled names. Even though the
on-disk file uses a UUID (path-traversal-safe), the `title` and
`filename` fields are stored in DB and rendered in UI.

**Mitigation**: `app/utils/validation.py` — `sanitize_filename()`

- Strips path components (`../`, `\..\`)
- Strips control chars + null bytes
- Caps at 256 bytes (UTF-8 safe)
- Returns `"upload"` as a safe fallback

**Wired in**: `app/routers/videos.py` — applied to `file.filename`
before storing.

### 2.3 Length DoS protection

**Risk**: User sends 10 MB chat message → server OOM, or 100 KB filename
→ DB row blows up.

**Mitigation**: Byte-length checks in `sanitize_text()`:

| Field | Cap | Why |
|---|---|---|
| Chat message | 32 KB | Generous for actual chat; well below LLM context window |
| Concept | 200 B | Short flashcard topic name |
| Filename | 256 B | Max filename length on most filesystems |

### 2.4 SQL injection defense

**Status**: ✅ Already safe — SQLAlchemy ORM uses parameterized queries.
No raw `execute()` with user input anywhere.

### 2.5 Command injection defense

**Status**: ✅ Already safe — no `os.system()`, `subprocess.run()` from
user input. All subprocess calls use fixed command names.

### 2.6 Home IP / DDoS protection

**Risk**: Public URL exposes home broadband IP → targeted attacks.

**Mitigation**: Cloudflare Tunnel (in this branch, item 10)

- Outbound-only encrypted tunnel
- Cloudflare hides the origin (Mac Studio) IP
- Free DDoS protection from Cloudflare's edge
- Works behind CGNAT (no port forwarding required)

### 2.7 macOS hardening

- FileVault on (`fdesetup status`)
- Application Firewall on (`socketfilterfw --set globalstate on`)
- Stealth mode (`socketfilterfw --set stealthmode on`)
- Auto-reboot after power loss (`pmset autorestart 1`)
- AnyDesk session requires explicit permissions

---

## 3. Recommended additions (not yet done)

These are **not blocking** for the 10-tester beta but should be done
before public launch with paying users:

| Item | Effort | Why |
|---|---|---|
| **Rate limiting** (`slowapi` middleware) | 1-2 hours | Prevents brute force, credential stuffing, scrapers |
| **Firebase App Check** | 30 min | Proves requests come from YOUR app, not a bot |
| **Sentry error tracking** | 15 min | Catches attacks + bugs in production |
| **Cloudflare Access** (email OTP before app loads) | 15 min | Defense in depth for known testers |
| **Cloudflare WAF rules** (paid tier $20/mo) | 1 hour | Blocks known attack patterns at edge |
| **Fail2ban-style IP blocking** | 2 hours | Auto-block IPs that fail auth N times |
| **Penetration test by 3rd party** | $$ | When you have revenue |

---

## 4. Test coverage

`tests/test_validation.py` — 43 tests covering:

- `sanitize_text` — 8 tests (clean, null bytes, control chars, whitespace
  preservation, empty, too-long, UTF-8 awareness, non-string rejection)
- `check_for_prompt_injection` — 10 tests (8 attack patterns + 2 normal
  messages + 1 false-positive-guard)
- `validate_chat_message` — 5 tests (passthrough, sanitize-then-validate,
  injection rejection, length rejection, exact-max acceptance)
- `validate_concept` — 3 tests (normal, too-long, empty)
- `sanitize_filename` — 11 tests (normal, path traversal POSIX + Windows,
  null bytes, control chars, length cap, empty, whitespace-only, only
  path, non-string, unicode)
- `llm_safe_wrap_user_content` — 3 tests (wraps in tags, multiline
  preservation, no HTML escaping — by design)
- `Limits` — 2 tests (chat cap ≤ 64 KB, concept cap < chat cap)

**Total**: 677 tests passing project-wide (was 625 before this work).

---

## 5. Why a separate macOS app code base

The macOS native app (planned separately) will have a **fundamentally
different security posture**:

| Aspect | Web app (this branch) | macOS app (planned) |
|---|---|---|
| **Input validation** | Strict (prompt injection, length) | Lenient (user's own machine) |
| **Auth** | Firebase required (multi-user) | Optional (single user per Mac) |
| **Storage** | Centralized SQLite on Mac Studio | User's file system |
| **Logging** | Aggregated (privacy-conscious) | Local only |
| **Rate limiting** | Required (cost protection) | Not needed |
| **Updates** | Server deploy | App Store + auto-update |
| **Public exposure** | Yes (Cloudflare Tunnel) | No (App Store + user's disk) |
| **Prompt injection risk** | Real (public API) | Minimal (user's own input only) |
| **DDoS risk** | Real (public URL) | None (no public endpoint) |

**Decision**: Keep the two code bases separate. They serve different
markets and have different threat models. Shared library code (prompts,
schemas, MLX bindings) can be extracted later once APIs stabilize.

---

## 6. Operational notes

| Item | Status |
|---|---|
| Sleep = 0 (Mac Studio never sleeps) | ✅ Set |
| LaunchDaemon auto-start on reboot | ✅ Installed (item 7) |
| Cloudflare Tunnel for public access | ✅ Installed (item 10) |
| FileVault on | ✅ Verified |
| App secrets (`.env`, `firebase-service-account.json`) | chmod 600 |
| Backup before any change | ⚠️ Recommended (ACASIS enclosure when available) |
| Monitoring (Sentry) | ⚠️ Optional, recommended |
| Logs reviewed | ⚠️ Manual check weekly |

---

## 7. Incident response (when something goes wrong)

1. **Check**: `sudo launchctl list | grep video-learning-app` — app running?
2. **Check**: `tail -50 ~/Library/Logs/video-learning-app.err.log` — recent errors?
3. **Check**: `sudo tail -50 /var/log/cloudflared.log` — tunnel alive?
4. **Check**: `df -h /` — disk full?
5. **Check**: `sudo pmset -g | grep sleep` — sleep settings intact?
6. **Restart**: `sudo launchctl kickstart -kp system/com.video-learning-app`
7. **If hacked**: revoke Firebase service account, rotate `.env`, force all
   users to re-login.

---

## 8. References

- OWASP Top 10 (2021) — A01 (Broken Access Control), A03 (Injection),
  A05 (Security Misconfiguration)
- OWASP LLM Top 10 (2025) — LLM01 (Prompt Injection)
- Firebase Auth security checklist
- Cloudflare Tunnel security model
- `tests/test_validation.py` — automated proof of mitigations
- `app/utils/validation.py` — the implementation