"""Prompt helper (Task 4, upgraded): conversational senior-prompt-engineer flow.

When a member tags the bot (or posts in the help channel) asking for a prompt to make
something (graphic, tweet, thread, video, landing page, app, ...), the bot asks a few
quick intake questions, then drafts a premium, ready-to-paste prompt tailored to that
output type. It recommends CREAO first and asks them to try it first; alternatives are
only given when the member comes back and asks for more.

Only fires when the bot is @mentioned or the message is in the help channel (enforced by
the caller in bot.py). Returns True when handled so chat.py's reply is skipped.

State is kept in memory per (channel, member) with a 30 minute TTL. It is intentionally
not persisted: a dropped drafting session just means the member asks again.
"""
from __future__ import annotations
import re, time, io, logging
import discord
import config, llm, curriculum, recommendations, prompttemplates

log = logging.getLogger("prompthelper")

_BUILD_RE = re.compile(
    r"(build|make|create|design|generate|"
    r"help me (make|build|write|design|create)|"
    r"i want to (build|make|create|design|write)|"
    r"i need (a|an|to make)|"
    r"can you (make|build|write|design|create)|"
    r"(give me|need|want|draft|write|create|make)\s+(a |an |me a |me an )?prompt|"
    r"prompt (for|to)\b|"
    r"landing page|website|web ?site|tweet|thread|logo|graphic|video|reel|app\b|image)",
    re.IGNORECASE,
)

# "give me other platforms" after a draft.
_MORE_RE = re.compile(
    r"((another|other|more|different)\s+(platform|tool|option|app)|alternative|"
    r"something else|what else can i use|other options|any other)",
    re.IGNORECASE,
)

_SESSION_TTL = 1800  # 30 minutes
_sessions: dict = {}        # (channel_id, user_id) -> {stage, category, request, ts}
_last_category: dict = {}   # (channel_id, user_id) -> (category, ts)


def _now() -> float:
    return time.time()


def _strip_mention(bot, message) -> str:
    text = message.content or ""
    uid = bot.user.id
    return text.replace(f"<@{uid}>", "").replace(f"<@!{uid}>", "").strip()


def wants_prompt(text: str) -> bool:
    return bool(_BUILD_RE.search(text or ""))


def _get_session(key):
    s = _sessions.get(key)
    if s and _now() - s["ts"] <= _SESSION_TTL:
        return s
    if s:
        _sessions.pop(key, None)
    return None


def has_active_session(channel_id, user_id) -> bool:
    """True if this member has an open drafting session in this channel, so the router can
    keep routing their replies to the prompt-helper without requiring a re-mention."""
    return _get_session((channel_id, user_id)) is not None


def _set_session(key, stage, category, request):
    _sessions[key] = {"stage": stage, "category": category, "request": request, "ts": _now()}


def _set_last(key, category):
    _last_category[key] = (category, _now())


def _get_last(key):
    v = _last_category.get(key)
    if v and _now() - v[1] <= _SESSION_TTL:
        return v[0]
    return None


async def maybe_handle(bot, message) -> bool:
    text = _strip_mention(bot, message)
    key = (message.channel.id, message.author.id)

    # 1. An active drafting session takes priority (the member is answering us).
    sess = _get_session(key)
    if sess:
        return await _continue_session(message, key, sess, text)

    # 2. "what other platform can i use" after a draft.
    if _MORE_RE.search(text):
        await _send(message, recommendations.alternatives(_get_last(key)))
        return True

    # 3. A fresh prompt request?
    if not wants_prompt(text):
        return False
    category = curriculum.detect_category(text)
    if category is None:
        _set_session(key, "awaiting_category", None, text)
        await _send(message, "happy to draft you a prompt. " + curriculum.GENERIC_CLARIFY)
        return True
    _set_session(key, "awaiting_details", category, text)
    await _send(message, prompttemplates.intake(category))
    return True


async def _continue_session(message, key, sess, text) -> bool:
    if sess["stage"] == "awaiting_category":
        # Whatever they said, lock a category (may be None -> generic) and ask for details.
        category = curriculum.detect_category(text)
        sess["category"] = category
        sess["stage"] = "awaiting_details"
        sess["request"] = (sess.get("request", "") + " " + text).strip()
        sess["ts"] = _now()
        await _send(message, prompttemplates.intake(category))
        return True

    # awaiting_details -> draft the prompt now.
    category = sess.get("category")
    request = sess.get("request", "")
    _sessions.pop(key, None)
    _set_last(key, category)

    label = category or "requested output"
    try:
        async with message.channel.typing():
            draft = await llm.draft_prompt(
                label, prompttemplates.blueprint(category), text, request
            )
    except Exception as e:
        log.error(f"draft_prompt failed: {e}")
        draft = None

    if not draft:
        await _send(message, "i hit a snag drafting that one. give me a moment and ask again.")
        return True

    await _deliver(message, category, draft.strip())
    return True


async def _deliver(message, category, draft):
    intro = "here's your prompt, built to paste straight in:"
    rec = recommendations.creao_first(category)
    fits_inline = len(draft) <= 1800 and "```" not in draft
    if fits_inline:
        try:
            await message.reply(f"{intro}\n```\n{draft}\n```", mention_author=False)
        except Exception as e:
            log.error(f"inline prompt reply failed: {e}")
    else:
        fname = (category or "prompt").replace(" ", "-") + "-prompt.txt"
        try:
            f = discord.File(io.BytesIO(draft.encode("utf-8")), filename=fname)
            await message.reply(content=intro, file=f, mention_author=False)
        except Exception as e:
            log.error(f"file prompt reply failed: {e}")
    await _send(message, rec)


async def _send(message, text):
    try:
        await message.reply(text, mention_author=False)
    except Exception as e:
        log.error(f"prompthelper reply failed: {e}")
