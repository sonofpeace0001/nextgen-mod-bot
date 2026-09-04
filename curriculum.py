"""Curriculum data -- data only, easy to edit.

The structured, lesson-by-lesson course (NEXTGEN Academy) now lives on the website
(config.ACADEMY_LINK), not in the bot -- see tutor.py, which just points members there.
What's left here is data unrelated to that in-bot curriculum:
  1. TIERS: tier NAMES only, used to store a member's self-reported level from the
     welcome DM (beginner/dabbled/comfortable -> novice/beginner/intermediate). No
     lesson content lives in the bot.
  2. CATEGORY_KEYWORDS / category helpers: how the prompt-helper (Task 4) maps a
     member's "i want to make X" message to an output category. Unrelated to the
     Academy retirement above.
"""
from __future__ import annotations

# Ordered tiers, novice -> grandmaster. Names only; leveling.py stores/validates against
# this list. No per-tier lesson content is kept in the bot anymore.
TIERS = ["novice", "beginner", "intermediate", "advanced", "grandmaster"]


def tier_index(level: str) -> int:
    try:
        return TIERS.index((level or "novice").lower())
    except ValueError:
        return 0


def next_tier(level: str) -> str | None:
    i = tier_index(level)
    return TIERS[i + 1] if i + 1 < len(TIERS) else None


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
