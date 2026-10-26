# Phase 2: Generative UI, part 1 (persistent and live views)

Built 2026-10-08, after N1kky's roadmap card "Generative UI Framework" (tailwind-injection,
hitl-loop). Rishi chose this round's scope: **persistent + live UI only**. Tailwind inside
widgets, JSON forms with approve/reject gates, and the widget bridge come later.

## What was researched first

| Source | Idea taken |
|---|---|
| MCP Apps (ui:// resources) | UI in a sandboxed frame, talking to the host over a small logged message set |
| OpenAI Apps SDK | widget state the host keeps (`setWidgetState`), updates pushed into a running widget |
| Google A2UI | incremental updates to a surface the agent already drew, instead of redrawing it |
| Tailwind docs | the browser runtime is for prototyping; it would have to be self-hosted (later round) |

## What existed before

`request_user_interaction` drew model-written HTML in a sandboxed frame (`allow-scripts`, no
same origin, a frame route whose CSP blocks the network) and paused the turn until the user
answered. Three gaps: widgets vanished on reload (only a summary card was left), every widget
blocked the turn, and the wait asked Redis for the answer ten times a second.

## What changed

- **Widgets are saved** (`widgets` table, schema 17). One row per frame: a game or a wizard
  that updates itself in place stays one row, with `live_id` the interaction id answering
  now. The answer is kept too. The row is attached to the reply its turn saves, the same way
  tool calls are (`_link_turn`), and history returns it, so a reload draws the widget again:
  questions and boards closed, as they were left; live views still working.
- **Live views: `render_ui`.** Shows a dashboard, progress board, chart or table and returns
  at once. Its `widget_id` updates the same view later, in this turn or a later one:
  `state_json` is merged into the saved state and the whole state is sent into the running
  frame (`stellar:update`), so it changes without reloading. The view reads its state on load
  from `window.stellarState`, so a reload shows the latest data. Every turn's system prompt
  lists the chat's live views by id (`live_view_digest`), because history carries messages
  only and the ids would otherwise be forgotten.
- **No polling.** The wait is a blocking pop (`BLPOP`, 1 s at a time so Stop is still
  noticed): Redis wakes it the moment the answer is pushed.

## Safety kept

- A live view can only be updated from its own chat; ids are checked as uuids.
- Sizes are capped: 200 KB of HTML, 20 KB of state.
- A live view takes no answers (`finish` does nothing), so nothing waits on it.
- The frame, its CSP and the postMessage checks are unchanged.
