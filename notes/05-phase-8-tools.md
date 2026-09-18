# Phase 8: the rest of the tool suite

Phases 4 and 5 gave the agent four tools: the time, a page, a web search,
and a sandbox to run code in. Phase 8 adds the eight that turn it from
something that answers into something that produces. This note explains
each one the way it was explained before building: what it does, how it
works underneath, and where our design leaves the reference.

## The shape of every tool, again

Nothing changed in the machinery from Phase 4. A tool is a plain Python
function with type hints and a docstring; the SDK turns those into the
schema the model sees, so the docstring is the instruction manual and
deserves real care. Every tool takes `status`, the line the user watches
while it runs. The loop in `_generate_turn` calls it, stores the result in
`tool_calls`, and feeds the result back as the next request. Tools never
raise: a failure comes back as text the model can read and react to.

Three new pieces of plumbing sit under the eight tools:

- **Where files go.** `_outputs_dir(user, chat)` is a folder per chat under
  `outputs/`, and `/api/outputs/<chat>/<name>` serves it, after checking
  the logged-in user owns that chat. The reference had one shared folder
  behind a public `/view/` URL: anyone who could guess a name could fetch
  the file. Ours cannot be guessed into.
- **How a tool calls the model itself.** Some tools need the model for
  their own work (planning a deck, watching a video, drawing). They use
  `_tool_model_call`, which applies the Phase 7 rules: first usable key,
  block and rotate on quota, fall back to a second model. The reference
  copied that loop into every tool by hand; one copy, one set of bugs.
- **How a file name from the model is resolved.** `_resolve_chat_file`
  looks in the chat's outputs and its sandbox workspace and refuses
  anything that resolves outside them, so `../../keys.env` is not a file.

## The eight tools

### generate_image

Asks a Gemini image model for a picture, saves it to the chat's outputs,
and returns the Markdown `![alt](/api/outputs/...)`. The chat renderer now
understands image syntax and shows it inline. Reference images from the
chat's files can be attached for variations. It is the one tool this
machine could not demonstrate today: image requests are a separate quota,
and the free tier gives each key very few per day, or none.

### make_presentation

Two model calls, then no model at all. First the deck is planned as
structured JSON, with `response_schema` forcing the exact shape (title,
subtitle, and per slide a title, bullets, speaker notes and a description
of a visual). Then python-pptx writes real slides: a themed title slide,
one slide per entry with an accent rule, bullets as paragraphs and notes
as speaker notes, a footer with page numbers. The reference asked the image
model to paint every slide as a single picture with the text baked in,
which is unreadable, uneditable and burns ten image requests a deck. Ours
is editable in PowerPoint and free of image quota unless `illustrate=True`
adds a picture per slide, generated in a thread pool.

### analyze_youtube_video

Gemini can watch a YouTube video from its URL: the link goes in as a
`file_data` part with an optional start and end offset, and the question
follows it. That is the whole trick, and it is why a summary with
timestamps comes back in a few seconds. Search uses the YouTube Data API
when a key is present and otherwise falls back to Tavily restricted to
youtube.com, so search works without an extra key.

### send_self_email

SMTP over SSL to Gmail (or any server via `SMTP_HOST`), from the address
in `EMAIL_USER` with an App Password in `EMAIL_PASS`, always to the
logged-in user's own address and nobody else. The body is Markdown and is
sent twice: as plain text and as HTML rendered by the `markdown` package,
so a report reads the same in a mail client. Attachments come from the
chat's files only.

### remember

The model's long-term memory about a person: one note per row in
`user_memory`, capped at a hundred, prepended to the system prompt of every
turn as "What you remember about this user". The model is told to save
preferences and corrections without being asked, and never secrets. The
reference called this `logs_and_preferences`; the job is the same, the
name says what it does.

### read_tool_output

A tool result longer than 12,000 characters is cut in the model's context
and the cut carries a note: the output number, and how to page or search
the rest. The full result was always stored in `tool_calls`; this tool
reads it back by line range or keyword, scoped to the chat. `fetch_url`
now keeps 80,000 characters of a page instead of 12,000, because the model
can finally reach the rest.

### manage_files

The sandbox workspace is a folder on the host, bind-mounted at `/lab`, so
sharing a file the code produced is a copy from one folder to another plus
a link. The reference had to stream a tar archive out of the container. An
image shares as inline Markdown, anything else as a download link.

### schedule_task

A task is a prompt with a time. The row sits in `scheduled_tasks` with a
UTC time; a daemon thread in every worker polls every thirty seconds.
Claiming a task is a single UPDATE that stamps the earliest due row with a
lock id, so several workers polling the same table cannot both start it.
A claimed task becomes an ordinary turn in its chat through the same
producer a typed message uses, so it gets the tool loop, key rotation,
persistence and cancellation for nothing; the transcript shows the task
firing and the reply under it. A recurring task re-arms itself when it
finishes; a claim older than thirty minutes is treated as a crashed worker
and handed back. One thing the reference got wrong and we did not: if the
user is mid-turn in that chat, the task waits a minute rather than
cancelling their turn.

## Configuration

| Variable | Needed by | Where to get it |
|---|---|---|
| `EMAIL_USER`, `EMAIL_PASS` | send_self_email | A Gmail address and an App Password (Google Account, Security, App passwords) |
| `YOUTUBE_API_KEY` | search in analyze_youtube_video, optional | Google Cloud Console, YouTube Data API v3 |
| `SMTP_HOST`, `SMTP_PORT` | a non-Gmail mail server, optional | your provider |

Image generation and video analysis use the existing Gemini keys.

## What was verified

Forty-five offline checks in `smoke_test.py`: memory save, dedupe, prompt
injection and delete; truncation with the pointer, paging and keyword
search; sharing a workspace file, traversal refused; email to the user's
own address with text, HTML and attachment, and the unconfigured case; the
deck writer and theme choice; scheduling by delay and by ISO time with an
offset, the past refused, listing and cancelling; the scheduler claiming a
due task and running it through a stub producer, marking it done, and
postponing one whose chat is busy; the outputs route for the owner, for
another user, for a missing file, for traversal, and anonymously.

Live: a YouTube video summarised in five seconds, a YouTube search through
Tavily, and a five-slide deck planned by the model and written to disk in
nineteen seconds.
