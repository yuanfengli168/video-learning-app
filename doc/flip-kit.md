# User Limit Overrides — The Manual Flip-Kit (Beta Operations Guide)

> **Status**: Living runbook. This IS the manual version of Todo #14's
> flip-kit — the operations doc for granting per-user limits during the
> invite-only beta (Stripe deferred to relaunch per A9).
> **First production use**: 2026-09-21 — jackyopenclaw.168@gmail.com flipped
> to 2GB per-file by the owner, manually.
> **Related**: `doc/limits-registry.md` (the design: override → tier default),
> `scripts/promote-admin.sh` (the sibling role-promotion script),
> `doc/roles-tiers-cheatsheet.md` (role ints — never renumber).

---

## The model in one line

```
effective_limit(user) = user override (if set) → else tier default (env var)
```

Two knobs, both stored on the `users` row, both **raw bytes** (not GB):

| Column | Controls | NULL means |
|---|---|---|
| `max_file_bytes` | max size of ONE uploaded video | PAID 1GB / ADMIN 20GB (env: `UPLOAD_MAX_FILE_PAID_GB` / `UPLOAD_MAX_FILE_ADMIN_GB`) |
| `storage_quota_bytes` | total video storage | PAID 25GB / ADMIN 100GB (env: `STORAGE_QUOTA_PAID_GB` / `STORAGE_QUOTA_ADMIN_GB`) |

**No restart needed.** The resolver (`app/services/upload_limits.py`) reads
the user row per-request; there is no cached limit to invalidate. The flip
takes effect on the user's next upload, and the `/usage` page (hard-refresh)
reflects it immediately — the card reads the same resolver the enforcement
uses (the anti-drift rule).

---

## The commands (copy-paste)

**DB path** (from `.env`): `/Volumes/Storage-Fast-NVMe/video_learning.db`

### Raise a user's per-file upload limit (e.g. to 2GB)

```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "UPDATE users SET max_file_bytes = 2147483648 WHERE email = 'THEIR@gmail.com';"
```

### Raise a user's storage quota (e.g. to 50GB)

```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "UPDATE users SET storage_quota_bytes = 53687091200 WHERE email = 'THEIR@gmail.com';"
```

### Verify the flip

```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "SELECT email, role, max_file_bytes, storage_quota_bytes FROM users WHERE email = 'THEIR@gmail.com';"
```

### Roll back to tier defaults

```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "UPDATE users SET max_file_bytes = NULL, storage_quota_bytes = NULL WHERE email = 'THEIR@gmail.com';"
```

`NULL` = "use the tier default" — the clean rollback.

### Cheat sheet: GB → bytes

| Limit | Bytes to use |
|---|---|
| 1 GB | `1073741824` |
| 2 GB | `2147483648` |
| 4 GB | `4294967296` |
| 10 GB | `10737418240` |
| 20 GB | `21474836480` |
| 25 GB | `26843545600` |
| 50 GB | `53687091200` |
| 100 GB | `107374182400` |

(Formula: GB × 1024 × 1024 × 1024 — binary GB, matching the code.)

---

## Safety notes (read before first use)

1. **The live app serves this DB while you write.** WAL mode allows one
   concurrent writer; a fast UPDATE is safe — but don't linger in the
   sqlite prompt with an open transaction. One command in, prompt closes.
2. **Never INSERT users rows by hand** — the app auto-creates rows on first
   login. If the email isn't found, have the user sign in once first, then
   flip (the same flow `promote-admin.sh` uses).
3. **Flip UP, never down silently.** Lowering someone's limit below what
   they're already storing is allowed by the schema (existing videos stay;
   new uploads are blocked) — but tell the user first. Surprises lose betas.
4. **These overrides are invisible to billing** — they're ops grants, not
   purchases. The Stripe build at relaunch will set these same columns via
   webhook; manual flips then become exceptional rather than routine.

---

## The 2GB-and-up caveat (watch during beta)

The chunked path handles any size up to the limit, but each 32MB chunk is
one HTTP PUT through the tunnel. At 2GB = 64 chunks; at sustained slow
uplinks a single chunk can approach Cloudflare's ~100s proxy timeout. The
per-chunk retry (3 attempts) covers blips, but **2GB+ on weak uplinks is
the one regime not yet watched live** — the "start at 1GB, raise later"
plan anticipated exactly this. If a 2GB+ upload fails oddly mid-chunk,
capture it (`scripts/incident-capture.sh` isn't needed — just note the
time + which chunk number) and check the sweeper/events table for the
session trail. Mitigation if it bites: reduce `UPLOAD_CHUNK_SIZE_MB` to
16 (env var — smaller chunks, more of them, shorter per-request time).

---

## Audit trail

The events table should log every flip (a TODO for the future
`promote-paid.sh`: write a `log_event` row per flip). Until then, log flips
in this file by hand:

| Date | Email | Change | By | Reason |
|---|---|---|---|---|
| 2026-09-21 | jackyopenclaw.168@gmail.com | max_file_bytes → 2GB | owner (manual) | owner account; first production use of the override infra |

---

## The future `promote-paid.sh` (spec for when it's built)

One command replacing the raw SQL (Todo #14's flip-kit):

```
bash scripts/promote-paid.sh their@gmail.com --role paid
bash scripts/promote-paid.sh their@gmail.com --file-limit 2GB --quota 50GB
bash scripts/promote-paid.sh their@gmail.com --reset-limits
```

Behavior: locate DB from `.env` (the promote-admin.sh pattern), verify the
user exists (sign-in-first flow), apply, verify, and write an events-table
row (`source='admin.flip'`, the email + the before/after values) so the
audit table above becomes automatic.