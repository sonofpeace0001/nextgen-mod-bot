"""Prompt helper: describe a task -> get a ready-to-paste prompt (Task 4).

When a member describes wanting to build / make something, detect the output category
(image, graphic, tweet, thread, video, landing page, app, website, ...). If the request
is too vague, ask ONE short clarifying question. Otherwise generate a ready-to-paste,
well-structured prompt tailored to that output type, then recommend tools (CREAO first).

Only fires when the bot is @mentioned or the message is in the help channel (enforced by
the caller in bot.py). Returns True when handled so chat.py's reply is skipped.
"""
from __future__ import annotations
import re, logging
import config, llm, curriculum, recommendations

log = logging.getLogger("prompthelper")

_BUILD_RE = re.compile(
    r"(build|make|create|design|generate|write me|help me (make|build|write|design|create)|"
    r"i want to (build|make|create|design|write)|i need (a|an|to make)|"
    r"can you (make|build|write|design|create)|"
    r"landing page|website|web ?site|tweet|thread|logo|graphic|video|reel|app\b|image)",
    re.IGNORECASE,
)

# Below this many words of actual description, treat the request as too vague.
_MIN_WORDS = 6


def _strip_mention(bot, message) -> str:
    text = message.content or ""
    for uid in (bot.user.id,):
        text = text.replace(f"<@{uid}>", "").replace(f"<@!{uid}>", "")
    return text.strip()


def wants_prompt(text: str) -> bool:
    return bool(_BUILD_RE.search(text or ""))


def _is_vague(text: str) -> bool:
    words = re.findall(r"[a-zA-Z0-9']+", text or "")
    return len(words) < _MIN_WORDS


async def maybe_handle(bot, message) -> bool:
    text = _strip_mention(bot, message)
    if not wants_prompt(text):
        return False

    category = curriculum.detect_category(text)

    # Too vague to act on: ask exactly one short clarifying question.
    if category is None:
        await _send(message, curriculum.GENERIC_CLARIFY)
        return True
    if _is_vague(text):
        await _send(message, curriculum.CLARIFY.get(category, curriculum.GENERIC_CLARIFY))
        return True

    # Enough to act on: generate a ready-to-paste prompt for this output type.
    body = await llm.coach(
        f"A member wants to create a {category}. Their request: \"{text}\".\n"
        f"Write a single ready-to-paste prompt they can hand to an AI tool to produce this "
        f"{category}. Make it well-structured and specific (subject, style, format, any "
        f"details worth pinning down). Output only the prompt itself, no preamble, no quotes.",
        max_tokens=320,
    )

    reply = (
        f"here's a prompt you can paste straight in:\n\n{body}\n\n"
        f"{recommendations.recommend(category)}"
    )
    await _send(message, reply)
    return True


async def _send(message, text):
    try:
        await message.reply(text, mention_author=False)
    except Exception as e:
        log.error(f"prompthelper reply failed: {e}")
