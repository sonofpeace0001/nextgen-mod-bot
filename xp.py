"""X (Twitter) engagement XP system.

No X API involved -- automatically verifying who actually retweeted/replied to a given
post requires a paid X API tier plus every member linking their handle, with real
ongoing cost and rate limits. Instead: a mod announces a post with /xpost, members
submit proof of their own engagement with /xproof, and a mod approves/denies in the
staff channel -- the exact same trusted-human-review pattern this bot already uses for
reports and ban appeals. XP is only ever awarded on approval.
"""
from __future__ import annotations
import datetime, logging
import discord, config, database as db, moderation
from discord.ext import tasks

log = logging.getLogger("xp")

_bot = None


def _tzinfo():
    """Resolve PROMPT_TZ, falling back to WAT (UTC+1) if zoneinfo/tzdata is missing."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.PROMPT_TZ)
    except Exception as e:
        log.warning(f"Could not load tz '{config.PROMPT_TZ}' ({e}); using fixed UTC+1.")
        return datetime.timezone(datetime.timedelta(hours=1))


_TZ = _tzinfo()
_LAST_LEADERBOARD_DATE_KEY = "xp_leaderboard_last_date"


class XPSubmissionView(discord.ui.View):
    def __init__(self, submission_id):
        super().__init__(timeout=None)
        self.submission_id = submission_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="xp_approve")
    async def approve(self, interaction, button):
        sub = db.get_xp_submission(self.submission_id)
        if not sub: await interaction.response.send_message("Not found.", ephemeral=True); return
        if sub["status"] != "pending":
            await interaction.response.send_message(f"Already {sub['status']}.", ephemeral=True); return
        db.update_submission_status(self.submission_id, "approved")
        total = db.add_xp(sub["guild_id"], sub["user_id"], config.XP_REWARD_AMOUNT)
        try:
            member = await interaction.client.fetch_user(sub["user_id"])
            await member.send(
                f"your proof was approved, +{config.XP_REWARD_AMOUNT} XP. you're at {total} XP now. "
                "thanks for engaging."
            )
        except Exception:
            pass
        db.log_action(sub["guild_id"], "XP APPROVED", sub["user_id"], str(interaction.user),
                       f"+{config.XP_REWARD_AMOUNT} XP (total {total})")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.green()
        e.set_footer(text=f"Approved by {interaction.user} -- +{config.XP_REWARD_AMOUNT} XP (total {total})")
        await interaction.response.edit_message(embed=e, view=None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="xp_deny")
    async def deny(self, interaction, button):
        sub = db.get_xp_submission(self.submission_id)
        if not sub: await interaction.response.send_message("Not found.", ephemeral=True); return
        if sub["status"] != "pending":
            await interaction.response.send_message(f"Already {sub['status']}.", ephemeral=True); return
        db.update_submission_status(self.submission_id, "denied")
        try:
            member = await interaction.client.fetch_user(sub["user_id"])
            await member.send(
                "your engagement proof wasn't approved this time. double check the link actually shows "
                "your reply or retweet, then feel free to submit again with /xproof."
            )
        except Exception:
            pass
        db.log_action(sub["guild_id"], "XP DENIED", sub["user_id"], str(interaction.user), sub["proof_link"][:200])
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.red()
        e.set_footer(text=f"Denied by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)


def _staff_channel(guild):
    target_id = config.STAFF_CHANNEL_ID or config.LOG_CHANNEL_ID
    return guild.get_channel(target_id) if target_id else None


async def announce_x_post(bot, interaction, link, note):
    """Handle /xpost: record the post, announce it in XP_ANNOUNCE_CHANNEL_ID (falls
    back to the channel the command was run in if unset), and ping the configured
    role. Mod-only, gated by the caller."""
    xid = db.add_x_post(interaction.guild.id, link, note or "", str(interaction.user))
    e = discord.Embed(
        title="new post from NEXTGEN, go engage",
        description=(note or "comment and repost to earn XP.") + f"\n\n{link}",
        color=discord.Color.blurple(),
    )
    e.add_field(
        name="how to earn XP",
        value=f"engage on X, then run `/xproof link:<your reply or repost link>` here. "
              f"a mod reviews it and you get +{config.XP_REWARD_AMOUNT} XP once approved.",
        inline=False,
    )
    e.set_footer(text=f"Posted by {interaction.user.display_name}")
    ping = f"<@&{config.XP_PING_ROLE_ID}>" if config.XP_PING_ROLE_ID else None
    target = interaction.channel
    if config.XP_ANNOUNCE_CHANNEL_ID:
        target = interaction.guild.get_channel(config.XP_ANNOUNCE_CHANNEL_ID) or interaction.channel
    try:
        await target.send(
            content=ping, embed=e,
            allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
        )
    except discord.Forbidden:
        await interaction.followup.send("I can't post there (missing permissions).", ephemeral=True)
        return
    where = f" in {target.mention}" if target.id != interaction.channel.id else ""
    await interaction.followup.send(f"Posted{where}. (post #{xid})", ephemeral=True)


async def submit_proof(bot, interaction, proof_link):
    """Handle /xproof: attach to the latest announced post, block duplicate submissions
    (denied ones can be resubmitted), and queue for mod approval in the staff channel."""
    xpost = db.get_latest_x_post(interaction.guild.id)
    if not xpost:
        await interaction.response.send_message(
            "there's no active post to submit proof for yet.", ephemeral=True)
        return

    existing = db.get_existing_submission(interaction.guild.id, interaction.user.id, xpost["id"])
    if existing:
        await interaction.response.send_message(
            f"you already have a submission for this post ({existing['status']}). "
            "wait for that to be reviewed before submitting again.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    sid = db.add_xp_submission(interaction.guild.id, interaction.user.id, xpost["id"], proof_link)

    ch = _staff_channel(interaction.guild)
    if ch:
        e = discord.Embed(title=f"XP submission #{sid}", color=discord.Color.gold())
        e.add_field(name="Member", value=f"{interaction.user} ({interaction.user.id})", inline=True)
        e.add_field(name="For post", value=xpost["link"][:500], inline=False)
        e.add_field(name="Proof link", value=proof_link[:500], inline=False)
        view = XPSubmissionView(sid)
        try:
            msg = await ch.send(embed=e, view=view)
            db.update_submission_message(sid, msg.id)
        except Exception as ex:
            log.error(f"Failed to post XP submission #{sid}: {ex}")
    else:
        log.warning("No STAFF_CHANNEL_ID or LOG_CHANNEL_ID set; XP submission not queued for review.")

    await interaction.followup.send("submitted for review. you'll get a DM once a mod checks it.", ephemeral=True)


async def restore_pending_views(bot):
    for s in db.get_pending_submissions():
        bot.add_view(XPSubmissionView(s["id"]))


def build_leaderboard_embed(guild, limit=10):
    """Shared by /xpleaderboard and the daily auto-post: top XP earners, excluding
    immune-role holders. Immune status is a live Discord role, not stored data, so
    over-fetch and filter here rather than in SQL. Returns None if nobody's earned yet."""
    rows = db.get_xp_leaderboard(guild.id, max(limit * 5, 100))
    lines = []
    for r in rows:
        member = guild.get_member(r["user_id"])
        if member and moderation._is_immune(member):
            continue
        name = member.display_name if member else f"User {r['user_id']}"
        lines.append(f"**{name}** -- {r['xp']} XP")
        if len(lines) >= limit:
            break
    if not lines:
        return None
    lines = [f"{n}. {line}" for n, line in enumerate(lines, 1)]
    return discord.Embed(title="XP Leaderboard", description="\n".join(lines), color=discord.Color.gold())


@tasks.loop(time=datetime.time(hour=config.XP_LEADERBOARD_HOUR, minute=0, tzinfo=_TZ))
async def _daily_leaderboard():
    if _bot is None or not config.XP_LEADERBOARD_ENABLED or not config.XP_ANNOUNCE_CHANNEL_ID:
        return
    today = datetime.datetime.now(_TZ).date().isoformat()
    if db.kv_get(_LAST_LEADERBOARD_DATE_KEY) == today:
        return  # already posted today (e.g. restart after the scheduled time)

    ch = _bot.get_channel(config.XP_ANNOUNCE_CHANNEL_ID)
    if not ch:
        log.warning("XP_ANNOUNCE_CHANNEL_ID not set or channel not found; skipping daily leaderboard.")
        return

    e = build_leaderboard_embed(ch.guild)
    if e is None:
        db.kv_set(_LAST_LEADERBOARD_DATE_KEY, today)
        return  # nobody's earned XP yet, nothing worth posting
    try:
        await ch.send(content="morning leaderboard check -- who's earning XP:", embed=e)
        db.kv_set(_LAST_LEADERBOARD_DATE_KEY, today)
        log.info(f"Posted daily XP leaderboard to channel {config.XP_ANNOUNCE_CHANNEL_ID}.")
    except Exception as ex:
        log.error(f"Failed to post daily XP leaderboard: {ex}")


@_daily_leaderboard.before_loop
async def _before():
    if _bot is not None:
        await _bot.wait_until_ready()


def start(bot):
    """Start the daily leaderboard scheduler. No-op if disabled or no announce channel."""
    global _bot
    _bot = bot
    if not config.XP_LEADERBOARD_ENABLED:
        log.info("XP_LEADERBOARD_ENABLED is false; daily XP leaderboard disabled.")
        return
    if not config.XP_ANNOUNCE_CHANNEL_ID:
        log.info("XP_ANNOUNCE_CHANNEL_ID not set; daily XP leaderboard disabled.")
        return
    if not _daily_leaderboard.is_running():
        _daily_leaderboard.start()
        log.info(f"Daily XP leaderboard started ({config.XP_LEADERBOARD_HOUR}:00 {config.PROMPT_TZ}).")
