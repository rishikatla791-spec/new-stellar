"""Model routing: which model answers a turn when the user leaves it on Auto.

Every message used to go to the same model at the same (low) thinking
level. That spends the scarcest quota on the easiest messages: on the free
tier a Flash model allows about 15 requests a key a day, while Flash-Lite
allows about 500 and Gemma about 1,500. Routing sends each turn to the
cheapest model that can do it well, and saves the strong model, thinking
hard, for the work that needs it.

Four tiers:
    swift     Flash-Lite, minimal thinking   greetings, short simple questions
    core      Flash, low thinking            normal work (the old behaviour)
    obsidian  best Flash, HIGH thinking      debugging, maths, planning, builds
    lunarity  Gemma 4 31B                    long reading and summarising, and
                                             the overflow tier when Flash quota
                                             runs out (it calls tools natively)

The router is a set of rules, not a model: classifying with a model call
would spend quota to decide how to spend quota. Learned routers (RouteLLM)
beat rules on average, but need preference data we do not have; every
decision is logged with its reason so the rules can be tuned from real use.

Pure functions only. app.py supplies what the rules need (the message,
attachment kinds, the last tier used in the chat, which models the keys
can use and which are out of quota) and applies the answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Candidate models per tier, best first. Only models the keys can actually
# use are kept (available_models() in app.py), so a model Google retires or
# refuses on the free tier drops out by itself. Checked on the free tier on
# 2026-10-08: 3.8-flash (tools + HIGH thinking), 3.5-flash, 3.5-flash-lite,
# gemma-4-31b-it (tools) all answer; 3.1-pro-preview has no free quota and is
# deliberately absent.
TIER_MODELS: dict[str, tuple[str, ...]] = {
    "swift":    ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"),
    "core":     ("gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.6-flash"),
    "obsidian": ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"),
    "lunarity": ("gemma-4-31b-it", "gemma-4-26b-a4b-it"),
}

# Thinking effort per tier. None leaves the model's own default (Gemma has
# no thinking setting). app.py turns the name into the right config for the
# model family, since 2.5 and older reject thinking_level outright.
TIER_THINKING: dict[str, str | None] = {
    "swift": "MINIMAL",
    "core": "LOW",
    "obsidian": "HIGH",
    "lunarity": None,
}

TIER_LABELS = {
    "swift": "Swift",
    "core": "Core",
    "obsidian": "Obsidian",
    "lunarity": "Lunarity",
    "manual": "Manual",
}

# When a tier's models are all out of quota, the next tier to try. Obsidian
# falls to Core rather than Gemma: a hard problem answered by a weaker model
# that still reasons is better than one answered by a model built for volume.
DEGRADE: dict[str, tuple[str, ...]] = {
    "obsidian": ("core", "lunarity", "swift"),
    "core": ("lunarity", "obsidian", "swift"),
    "swift": ("lunarity", "core"),
    "lunarity": ("core", "swift"),
}

# Gemma's window is far smaller than Flash's 1M. A chat already this large
# goes to a Flash tier even when the message itself looks like reading.
LUNARITY_MAX_CONTEXT_TOKENS = 100_000

# --- the signals -----------------------------------------------------------

_GREETING = re.compile(
    r"^\s*(hi|hii+|hello|hey|yo|hola|namaste|thanks?|thank you|thx|ty|ok(ay)?|cool|nice|great|"
    r"good (morning|afternoon|evening|night)|bye|goodbye|gm|gn|yes|no|yep|nope|sure|"
    r"how are you|who are you|what can you do)\b[\s!.?,]*$",
    re.I)

# "hi there", "hey stellar", "thanks a lot!": a greeting word opening a few
# words that ask for nothing.
_GREETING_START = re.compile(
    r"^\s*(hi+|hello|hey|yo|hola|namaste|thanks?|thank you|thx|good (morning|afternoon|evening|night)|"
    r"bye|goodbye|ok(ay)?|cool|nice|great|awesome)\b", re.I)
_WORK_WORDS = re.compile(
    r"\b(code|build|deploy|run|file|app|site|website|image|search|write|make|create|fix|"
    r"explain|help me|how (do|to|can)|what is|why)\b", re.I)

# Work that rewards slow, careful reasoning. Kept to phrases that mean it:
# "error" alone matches "Error 404 page design" as easily as a traceback.
_LOGIC = re.compile(
    r"\b(debug\w*|traceback|stack ?trace|exception|segfault|race condition|deadlock|memory leak|"
    r"doesn'?t work|not working|isn'?t working|broken|fails?|failing|crash\w*|"
    r"fix (the |this |my )?(bug|error|issue|code|test)|why (does|is|do|did|won'?t|isn'?t)|"
    r"prove|proof|theorem|derive|derivation|integral|derivative|equation|solve|"
    r"algorithm|complexity|big[- ]?o|optimi[sz]e|refactor\w*|architect\w*|system design|"
    r"design (a|an|the) (system|database|schema|api|architecture)|"
    r"step[- ]by[- ]step|plan (out|the|a|an)|trade-?offs?|compare (the )?approaches|"
    r"build (me )?(a|an|the) (full|complete|whole)?\s*(website|web ?app|app|game|api|backend|dashboard|clone)|"
    r"(create|make|develop) (me )?(a|an) (full|complete|whole)?\s*(website|web ?app|app|game|api|backend|dashboard)|"
    r"deploy|migrat\w+|security (review|audit)|vulnerab\w+|reverse[- ]engineer)\b",
    re.I)

# Asking for reading: summarise this, explain this document.
_READING = re.compile(
    r"\b(summari[sz]e|summary|tl;?dr|key points|read (this|the|my)|go through|analy[sz]e (this|the) "
    r"(document|pdf|file|report|paper|article|transcript)|extract (the )?\w+ from)\b",
    re.I)

# A pasted traceback or compiler error, whatever words come with it.
_TRACE = re.compile(
    r"(Traceback \(most recent call last\)|^\s+at .+\(.+:\d+:\d+\)|Error: .+\n\s+at |"
    r"\bline \d+, in \b|SyntaxError|TypeError|ValueError|KeyError|NullPointerException|"
    r"Segmentation fault|panic:|undefined reference)",
    re.M)

_FOLLOW_UP = re.compile(
    r"^\s*(yes|yeah|yep|ok(ay)?|sure|go ahead|do it|continue|go on|proceed|fix it|try again|"
    r"again|same|more|next|and\??|then\??|still|now)\b", re.I)

_CODE_FENCE = re.compile(r"```")
_MATHY = re.compile(r"[=+\-*/^√∑∫≤≥≠]")

DOC_KINDS = {"pdf", "doc", "docx", "txt", "md", "csv", "json", "xlsx", "pptx", "html"}


@dataclass
class Route:
    tier: str
    reason: str
    models: list[str] = field(default_factory=list)
    thinking: str | None = None

    @property
    def label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier.title())


def classify(message: str, attachment_kinds: list[str] | None = None,
             previous_tier: str | None = None, context_tokens: int = 0) -> tuple[str, str]:
    """(tier, reason) for one user message, before quota is considered."""
    text = message or ""
    stripped = text.strip()
    kinds = {k.lower().lstrip(".") for k in (attachment_kinds or [])}
    code_lines = 0
    if _CODE_FENCE.search(text):
        # Lines inside fences, roughly: a long pasted block is real work.
        parts = text.split("```")
        code_lines = sum(p.count("\n") for p in parts[1::2])

    if _TRACE.search(text):
        return "obsidian", "a pasted error or traceback"
    if code_lines >= 15:
        return "obsidian", f"{code_lines} lines of code to work through"
    if _LOGIC.search(text):
        return "obsidian", "debugging, maths, design or a multi-step build"
    if len(_MATHY.findall(stripped)) >= 6 and any(ch.isdigit() for ch in stripped):
        return "obsidian", "a calculation"

    # A short follow-up continues whatever the last turn was doing: "yes,
    # fix it" after a debugging turn is still debugging (session stickiness,
    # as OpenRouter's auto router does).
    if previous_tier in ("obsidian", "core", "lunarity") and len(stripped) < 120 \
            and _FOLLOW_UP.search(stripped):
        return previous_tier, "a follow-up to the previous turn"

    reading = bool(_READING.search(text)) or bool(kinds & DOC_KINDS) or len(text) > 6000
    if reading and context_tokens < LUNARITY_MAX_CONTEXT_TOKENS:
        return "lunarity", "reading or summarising a long text"

    if not kinds and len(stripped) <= 80 and not code_lines and (
            _GREETING.match(stripped)
            or (_GREETING_START.match(stripped) and len(stripped.split()) <= 6
                and not _WORK_WORDS.search(stripped))):
        return "swift", "a short greeting or simple reply"
    if not kinds and len(stripped) <= 60 and not code_lines and "?" in stripped \
            and not re.search(r"\b(code|build|deploy|run|file|app|site|image|search)\b", stripped, re.I):
        return "swift", "a short question"
    return "core", "general work"


def route(message: str, *, available: set[str], exhausted: set[str],
          attachment_kinds: list[str] | None = None, previous_tier: str | None = None,
          context_tokens: int = 0) -> Route:
    """The tier and the models to try for one turn.

    available: models the API keys can use at all.
    exhausted: models with no key left today (blocked or out of quota).
    A tier with nothing usable degrades along DEGRADE; the reason says so.
    """
    tier, reason = classify(message, attachment_kinds, previous_tier, context_tokens)
    for candidate in (tier, *DEGRADE.get(tier, ())):
        if candidate == "lunarity" and context_tokens >= LUNARITY_MAX_CONTEXT_TOKENS:
            continue
        models = [m for m in TIER_MODELS[candidate] if m in available and m not in exhausted]
        if models:
            why = reason if candidate == tier else \
                f"{reason}; {TIER_LABELS[tier]} is out of quota, so {TIER_LABELS[candidate]}"
            return Route(candidate, why, models, TIER_THINKING[candidate])
    # Nothing usable anywhere: hand back the first tier's models anyway and
    # let the turn's own quota handling wait or explain.
    models = [m for m in TIER_MODELS[tier] if m in available] or list(TIER_MODELS["core"])
    return Route(tier, f"{reason}; every tier is busy", models, TIER_THINKING[tier])
