# Generative UI runtime (Phase 2, upgraded)

Started 2026-10-08. Rishi asked for a production Generative UI framework: React, Tailwind,
shadcn-style components, Motion, Lucide, charts, 3D where it earns it; premium SaaS quality;
progressive edits; events back to the AI; controlled capabilities. His decisions: all packages
approved, no GSAP (licence), hybrid specs + custom JSX, three stages verified live.

## Architecture

```
model ── ui_create(spec, state) ──► app.py validates against catalog.json, saves (widgets, format 'spec')
      ◄─ widget_id, ids ───────────┘   │ emits {type: interaction, format: spec, ...}
      ── ui_update(ops) ──────────► _apply_ui_ops (same as genui/src/store.js) → saved → emitted as a patch
page (main.js) ── fetches static/genui/runtime.{js,css} once ── writes them into the sandboxed frame
frame (React runtime) ── draws the spec; applies patches in place; saves state; emits events
```

- **The frame** is the same sandbox as every widget: `allow-scripts`, no same origin, a CSP
  with no network. The runtime is inlined into the frame's document by the page, because the
  frame may not fetch anything. Its only channel out is postMessage (`genui/src/bridge.js`):
  height, state, event, finish, display, reveal.
- **The spec** is a tree of catalogue components. It cannot run code or style anything the
  catalogue does not offer, so every interface uses the design system. Bindings read the state
  (`$bind`, `{{/path}}`, `$item` in a Repeat); derived values compute from lists (`$count`,
  `$sum`, `$avg`, `$min`, `$max`, `$filter` with where and search); actions change it (set,
  toggle, push, remove, open/close, toast, emit, notify, submit, display).
- **Progressive edits.** Operations address nodes by id (insert/update/replace/move/remove)
  and the state by path (set/merge/push/delete). React keeps keyed nodes, so changed numbers
  animate and inserted parts slide in. The page and the server run the same operations; a
  test runs both engines (node and Python) on the same list and requires identical results.
- **One catalogue** (`genui/src/catalog.js` → `static/genui/catalog.json`) feeds the registry
  check in the build, the server's validator, and the model's brief (`_genui_guide`).
- **Feedback to the model**: validation problems (unknown component, children where none are
  allowed, duplicate ids, a dialog without an id) refuse the spec with the reason; unknown icons
  and figures left stale by a list change come back as warnings/`check`.

## Build

`cd genui && npm install && npm run build` → `static/genui/runtime.js` (React 19, Radix,
Motion, Lucide subset, ~224 KB gzipped), `runtime.css` (Tailwind compiled from the components
only, Inter inlined), `catalog.json`, `runtime.js.LEGAL.txt` (licences of everything bundled).
The output is committed; the server never needs Node.

## Found on the live site and fixed

| Seen | Fix |
|---|---|
| Table figures counted up from zero on every tab switch | cells animate only on change |
| A dialog in a tall frame could open off screen | the runtime reports it; the page scrolls it into view |
| The model's add-task form only emitted, so the click did nothing | derived values, the push pattern in the brief, a "Noted" toast for live emits |
| A list changed and the done/total KPIs kept old numbers | `ui_update` names stale figures in its result |
| KPI cards with empty icon boxes (unknown icon names) | 411 icons, nearest-name fallback, no box without an icon |

## Stages

- **A (done):** runtime, 43 components, specs, patches, derived values, events, state
  persistence, themes, validation, the brief.
- **B:** animated charts (Recharts), command palette (cmdk), drag and drop (dnd-kit), richer
  data tables, more overlays.
- **C:** sandboxed custom JSX components (Sucrase + Tailwind runtime), 3D (React Three Fiber,
  lazy), a full-width workspace view.
