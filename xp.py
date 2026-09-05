"""X (Twitter) engagement XP system.

No X API involved -- automatically verifying who actually retweeted/replied to a given
post requires a paid X API tier plus every member linking their handle, with real
ongoing cost and rate limits. Instead: a mod announces a post with /xpost, members
submit proof of their own engagement with /xproof, and a mod approves/denies in the
staff channel -- the exact same trusted-human-review pattern this bot already uses for
reports and ban appeals. XP is only ever awarded on approval.
"""
from __future__ import annotations
import discord, config, database as db, logging
log = logging.getLogger("xp")


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
    """Handle /xpost: record the post, announce it in the channel the command was run
    in, and ping the configured role. Mod-only, gated by the caller."""
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
    try:
        await interaction.channel.send(
            content=ping, embed=e,
            allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
        )
    except discord.Forbidden:
        await interaction.followup.send("I can't post here (missing permissions).", ephemeral=True)
        return
    await interaction.followup.send(f"Posted. (post #{xid})", ephemeral=True)


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
