"""Tutor: novice -> grandmaster guidance (Task 5).

Backed by curriculum.py for content and leveling.py for tiers. When a member asks to
learn / "where do i start" / "what next", give step-by-step guidance scaled to their
stored level, one concrete next action, and the relevant tool rec (CREAO first).

Only fires when the bot is @mentioned or the message is in the help channel (the
caller in bot.py enforces that). Returns True when it handled the message so the
caller skips chat.py's reply and avoids double-replying.
"""
from __future__ import annotations
import re, logging
import config, llm, leveling, curriculum, recommendations

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


async def maybe_handle(bot, message) -> bool:
    text = _strip_mention(bot, message)
    if not wants_tutor(text):
        return False

    uid = message.author.id
    level = leveling.get_level(uid)
    action = curriculum.first_action(level)
    nxt = curriculum.next_tier(level)
    category = curriculum.detect_category(action)

    context = (
        f"This member's current level is '{level}'.\n"
        f"Their one concrete next action is: {action}.\n"
        + (f"After this tier comes '{nxt}'.\n" if nxt else "They are at the top tier.\n")
        + f"They said: {text}"
    )
    guidance = await llm.coach(
        "Give this member friendly, step-by-step guidance for their level. Point them at "
        "their one concrete next action and encourage them to get that small win first. "
        "Keep it short. Do not write any tool links yourself.",
        context=context,
        max_tokens=260,
    )

    # Guidance as plain text; the CREAO rec rides along in an embed so the link shows as "CREAO AI".
    # Contextual links (agent / income) are appended in code only when the member asked about them.
    rec_text = recommendations.recommend(category)
    ctx = recommendations.contextual_extra(text)
    if ctx:
        rec_text += "\n\n" + ctx
    rec_embed = recommendations.embed(rec_text)
    try:
        await message.reply(content=guidance, embed=rec_embed, mention_author=False)
    except Exception as e:
        log.error(f"tutor reply failed: {e}")
    return True
