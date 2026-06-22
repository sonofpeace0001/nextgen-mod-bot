"""Simple member level store (Task 5).

A thin wrapper over the member_level DB table (user_id, level). Levels are the five
curriculum tiers. Set initially from the welcome-DM answer, read and advanced by the
tutor.

NOTE (phase two, optional): there is intentionally NO automatic XP / message-count
ranking here. If you want that later, add an events table and an advance-on-threshold
rule that calls advance() -- TODO hook below.
"""
from __future__ import annotations
import database as db
import curriculum

DEFAULT_LEVEL = "novice"

# Welcome-DM answer -> starting tier (Task 1 -> Task 5 mapping).
#   total beginner -> novice, dabbled a bit -> beginner, pretty comfortable -> intermediate
ANSWER_TO_LEVEL = [
    (("comfortable", "pretty", "confident", "experienced", "advanced"), "intermediate"),
    (("dabble", "dabbled", "a bit", "some", "little"),                  "beginner"),
    (("beginner", "total", "new", "none", "never", "nothing", "zero"),  "novice"),
]


def level_from_answer(text: str) -> str:
    """Map a free-text welcome-DM reply to a starting tier."""
    t = (text or "").lower()
    for needles, level in ANSWER_TO_LEVEL:
        if any(n in t for n in needles):
            return level
    return DEFAULT_LEVEL


def get_level(uid: int) -> str:
    return db.get_member_level(uid) or DEFAULT_LEVEL


def set_level(uid: int, level: str) -> None:
    if level not in curriculum.TIERS:
        level = DEFAULT_LEVEL
    db.set_member_level(uid, level)


def advance(uid: int) -> str:
    """Move a member up one tier (capped at grandmaster). Returns the new level."""
    nxt = curriculum.next_tier(get_level(uid))
    if nxt:
        set_level(uid, nxt)
    return get_level(uid)


# TODO (phase two): wire an automatic XP / activity ranking system that calls
# advance() when a member crosses a threshold. Left out on this pass by design.
