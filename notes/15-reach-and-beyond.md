# Reach and beyond (Phases E and F)

Built 2026-10-08. Rishi asked to make new-stellar match and beat main Stellar; for this round
he chose **Reach + beyond** only, with all server code kept in `app.py`. Every phase was tested
in `smoke_test.py` and checked on new-stellar.stellarai.site with free calls only. No new
Python dependency anywhere.

| Phase | What | Main Stellar | new-stellar now |
|---|---|---|---|
| E1 | Install + notifications | PWA, VAPID push via a library | PWA (manifest, maskable icons), Web Push built on the standards (VAPID RFC 8292, aes128gcm RFC 8291) with `cryptography`; endpoints limited to real push services; quiet when a Stellar window is in front |
| E2 | Multi-device sync | SSE user events (one held connection per tab) | a Redis change counter per user, read every 3 s by visible tabs: no thread held, changes in ~3 s, interface state included |
| E3 | Bring your own key | Fernet-encrypted per-user key | the same, plus a free validity check before saving, last-4 hint only, used first for that user's turns and tools |
| F1 | Backup provider | none (a diagnostic report when every key fails) | OpenRouter's free tool-capable models answer a turn Gemini cannot; same prompt, history and tools; key rotation, model fallback; free models only |
| F2 | Background tasks | — | `/bg` or the composer's Background switch: 30 steps, autonomous, notification with a summary; finished project jobs announced |
| F3 | Documents | sandbox scripts | Word, Excel and PowerPoint extracted on the server (headings, tables, every sheet, slide notes), within zip-bomb limits; works without Docker |

## How each works

- **E1.** `/sw.js` handles `push` and `notificationclick` only (no fetch handler, no cache).
  The signing key is made beside the database (`vapid_private.pem`) or set with
  `VAPID_PRIVATE_KEY`. Triggers: a finished reply (titled "Scheduled task:" or "Done:" when it
  was one), a widget or interface waiting for an answer, a project job ending. Settings → App
  and notifications: turn on/off, send a test, install. A notification opens `/?chat=N`.
- **E2.** `_bump_sync(user)` runs on every message saved, chat made/renamed/deleted, reply
  start/end, interface state/event/update. `GET /api/sync` → `{v}`. The page refetches the chat
  list, the open chat and interface states when it moves, and once more 2 s later.
- **E3.** `users.gemini_key_enc` (Fernet, key in `STELLAR_ENCRYPTION_KEY` or `encryption.key`),
  `gemini_key_hint`. `gemini_keys()` puts the user's key first inside their turn; the model
  listing and the admin key board stay on `shared_gemini_keys()`.
- **F1.** `_openrouter_turn` runs when a turn produced nothing and the error is quota,
  overloaded, auth, transient or a missing model. Tool schemas come from the same functions the
  Gemini SDK reads (`FunctionDeclaration.from_callable_with_api_option` → JSON Schema). The
  reply is labelled **Backup · model**. The live server needs `OPENROUTER_API_KEY_n` in its
  keys.env for this to apply there. The test suite switches it off unless a test uses a
  stand-in, so a run spends no quota.
- **F2.** `args["background"]` → `BACKGROUND_TOOL_ITERATIONS` (30) and `BACKGROUND_NOTE`; six
  an hour per user. `_watch_jobs` in the reaper marks jobs done/failed/lost and notifies.
- **F3.** `document_text(path)` with `_read_docx`, `_read_xlsx`, `_read_pptx`; cached as
  `<file>.extracted.txt`; parts over 30 MB uncompressed or archives over 5,000 parts refused.

## Verified on the live site

- E1: service worker active, push key served, Settings section (the embedded browser denies
  notification permission, so delivery to a real device needs a normal browser or phone).
- E2: a rename made "from another device" appeared in the open tab after 3.0 s.
- E3: section shown; a malformed key refused (no real key was entered).
- F2: a background task ran three tool calls and summed up; the switch turned itself off.
- F3: a Word budget uploaded; "who owns the biggest cost, and the total?" answered correctly
  (Ananya, ₹11,700) by Lunarity from the extracted table.
- F1: exercised in the suite (stand-in), and once for real locally before the suite switched it
  off: an overloaded turn answered by `nvidia/nemotron-3-super-120b-a12b:free`.
