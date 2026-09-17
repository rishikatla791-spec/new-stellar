# How N1kky Actually Built Stellar

Reconstructed from the full git history: 774 commits, 2026-01-30 to 2026-07-31.

---

## The headline: there were no phases

The repo's "Initial commit" is not a starting point. It already contained:

```
app.py             3,995 lines
index.html         6,730 lines    (with CSS and JS inline)
prompts.py           548 lines
file_scanning.py     829 lines
requirements.txt      87 lines
dockersetup.py       180 lines
```

That is a finished, working product in commit #1. He built it privately
first and pushed a snapshot. **The history cannot show you how to start,
because it begins in the middle.**

This matters for you: reading his early commits to learn the build order
tells you nothing, because the hard part happened before git was involved.

---

## What it was on day one (Jan 30) — not an agent platform

The original README describes something quite different:

- A **multi-mode chatbot**: Stellar (general), Spectrum (research +
  Tavily), Nebula (web dev planning), Cosmos (data reports with Chart.js)
- `google-generativeai` — the *old*, now-deprecated SDK
- `sqlitecloud` — a hosted database, not local SQLite
- Code preview in an iframe

The "modes" were **prompt templates**, not tools. There was no tool loop, no
Docker sandbox for the agent, no orchestrator, no streaming architecture.
The four modes later became the four personas (Obsidian, Crimson, Lunarity,
Emerald).

---

## The real timeline

| Date | What happened | Significance |
|------|---------------|--------------|
| **Jan 30** | First push. Multi-mode chatbot, 4k-line `app.py`. | Already substantial |
| **Feb 3** | "Forge" / "CodeLab": run code in Docker. First tests. | Execution arrives |
| **Feb–Mar** | Feature churn. Modes added and removed (Nebula deleted Feb 3). | Finding the product |
| **Mar 30** | `agent_tools.py` split out of `app.py`. | Tools become a concept — **2 months in** |
| **Apr 5** | **"Stellar Lab Sandbox: persistent Docker container for agentic execution"** | ← *the pivot to an agent platform* |
| **Apr 6** | Subdomain routing + repo tool. | `repo_control` is born |
| **Apr 11** | Google Auth, waitlist, admin dashboard. | Multi-user |
| **Apr** | **219 commits** | The explosion month |
| **May 1** | Model fallback chains, `BLOCK_NONE` safety settings. | Quota reality bites |
| **May 17** | **"Re-organize file structure (static, templates, deploy)"** — CSS and JS finally extracted from the 6,730-line `index.html`. | Structure at last — **3.5 months in** |
| **Jun 1** | PWA, service worker, Web Push. | |
| **Jun 5–6** | SSH TUI gateway, Sentinel self-healer. | |
| **Jun 10–13** | **Orchestrator + shared agent memory.** | AI agents start writing the code |
| **Jun** | **317 commits** | Mostly agent-authored |
| **Jun 23** | `key_manager.py` extracted from `app.py`. | **~5 months in** |
| **Jul 23** | PTY terminal streaming with xterm.js. | |
| **Jul 31** | Last commit. | |

`app.py` grew 3,995 → 4,499 → 5,395 → 6,273 → **8,380** → 8,561 lines.
The steepest jump is June 7–16: the agent era.

Roughly **400 of 774 commits** use conventional-commit prefixes
(`feat:`, `fix:`, `refactor:`) — the orchestrator's output signature.
From June onward, the majority of this codebase was written by its own AI
agents.

---

## Four things this reveals

**1. He never designed the architecture up front.** He built a chatbot,
then bolted on execution, then sandboxing, then multi-user, then autonomy.
Each step was "what do I want next", not "what does this depend on".

**2. Every clean module is a late extraction, not an early decision.**
`agent_tools.py` (Mar 30), `templates/` + `static/` (May 17),
`key_manager.py` (Jun 23) — all pulled out of `app.py` *months* after the
code was written. `app.py` is 8,561 lines because it was the default
destination for everything, and splitting it was always deferrable.

**3. The structural debt was real and he paid it.** May 17's
"Extract inline CSS and JS from index.html" is a 6,730-line file finally
being broken up. That refactor is pure cost — it added no features.

**4. He always had something he used daily.** This is the genuine strength
of his approach and the reason the product is coherent: he was his own
user from day one, so he always knew what to build next.

---

## Why our roadmap is different — and where his is better

Our 13 phases are a **dependency-ordered reconstruction**, not his history.
Deliberately:

- Following his real order means building a multi-mode prompt-template
  chatbot first and discarding most of it at the April pivot.
- He could bolt `lab_execute` onto his streaming code in April because he
  had lived with that code for three months. You have not.
- His two big refactors (May 17, Jun 23) are work you skip entirely by
  structuring correctly on day one — which is what phase 1 did by splitting
  `db.py` / `auth.py` / `chat.py` instead of starting a monolith.

**Where his approach beats ours:** he was never building toward an abstract
plan. Every commit scratched a real itch in software he used that day. A
phase roadmap can feel academic by comparison.

The mitigation is simple and worth taking seriously: **every phase ends in
something that runs — so actually use it.** Send real messages in phase 1.
Break the stream in phase 2. Ask the agent to do real work in phase 5. The
roadmap gives you his ordering-in-hindsight; using it daily gives you his
instinct for what matters next.
