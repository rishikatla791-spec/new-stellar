# File uploads

Attach files to a message with the paperclip, by dragging them onto the
window, or by pasting (a screenshot straight from the clipboard works).
The model sees them, and so does the sandbox.

## The journey of one file

1. **Upload, before sending.** The moment a file is chosen it goes to
   `POST /api/chats/<id>/uploads` and appears as a chip above the input.
   Uploading early means a big file is already on the server by the time
   you finish typing, and Send is instant. Until the message is sent the
   attachment row has `message_id = NULL`: it is *pending*, and the chip's
   x deletes it (`DELETE .../uploads/<att>`).
2. **Two copies are written.**
   - `uploads/u{user}_c{chat}/<name>`, the canonical copy. The app serves
     it from here and builds the model's view from here. The sandbox cannot
     touch it.
   - `sandbox_runs/.../uploads/<name>`, which the container sees as
     `/lab/uploads/<name>`. This is a working copy. The agent may unzip it,
     edit it or delete it, and the original is unaffected.
3. **Send.** The query carries `attachment_ids`. The server checks that
   each one is in *this* chat, belongs to *this* user and is still pending,
   and then links them to the new message. From then on they cannot be
   deleted (409), because the transcript refers to them.
4. **The model's view.** `_attachment_parts` turns each file into parts:
   - images, PDF, audio and video are sent **as the bytes themselves**
     (`inline_data`). Gemini reads those natively.
   - text and code files are sent **as their text**, up to 200k characters.
   - anything else (zip, docx, xlsx, ...) gets a note: *"this file is in the
     sandbox at /lab/uploads/..., use lab_execute to open it"*.
   Every file also gets a one-line note with its name, type and size, so
   the model can refer to it even when the bytes are not included.

## Why inline bytes and not the Files API

Gemini has a Files API: upload once, then refer to the file by a handle.
N1kky's Stellar uses it. Two properties make it awkward here:

- a file belongs to **the API key that uploaded it**. Stellar rotates
  across a pool of keys when one hits its quota, so every key switch
  means uploading the file again. N1kky's code re-uploads on rotation.
- it **expires after 48 hours**, so returning to a chat from last week means
  its files are gone from Google's side.

Inline bytes have neither problem. Any key can send them, at any time. The
cost is a ceiling of about 20 MB per request, which is why there is a
budget.

## Keeping requests small

| Setting | Value | Why |
|---|---|---|
| `UPLOAD_MAX_BYTES` | 25 MB | one file |
| `UPLOAD_MAX_PER_MESSAGE` | 10 | files per message |
| `ATTACH_INLINE_BUDGET` | 15 MB | bytes shown per request, under Gemini's ~20 MB |
| `ATTACH_HISTORY_MESSAGES` | 3 | how many recent messages resend their files |
| `ATTACH_TEXT_MAX` | 200k chars | text shown inline; the rest is in the sandbox |

The history window matters for follow-ups. "Now make it blue" about a
picture sent two messages ago still has the picture. A picture from forty
messages back is only a note. Otherwise every turn in a long chat would
resend every file.

Newest files get the budget first. A file that does not fit is not dropped
silently: its note says it was too large to show and points at the
sandbox copy.

## Serving files back safely

A user's upload is served from Stellar's own domain, so an uploaded
`page.html` containing `<script>` would run *as Stellar* if it were served
as a page. It could read the chat or make requests with your session.
So:

- only images, PDF and plain text (`.txt`, `.md`, `.json`) display inline.
  Everything else, **HTML and SVG included**, is sent as a download
  (`Content-Disposition: attachment`).
- `X-Content-Type-Options: nosniff` stops the browser from deciding a
  "text" file is really HTML.
- `Content-Security-Policy: default-src 'none'; ... sandbox` means that even
  if something does render, it cannot run a script.
- a file is only found through its chat, and the chat through its owner.
  Another user gets a 404, not a 403, so they cannot even learn that an
  id exists.

File names are cleaned (`_safe_filename`) so `../../escape.txt` cannot climb
out of its folder, and a clash gets a short random suffix (`notes_3f9a.txt`),
so two `notes.txt` files are both kept.

## Verified

- A drawn PNG (a green triangle labelled ZEPHYR) was dropped onto the page
  and sent with "what is in this picture?". The model described the
  triangle and read the word.
- A zip holding `notes/briefing.txt` was attached with "what is the launch
  code inside?". The model cannot read a zip directly, so it used
  `lab_execute` twice (list, then unzip and cat) and answered
  `ORCHID-7431`.
- `smoke_test.py` covers the rest: ownership, traversal, duplicates, size
  limits, pending versus sent, HTML downloaded rather than rendered, and
  what the model is and is not shown.

## Beyond N1kky's version

- inline bytes instead of per-key Files API handles, so key rotation and
  old chats work without re-uploading
- a canonical copy the sandbox cannot damage, plus a working copy it can
- a bounded history window and byte budget instead of resending everything
- ownership checks and no-script serving on every download
