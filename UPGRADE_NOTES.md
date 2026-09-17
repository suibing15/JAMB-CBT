# UPGRADE NOTES — JAMB-CBT

This file documents everything changed in this pass, and flags what's
still worth fixing next. Written for whoever maintains this repo
going forward (probably future-you).

---

## 1. Native JAMB-style CBT engine

**Before:** Candidates had to fully "finish" one subject (submit +
land on a "Subject Finished" screen) before the next subject even
became reachable. Each subject had its own independent timer.

**Now:** A single `UTMESession` (in `exams/models.py`) attaches ALL
of a candidate's registered subjects at once. The exam screen
(`templates/exam_portal/utme_exam.html`) shows subject tabs the
candidate can click at any time — mid-question, mid-subject, whatever
— exactly like the real JAMB CBT software. There is ONE shared
countdown (default 130 minutes = 2h10m, the real UTME duration,
configurable via `JAMB_TOTAL_DURATION_MINUTES` env var) covering the
whole sitting. One "Submit Exam" button grades every subject at once.

The timer is **server-verified**: the browser polls
`/exams/utme/heartbeat/` every 20 seconds, and the server's own
`remaining_time()` (based on `started_at` + `total_duration_minutes`)
is the source of truth. A candidate editing their local JS clock
cannot extend their exam.

---

## 2. Mega-server remote license/kill-switch — REMOVED

Deleted entirely:
- `middleware/license_guard.py`
- `middleware/license_lock.py`
- `utils/license_state.py`
- `utils/heartbeat_runner.py`
- `utils/mega_client.py`
- the now-empty `middleware/` and `utils/` directories

This code sent an HMAC-signed heartbeat to an external `MEGA_URL`
every 60 seconds and could remotely 403 the entire application if
that server said "locked". It was **not** wired into `MIDDLEWARE` in
the version I inherited, so it was dormant — but the capability
existed and could have been silently re-enabled. It is now gone
completely; there is no remaining code path that lets a third party
remotely disable this application.

---

## 3. PDF generation — now fully streamed, zero disk writes

**Before:** `accounts/views.py::download_pdf` wrote a PDF to
`MEDIA_ROOT/candidates/<phone>/registration_form.pdf` on every single
call, and saved that path to `Candidate.registration_form`
(a `FileField`). `management/views.py::download_all_pdfs` then read
those saved files back off disk to zip them.

**Now:**
- `accounts/views.py::_build_registration_pdf_bytes()` builds the PDF
  entirely in a `BytesIO` buffer and returns raw bytes — nothing
  touches disk.
- `download_pdf` streams those bytes straight into the
  `HttpResponse`.
- The `Candidate.registration_form` FileField has been **removed**
  from the model (migration `accounts/0006`) since nothing writes to
  it anymore.
- `management/views.py::download_all_pdfs` now regenerates every
  candidate's PDF on the fly and writes each one into an in-memory
  `zipfile.ZipFile(BytesIO())`, then streams the finished ZIP. No
  temp files, no `all_registration_forms.zip` left on disk.
- The exam result-slip PDF (`exam_portal/views.py::generate_result_pdf`)
  and the chat-export PDF were already writing into `response`
  directly — those were fine, just confirmed and left as-is.

This matters most once you deploy anywhere with ephemeral disk
(Render, Railway, most container platforms) — previously generated
PDFs would silently vanish on every redeploy anyway, so persisting
them to local disk was never reliable in the first place.

---

## 4. Supabase-ready

`jamb_system/settings.py` now reads:

- **`DATABASE_URL`** — a standard Postgres connection string.
  Point this at Supabase's **pooled (port 6543, PgBouncer
  "Transaction" mode)** connection string, not the direct 5432
  connection, since that's what's meant for many short-lived web
  connections like Django/Gunicorn workers. Parsed via
  `dj-database-url`; `DISABLE_SERVER_SIDE_CURSORS` is set
  automatically because PgBouncer transaction mode doesn't support
  Django's server-side cursors.
  - No `DATABASE_URL` set → falls back to local SQLite, so `git
    clone` + `pip install` + `runserver` still works with zero setup
    for local development.

- **Supabase Storage (S3-compatible) for media** — set
  `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL` (your Supabase
  Storage S3 endpoint), `AWS_S3_REGION_NAME`, and this app will route
  all `ImageField`/`FileField` uploads (passport photos, signatures,
  question images) through `django-storages`' S3 backend instead of
  local disk. Toggle explicitly with `USE_SUPABASE_STORAGE=true` if
  you want it on/off independent of the bucket-name check.

You still need to run:
```bash
python manage.py migrate
```
against the Supabase database once `DATABASE_URL` is set, and create
a Supabase Storage bucket + policy before turning on
`USE_SUPABASE_STORAGE`.

---

## 5. Security hardening added

- **Rate limiting** (`django-ratelimit`) on:
  - candidate PIN login (`10/min` per IP, `8/5min` per phone number)
  - registration (`6/hour` per IP)
  - registration/result PDF downloads (`20/min` per IP)
  - "find my record" lookup (`15/min` per IP — this endpoint lets
    anyone search by phone number, so it doubles as an
    account-enumeration guard)
  - public chat send endpoints (`30/min` per IP)
- **Brute-force PIN lockout**: after 5 failed PIN attempts on the
  *same phone number* (regardless of source IP — so an attacker
  can't just rotate IPs to keep guessing one candidate's 6-digit PIN),
  that phone number is locked for 15 minutes.
- **Session security**: session key is rotated (`cycle_key()`) on
  every successful login to defeat session-fixation attacks; cookies
  are `HttpOnly`, `Secure` (outside DEBUG), `SameSite=Lax`; session
  expires after 4 hours or on browser close.
- **`DEBUG` now defaults to `False`.** The old code had
  `DEBUG = True` hardcoded — meaning full stack traces, settings, and
  SQL were exposed to anyone on the live production site. It's now
  `DJANGO_DEBUG` (env var), defaulting to `False`, and the app
  **refuses to start** in that mode without a real `DJANGO_SECRET_KEY`
  set (previously there was a hardcoded insecure fallback key that
  would have quietly run in production too).
- **`ALLOWED_HOSTS` no longer defaults to `"*"`** in production —
  must be set explicitly via `DJANGO_ALLOWED_HOSTS`.
- **HSTS, secure cookies, X-Frame-Options, nosniff, XSS filter** all
  enabled outside `DEBUG`.
- **Upload size caps** (`DATA_UPLOAD_MAX_MEMORY_SIZE`,
  `FILE_UPLOAD_MAX_MEMORY_SIZE`) added — previously unbounded, so a
  malicious multi-megabyte "passport photo" or bulk XLSX upload could
  exhaust server memory.
- **Admin URL obscuring actually applied.** The old `jamb_system/urls.py`
  *defined* a `SECURE_ADMIN_URL = "secure-admin-4932/"` variable in a
  comment implying it was in use, but the `path()` call right below
  it still mounted the admin at the plain `admin/admin.site.urls`
  path — the obscured URL was never actually wired up. This is now
  fixed: the admin is genuinely only reachable at
  `/<DJANGO_ADMIN_URL or secure-admin-4932>/`, and `/admin/` now
  404s. (Reminder: this is obscurity, not real access control — it
  doesn't replace strong passwords/MFA on the admin account itself.)
- **Calculator XSS/arbitrary-code risk reduced**: the in-page
  scientific calculator used raw `eval()` on whatever the candidate
  typed. Replaced with a regex allow-list (`^[0-9+\-*/.\s]+$`) before
  evaluating, so it can no longer execute arbitrary JS even if
  someone tampers with the input field via devtools.
- **`django-ratelimit`'s block page** now shows a clean, friendly
  "429 Too Many Requests" page (`jamb_system/views.py`) instead of a
  raw Django exception/traceback when `DEBUG=False`.

---

### Round 2 — everything from the "still worth doing" list, done

All six items flagged in the first pass have now been addressed:

1. **`records` app removed entirely.** Deleted the folder, removed
   from `INSTALLED_APPS`. Confirmed via `manage.py check` and a
   fresh migrate that nothing else depended on it.

2. **`typing_state` moved off the bare in-process dict** into
   `django.core.cache` (`exam_portal/views.py`), which is Redis-
   backed via `REDIS_URL` in production — this now works correctly
   across multiple Gunicorn workers. Also added an 8-second TTL so a
   crashed/closed admin tab doesn't leave "Admin is typing..." stuck
   on forever.

3. **Two real, serious chat vulnerabilities found and fixed** while
   addressing the "no visitor authentication" note (this turned out
   to be more serious than originally flagged):
   - `fetch_messages` used to trust a client-supplied `?contact=`
     query param to decide which thread to return — meaning anyone
     could read anyone else's entire support chat just by knowing or
     guessing their phone number, with zero authentication. Fixed by
     introducing `ChatMessage.thread_token`, a random value generated
     server-side and stored in the *visitor's own Django session* —
     never sent back by the client as a guessable value. Verified
     with a real cross-session test: Visitor B spoofing Visitor A's
     phone number in the query string now gets an empty result.
   - The Django admin's "Reply" button had a related bug: because
     `visitor_contact` is (intentionally) a read-only field on the
     admin form, the value never actually reached `save_model()` on
     submit, so it fell back to "the single most recent message from
     ANY visitor in the whole system" — meaning a reply could
     silently attach to a completely unrelated person's thread. Fixed
     by carrying the `reply_to` id through as a hidden POST field
     (see `templates/admin/exam_portal/chatmessage/change_form.html`)
     and resolving the correct thread from it at save time.
   - The old `chat_api` / `chat_api_send` endpoints were also removed
     entirely (not patched) — they returned *every* message in the
     system to *anyone*, unauthenticated, and were referenced nowhere
     in the UI. Pure dead, dangerous surface area.

4. **Real automated tests added.** 20 tests across all four apps
   (`accounts`, `exams`, `exam_portal`, `management`), covering: PIN
   generation, streamed-PDF regression protection, the native
   all-subjects-at-once exam flow end to end, per-subject grading,
   flagging, the chat thread-isolation fix (including the admin-reply
   fix), rate limiting, and the bulk-download permission fix. All 20
   pass consistently, including under `manage.py test --shuffle`
   across multiple random seeds (this caught and fixed a real
   test-isolation bug of its own: rate-limit cache state leaking
   between test classes — every test class now calls `cache.clear()`
   in `setUp`).

5. **PIN entropy increased** from 6 to 8 digits (1,000,000 →
   100,000,000 possible values). Also added a second, independent
   layer of brute-force protection: an IP-level anomaly guard
   (`_register_failed_attempt_for_ip` in `exam_portal/views.py`)
   that locks out an IP once it has failed logins against more than
   15 *distinct* phone numbers within a 30-minute window — this
   specifically catches the "patient distributed guesser who stays
   under the per-phone lockout threshold for any single number"
   pattern that the per-phone lockout alone couldn't.

6. **Vestigial `ExamSubject.duration_minutes` field removed.**
   Confirmed via full-codebase grep that nothing in the exam engine,
   admin, or templates depended on it (only test fixtures did, and
   those were updated), then removed the field and generated/applied
   the migration.

### Round 2 — additional issues found during a full codebase scan

Beyond the checklist above, a full scan (every `.py` file, every
template's `{% url %}` reference cross-checked against every
urlconf, a full page-by-page render smoke test, and a `pyflakes`
pass) turned up a few more things worth fixing:

- **A completely dead, insecure duplicate login flow.**
  `exams/views.py` and `exams/urls.py` contained a second `exam_login`
  view with no rate limiting, no lockout, a redirect to a URL name
  (`exam_dashboard`) that doesn't exist anywhere, and a template
  reference (`exams/exam_login.html`) that also doesn't exist. This
  urlconf was never actually `include()`-d anywhere in
  `jamb_system/urls.py`, so it was completely unreachable — but it
  was exactly the kind of trap that could confuse a future editor
  into "fixing" the wrong login view. Removed both files.
- **A relative-path bug in the registration PDF's logo/watermark.**
  `accounts/views.py` referenced `"static/img/logo.png"` as a bare
  relative path, which only resolves correctly if the process's
  current working directory happens to be the project root — not
  guaranteed under Gunicorn/systemd/most real deployments. It was
  silently swallowed by a `try/except`, so the practical symptom
  would just be "the logo watermark sometimes doesn't show up,"
  never an actual error. Fixed to resolve through `settings.BASE_DIR`
  instead, matching the pattern already used correctly elsewhere in
  the codebase.
- **Unused imports cleaned up** across the whole codebase (verified
  with `pyflakes`; only two intentional, explicitly-commented stub
  imports remain in `management/admin.py` and `management/models.py`,
  both dead-app-removal leftovers kept only so those files still
  import cleanly as empty stubs).
- **Bare `except:` clauses tightened** to `except Exception:` in the
  PDF-generation code (bare `except:` also silently catches
  `KeyboardInterrupt`/`SystemExit`, which is bad practice even where
  the intended behavior — "skip drawing an optional image if it's
  missing" — was otherwise correct).

### Fixed in the first pass (for reference)
1. **Three separate, conflicting `Question`/`ExamYear`/`ExamSubject`
   model definitions.** `accounts/models.py` and
   `management/models.py` each defined their own copies of these
   models, but **only the versions in `exams/models.py` were ever
   actually used** by `exam_portal` and `management`'s views. The
   dead copies (and their admin registrations in
   `management/admin.py`) have been removed. This was a real trap:
   editing "the wrong" `Question` model would have silently done
   nothing.
2. **Admin URL obscuring was defined but not applied** — fixed.
3. **`DEBUG = True` hardcoded in production** — fixed.
4. **Insecure hardcoded `SECRET_KEY` fallback that would run even in
   production** — fixed; app now refuses to start without one
   outside DEBUG.
5. **No rate limiting anywhere** — fixed.
6. **PDFs and the bulk-zip both wrote to local disk indefinitely,
   with no cleanup** — fixed via streaming.
7. **Calculator `eval()` on untrusted input** — fixed with an
   allow-list.
8. **No `.gitignore`** — added.

---

## 7. Environment variables reference (new/changed)

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django secret key | **required outside DEBUG** |
| `DJANGO_DEBUG` | `true`/`false` | `false` |
| `DJANGO_ALLOWED_HOSTS` | comma-separated hosts | empty in prod (must set) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | comma-separated origins | sensible defaults for onrender/ngrok |
| `DJANGO_SECURE_SSL_REDIRECT` | force HTTPS redirect | `true` outside DEBUG |
| `DJANGO_ADMIN_URL` | obscured admin path | `secure-admin-4932/` |
| `DATABASE_URL` | Supabase/Postgres connection string | falls back to SQLite |
| `DATABASE_SSL_REQUIRE` | require SSL to DB | `true` |
| `REDIS_URL` | cache backend (rate limiting, chat typing indicator, login lockouts) | falls back to local memory |
| `USE_SUPABASE_STORAGE` | enable S3-compatible media storage | auto-on if `AWS_STORAGE_BUCKET_NAME` set |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_STORAGE_BUCKET_NAME` / `AWS_S3_ENDPOINT_URL` / `AWS_S3_REGION_NAME` | Supabase Storage credentials | — |
| `JAMB_TOTAL_DURATION_MINUTES` | shared UTME sitting length | `130` |

---

## 8. What's genuinely still outstanding

Everything flagged in the original "still worth doing" list has now
been done (see Round 2 above). What's left is smaller and lower
priority:

- **Live chat visitor identity is still self-reported.** A visitor
  can type any name/phone number when starting a chat, and it's
  recorded at face value (though the *thread itself* is now properly
  isolated per-browser-session — see the fix above — so this is no
  longer a data-leak risk, just a "the admin can't fully trust the
  displayed name/number" caveat). Low priority for a support inbox.
- **CI is not set up.** The test suite exists and passes reliably,
  but nothing runs it automatically on push/PR yet. Worth wiring up
  a simple GitHub Actions workflow (`manage.py test`) if this repo
  gets more than one contributor.
- **Test coverage is solid for the core flows** (registration, the
  native exam engine, chat security, admin permissions) but doesn't
  yet cover every corner — e.g. the bulk-question Excel upload path,
  the exam-year/subject management CRUD forms, or the live-chat
  polling/typing-indicator endpoints directly. Extend as those areas
  change.
