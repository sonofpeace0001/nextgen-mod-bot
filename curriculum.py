"""Curriculum content (Task 5) -- data only, easy to edit.

Two things live here:
  1. TIER_SKILLS: per-tier ordered milestones the tutor walks members through.
  2. CATEGORY_KEYWORDS / category helpers: how the prompt-helper (Task 4) maps a
     member's "i want to make X" message to an output category.

No bot logic here on purpose. tutor.py reads this for content, leveling.py owns the
stored level, prompthelper.py reads the category map.
"""
from __future__ import annotations

# Ordered tiers, novice -> grandmaster.
TIERS = ["novice", "beginner", "intermediate", "advanced", "grandmaster"]

# Per tier: a short ordered list of concrete skills / milestones.
# Plain language, one small win before theory.
TIER_SKILLS = {
    "novice": [
        "make your first AI graphic, even a rough one",
        "write your first useful prompt and tweak it once",
        "try one AI chat tool and ask it something real",
    ],
    "beginner": [
        "make a clean graphic you'd actually post",
        "write a prompt that gets a good result on the first or second try",
        "use AI to speed up one task you already do",
    ],
    "intermediate": [
        "build a simple landing page with AI",
        "make a short AI video or animation",
        "turn one idea into a finished, shareable piece",
    ],
    "advanced": [
        "ship a small AI-built tool or site that a real person uses",
        "combine two or more AI tools to finish one project",
        "help someone else make their first AI thing",
    ],
    "grandmaster": [
        "chain agents or automations to run a task without you",
        "ship an AI-built product end to end",
        "teach a repeatable workflow others can follow",
    ],
}


def tier_index(level: str) -> int:
    try:
        return TIERS.index((level or "novice").lower())
    except ValueError:
        return 0


def next_tier(level: str) -> str | None:
    i = tier_index(level)
    return TIERS[i + 1] if i + 1 < len(TIERS) else None


def first_action(level: str) -> str:
    """The first concrete next action for someone at this tier."""
    skills = TIER_SKILLS.get((level or "novice").lower(), TIER_SKILLS["novice"])
    return skills[0]


def skills_for(level: str) -> list[str]:
    return TIER_SKILLS.get((level or "novice").lower(), TIER_SKILLS["novice"])


# ── Prompt-helper categories (Task 4) ─────────────────────────────
# category -> keywords that signal it. Order matters: more specific first.
CATEGORY_KEYWORDS = {
    "image generation": ["image", "picture", "photo", "art", "illustration", "render", "ai image"],
    "graphic design":   ["graphic", "logo", "poster", "flyer", "banner", "thumbnail", "design", "cover"],
    "thread":           ["thread"],
    "tweet":            ["tweet", "x post", "post on x"],
    "video":            ["video", "reel", "clip", "animation", "short"],
    "landing page":     ["landing page", "landing"],
    "website":          ["website", "web site", "web page", "webpage", "site"],
    "app":              ["app", "application", "mobile app", "tool"],
}

# Per-category clarifying question, asked when the request is too vague to act on.
CLARIFY = {
    "image generation": "what should the image show, and what's the vibe or style you want?",
    "graphic design":   "what's it for, and what words or brand should be on it?",
    "tweet":            "what's the tweet about, and who's it for?",
    "thread":           "what's the thread about, and how many points do you want to make?",
    "video":            "what's the video about, and how long should it be?",
    "landing page":     "what's the page selling or signing people up for?",
    "website":          "what's the website for, and what are the main pages?",
    "app":              "what should the app do, and who's it for?",
}

GENERIC_CLARIFY = "what are you trying to make? a graphic, a tweet, a landing page, an app, or something else?"


def detect_category(text: str) -> str | None:
    """Return the first matching output category, or None if unclear."""
    t = (text or "").lower()
    for category, words in CATEGORY_KEYWORDS.items():
        if any(w in t for w in words):
            return category
    return None
