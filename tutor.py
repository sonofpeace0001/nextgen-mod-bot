"""Tutor entry point.

The structured, lesson-by-lesson course (NEXTGEN Academy) now lives on the website
(config.ACADEMY_LINK), not in the bot -- see the retirement note in curriculum.py. When
a member asks to learn / "where do i start" / "what next", point them at the Academy
site, remind them the prompt-helper is available in here, and still surface a tool
recommendation (CREAO first, or the agent/income links) if their message actually calls
for one.

Only fires when the bot is @mentioned or the message is in the help channel (the
caller in bot.py enforces that). Returns True when it handled the message so the
caller skips chat.py's reply and avoids double-replying.
"""
from __future__ import annotations
import re, logging
import config, recommendations

log = logging.getLogger("tutor")

_LEARN_RE = re.compile(
    r"(where do i (start|begin)|where to (start|begin)|how do i start|"
    r"what (do i do|should i (do|learn)|next|now)|what'?s next|"
    r"teach me|guide me|i want to learn|help me learn|getting started|"
    r"get started|level up|next step|where should i)",
    re.IGNORECASE,
)


def _strip_mention(bot, message) -> str:
    text = message.content or ""
    for uid in (bot.user.id,):
        text = text.replace(f"<@{uid}>", "").replace(f"<@!{uid}>", "")
    return text.strip()


def wants_tutor(text: str) -> bool:
    return bool(_LEARN_RE.search(text or ""))


GUIDANCE = (
    f"the actual course lives on NEXTGEN Academy now, sign up and follow the track there: "
    f"{config.ACADEMY_LINK}\n\n"
    "in here i can help draft a ready-to-paste prompt for whatever you're building, or "
    "point you to the right tool. what are you trying to make?"
)


async def maybe_handle(bot, message) -> bool:
    text = _strip_mention(bot, message)
    if not wants_tutor(text):
        return False

    # Only show a tool recommendation if the message actually called for one (agent/
    # automation/earning questions); no default CREAO push here, this is a redirect.
    ctx = recommendations.contextual_extra(text)
    try:
        if ctx:
            await message.reply(content=GUIDANCE, embed=recommendations.embed(ctx), mention_author=False)
        else:
            await message.reply(content=GUIDANCE, mention_author=False)
    except Exception as e:
        log.error(f"tutor reply failed: {e}")
    return True
