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

    # 1. An active session means the member is answering our questions -> draft now.
    #    Strict two-turn flow: we ask everything once, then the very next reply drafts.
    sess = _get_session(key)
    if sess:
        return await _draft_from_answers(message, key, sess, text)

    # 2. "what other platform can i use" after a draft.
    if _MORE_RE.search(text):
        await _send(message, recommendations.alternatives(_get_last(key)))
        return True

    # 3. A fresh prompt request -> ask all the intake questions at once, in one message.
    if not wants_prompt(text):
        return False
    category = curriculum.detect_category(text)  # may be None -> generic intake/blueprint
    _set_session(key, "answering", category, text)
    await _send(message, prompttemplates.intake(category))
    log.info(f"prompt session opened for {message.author} (category={category})")
    return True


async def _draft_from_answers(message, key, sess, answers) -> bool:
    """The member just answered the intake questions. Draft the prompt and finish."""
    request = sess.get("request", "")
    # Resolve a category from anything we have, falling back to a generic blueprint.
    category = sess.get("category") or curriculum.detect_category(answers) \
        or curriculum.detect_category(request)
    _sessions.pop(key, None)          # one-shot: clear before drafting so we never loop
    _set_last(key, category)

    draft = None
    try:
        draft = await llm.draft_prompt(
            category or "requested output",
            prompttemplates.blueprint(category),
            answers, request,
        )
    except Exception as e:
        log.error(f"draft_prompt failed: {e}")

    if not draft or not draft.strip():
        await _send(message, "i couldn't draft that one just now. tag me and try again in a moment.")
        return True

    await _deliver(message, category, draft.strip())
    log.info(f"prompt drafted for {message.author} (category={category}, {len(draft)} chars)")
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
