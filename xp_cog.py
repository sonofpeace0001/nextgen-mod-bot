"""Discord side of the points system (Reach XP and Builder XP).

Members post proof in the proof channels. A human reviewer checks it on a card in the staff
review channel and awards points. All scoring is in xp_engine.py and is deterministic:
no LLM is used to score or review anything. See points_config.py for the numbers.

Everything runs on UTC.
"""
from __future__ import annotations

import datetime
import hashlib
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import database as db
import moderation
import points_config as pc
import tickets
import xp_engine as xe
from points_store import Store

log = logging.getLogger("xp_cog")

UTC = datetime.timezone.utc
MAX_HASH_BYTES = 8 * 1024 * 1024

LANE_TITLES = {pc.REACH: "Reach", pc.BUILDER: "Builder", "combined": "Combined"}

ERRORS = {
    "not_found": "That submission no longer exists.",
    "not_pending": "That submission has already been reviewed.",
    "no_category": "Choose a category first.",
    "bad_category": "That category is not valid for this channel.",
    "single_only": "Only one category can be chosen for this submission.",
}


def now_utc():
    return datetime.datetime.now(UTC)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def is_staff(member) -> bool:
    if member.id == config.FOUNDER_ID:
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms and perms.administrator:
        return True
    ids = config.STAFF_ROLE_IDS or config.IMMUNE_ROLE_IDS
    return any(r.id in ids for r in getattr(member, "roles", []))


def is_reviewer(member) -> bool:
    if is_staff(member):
        return True
    return any(r.id in config.REVIEWER_ROLE_IDS for r in getattr(member, "roles", []))


def staff_only():
    async def pred(interaction: discord.Interaction):
        if is_staff(interaction.user):
            return True
        await interaction.response.send_message("You need a staff role to use this.", ephemeral=True)
        return False
    return app_commands.check(pred)


def is_silent_channel(channel) -> bool:
    """Announcement, ignored and ticket channels: the bot must stay quiet there."""
    return (channel.id in config.IGNORED_CHANNEL_IDS
            or channel.id == config.ANNOUNCEMENT_CHANNEL_ID
            or tickets.is_ticket_channel(channel))


def _unix(timestamp_text) -> int:
    return int(xe.parse_ts(timestamp_text).timestamp())


def _cycle_text(start, end) -> str:
    s = xe.parse_ts(start)
    e = xe.parse_ts(end) - datetime.timedelta(seconds=1)
    return f"{s.day} {s.strftime('%b')} to {e.day} {e.strftime('%b')} UTC"


def _cap(text):
    return text[:1].upper() + text[1:]


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


# ---------------------------------------------------------------------------
# Review card
# ---------------------------------------------------------------------------


class RejectModal(discord.ui.Modal, title="Reject proof"):
    reason = discord.ui.TextInput(label="Reason", max_length=120, required=True,
                                  placeholder="Short reason the member will see")

    def __init__(self, cog, sid):
        super().__init__()
        self.cog, self.sid = cog, sid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        text = str(self.reason).strip().rstrip(".") or "no reason given"
        sub = self.cog.store.get_submission(self.sid)
        if not sub or not self.cog.engine.reject(self.sid, interaction.user.id, text, now_utc()):
            await interaction.followup.send(ERRORS["not_pending"], ephemeral=True)
            return
        await self.cog.reply_to_proof(interaction.guild, sub, f"Not approved: {text}.")
        await self.cog.finalise_card(sub, color=discord.Color.red(),
                                     footer=f"Rejected by {interaction.user}", result=text)
        db.log_action(sub["guild_id"], "POINTS REJECTED", sub["user_id"], str(interaction.user), text[:200])
        await interaction.followup.send("Rejected.", ephemeral=True)


class ReviewView(discord.ui.View):
    """One per submission. Persistent: timeout=None and every custom_id carries the submission id."""

    def __init__(self, cog, sid, channel_key, subtype="", selected=()):
        super().__init__(timeout=None)
        self.cog, self.sid = cog, sid
        cats, multi = xe.menu_categories(channel_key, subtype)
        selected = set(selected)
        options = [
            discord.SelectOption(
                label=f"{_cap(xe.label(c))} (+{pc.CATEGORIES[c]['points']})"[:100],
                value=c, default=c in selected)
            for c in cats
        ]
        select = discord.ui.Select(
            custom_id=f"xpr:{sid}:cat", placeholder="Choose category" if not multi else "Choose one or more",
            min_values=1, max_values=len(options) if multi else 1, options=options, row=0)
        select.callback = self.on_select
        self.add_item(select)
        for name, label, style, cb in (
            ("approve", "Approve", discord.ButtonStyle.green, self.on_approve),
            ("reject", "Reject", discord.ButtonStyle.red, self.on_reject),
            ("zero", "Zero points (spam)", discord.ButtonStyle.secondary, self.on_zero),
        ):
            btn = discord.ui.Button(custom_id=f"xpr:{sid}:{name}", label=label, style=style, row=1)
            btn.callback = cb
            self.add_item(btn)
        self.channel_key, self.subtype = channel_key, subtype

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not is_reviewer(member):
            await interaction.response.send_message("Only reviewers can use these controls.", ephemeral=True)
            return False
        sub = self.cog.store.get_submission(self.sid)
        if sub:
            if sub["user_id"] == member.id:
                await interaction.response.send_message("You cannot review your own submission.", ephemeral=True)
                return False
            ref = self.cog.store.get_referral(sub["user_id"])
            if ref and ref["inviter_id"] == member.id:
                await interaction.response.send_message(
                    "You cannot review a submission from someone you invited.", ephemeral=True)
                return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        values = list(interaction.data.get("values", []))
        self.cog.store.set_selected(self.sid, ",".join(values))
        await interaction.response.edit_message(
            view=ReviewView(self.cog, self.sid, self.channel_key, self.subtype, values))

    async def on_approve(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sub = self.cog.store.get_submission(self.sid)
        if not sub:
            await interaction.followup.send(ERRORS["not_found"], ephemeral=True)
            return
        selected = [c for c in (sub["selected"] or "").split(",") if c]
        try:
            res = self.cog.engine.approve(self.sid, selected, interaction.user.id, now_utc())
        except Exception:
            log.exception("approve failed for submission %s", self.sid)
            await interaction.followup.send("Something went wrong. Nothing was awarded, try again.", ephemeral=True)
            return
        if not res.ok:
            await interaction.followup.send(ERRORS.get(res.error, "Could not approve that."), ephemeral=True)
            return
        await self.cog.reply_to_proof(interaction.guild, sub, self.cog.approval_text(res))
        total = sum(a.points for a in res.awards)
        await self.cog.finalise_card(
            sub, color=discord.Color.green(), footer=f"Approved by {interaction.user}",
            result=self.cog.card_result(res))
        db.log_action(sub["guild_id"], "POINTS APPROVED", sub["user_id"], str(interaction.user),
                      ", ".join(f"{a.category} +{a.points}" for a in res.awards)[:200])
        await interaction.followup.send(f"Approved. {total} points awarded.", ephemeral=True)

    async def on_reject(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RejectModal(self.cog, self.sid))

    async def on_zero(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sub = self.cog.store.get_submission(self.sid)
        strikes = self.cog.engine.zero_points(self.sid, interaction.user.id, now_utc()) if sub else None
        if strikes is None:
            await interaction.followup.send(ERRORS["not_pending"], ephemeral=True)
            return
        await self.cog.finalise_card(sub, color=discord.Color.dark_grey(),
                                     footer=f"Zero points by {interaction.user}",
                                     result=f"Marked as spam. Strike {strikes}.")
        db.log_action(sub["guild_id"], "POINTS ZEROED", sub["user_id"], str(interaction.user), f"strike {strikes}")
        if strikes >= pc.STRIKE_ALERT_AT:
            await self.cog.staff_alert(
                f"<@{sub['user_id']}> now has {_plural(strikes, 'strike')} for spam proof. Please take a look.")
        await interaction.followup.send(f"Zero points recorded. That is strike {strikes}.", ephemeral=True)


class LeaderboardView(discord.ui.View):
    def __init__(self, owner_id, make_embed, pages, page=0):
        super().__init__(timeout=180)
        self.owner_id, self.make_embed, self.pages, self.page = owner_id, make_embed, pages, page
        self._sync()

    def _sync(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Run /xpleaderboard yourself to page through it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction, button):
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.make_embed(self.page), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.make_embed(self.page), view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class XPCog(commands.Cog):
    def __init__(self, bot, store, engine):
        self.bot, self.store, self.engine = bot, store, engine
        # guild id -> {code: {"uses", "max", "inviter", "gone"}}
        self._invites = {}
        self._invites_ok = {}

    # ---- lifecycle -------------------------------------------------------
    async def cog_load(self):
        # One bad row (for example a card whose channel is no longer a proof channel) must
        # never stop the whole bot from starting.
        for sub in self.store.pending_with_cards():
            try:
                key = config.PROOF_KEY_BY_ID.get(sub["channel_id"], "")
                if not key:
                    log.warning("Pending submission %s is in a channel that is no longer a proof channel.", sub["id"])
                    continue
                subtype = "help" if "help" in (sub["flags"] or "").split(",") else ""
                selected = [c for c in (sub["selected"] or "").split(",") if c]
                self.bot.add_view(ReviewView(self, sub["id"], key, subtype, selected),
                                  message_id=sub["review_message_id"])
            except Exception:
                log.exception("Could not restore the review card for submission %s", sub.get("id"))
        for w in self.engine.rules.warnings:
            log.warning(w)
        if config.POINTS_TZ.upper() != "UTC":
            log.warning("POINTS_TZ is '%s' but points always run on UTC; the setting is ignored.", config.POINTS_TZ)
        for name in ("REACH_CHANNEL_ID", "ACADEMY_CHANNEL_ID"):
            if not getattr(config, name):
                log.warning("%s is not set, so that proof channel is off.", name)
        if not config.STAFF_REVIEW_CHANNEL_ID:
            log.warning("No STAFF_REVIEW_CHANNEL_ID, STAFF_CHANNEL_ID or LOG_CHANNEL_ID: review cards cannot be posted.")
        log.info("Points ready. Proof channels: %s. Cycle %s (starts %s, %s days).",
                 config.PROOF_CHANNELS, self.engine.cycle_of(now_utc()),
                 self.engine.rules.cycle_start, self.engine.rules.cycle_length)

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds:
            for key, cid in config.PROOF_CHANNELS.items():
                ch = g.get_channel(cid)
                if ch is None:
                    continue
                if is_silent_channel(ch):
                    log.warning("Proof channel %s (#%s) is in the ignored, announcement or ticket list, "
                                "so the bot stays silent there and proof intake is off.", key, ch.name)
            await self._cache_invites(g)
        if not self._hourly.is_running():
            self._hourly.start()
        if config.XP_LEADERBOARD_ENABLED and config.XP_ANNOUNCE_CHANNEL_ID and not self._daily_board.is_running():
            self._daily_board.start()
            log.info("Daily points leaderboard started (%02d:00 UTC).", config.XP_LEADERBOARD_HOUR)

    async def cog_unload(self):
        self._hourly.cancel()
        self._daily_board.cancel()

    # ---- small helpers ---------------------------------------------------
    async def staff_alert(self, text):
        ch = self.bot.get_channel(config.STAFF_REVIEW_CHANNEL_ID)
        if ch:
            try:
                await ch.send(text, allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:
                log.error("staff alert failed: %s", e)

    async def reply_to_proof(self, guild, sub, text):
        ch = guild.get_channel(sub["channel_id"]) if guild else None
        if not ch:
            return
        try:
            msg = await ch.fetch_message(sub["message_id"])
            await msg.reply(text, mention_author=False)
        except discord.NotFound:
            try:
                await ch.send(f"<@{sub['user_id']}> {text}", allowed_mentions=discord.AllowedMentions(users=True))
            except Exception as e:
                log.error("proof reply failed: %s", e)
        except Exception as e:
            log.error("proof reply failed: %s", e)

    async def finalise_card(self, sub, *, color, footer, result=None):
        ch = self.bot.get_channel(config.STAFF_REVIEW_CHANNEL_ID)
        if not ch or not sub["review_message_id"]:
            return
        try:
            msg = await ch.fetch_message(sub["review_message_id"])
            e = msg.embeds[0] if msg.embeds else discord.Embed()
            e.color = color
            e.set_footer(text=footer)
            if result:
                e.add_field(name="Result", value=result[:1000], inline=False)
            await msg.edit(embed=e, view=None)
        except Exception as ex:
            log.error("could not update review card %s: %s", sub["review_message_id"], ex)

    @staticmethod
    def approval_text(res):
        parts = []
        for a in res.awards:
            what = xe.label(a.category)
            if a.points > 0:
                note = f" ({a.notes[0]})" if a.notes else ""
                parts.append(f"+{a.points} {a.lane} XP for {what}{note}")
            else:
                why = a.notes[0] if a.notes else "no points"
                parts.append(f"no points for {what} ({why})")
        text = "Approved. " + ", ".join(parts) + "."
        mine = [b for b in res.bonuses if res.awards and b.user_id == res.awards[0].user_id]
        if mine:
            text += " Bonus: " + ", ".join(f"+{b.points} {b.lane} XP, {b.notes[0] if b.notes else b.category}" for b in mine) + "."
        return text

    @staticmethod
    def card_result(res):
        lines = []
        for a in res.awards:
            note = f" ({'; '.join(a.notes)})" if a.notes else ""
            lines.append(f"{_cap(xe.label(a.category))}: +{a.points} {a.lane}{note}")
        for b in res.bonuses:
            lines.append(f"Bonus for <@{b.user_id}>: +{b.points} {b.lane}, {b.notes[0] if b.notes else b.category}")
        return "\n".join(lines) or "Approved."

    def hide_fn(self, guild):
        """Hidden from leaderboards: people who left, and immune-role holders (Elite, staff)."""
        def hide(uid):
            m = guild.get_member(uid)
            return m is None or moderation._is_immune(m)
        return hide

    # ---- intake ----------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        key = config.PROOF_KEY_BY_ID.get(message.channel.id)
        if not key:
            return
        if is_silent_channel(message.channel):
            return  # the bot sends nothing in announcement, ignored or ticket channels
        try:
            await self._intake(message, key)
        except Exception:
            log.exception("proof intake failed for message %s", message.id)

    async def _reply(self, message, text):
        try:
            await message.reply(text, mention_author=False)
        except Exception as e:
            log.error("intake reply failed: %s", e)

    async def _image_hash(self, message):
        for att in message.attachments:
            if (att.content_type or "").startswith("image/") and att.size <= MAX_HASH_BYTES:
                try:
                    return hashlib.sha256(await att.read()).hexdigest()
                except Exception as e:
                    log.warning("could not hash attachment: %s", e)
                return None
        return None

    async def _intake(self, message, key):
        uid = message.author.id
        if self.store.is_excluded(uid):
            return
        created = xe.ts(message.created_at)
        lane = pc.CHANNEL_LANES[key]
        check = xe.check_intake(key, message.content, len(message.attachments))

        if not check.ok:
            if check.store_rejected:
                self.store.add_submission(
                    guild_id=message.guild.id, user_id=uid, channel_id=message.channel.id,
                    message_id=message.id, lane=lane, lesson_day=None, content_hash=None, url_key=None,
                    within_window=False, created_at=created, status="rejected", reason=check.code)
            await self._reply(message, check.reply)
            return

        content_hash = await self._image_hash(message)
        if self.engine.check_duplicate(uid, key, check.url_key, content_hash):
            await self._reply(message, xe.MSG_DUPLICATE)
            return

        flags = []
        if check.subtype == "help":
            flags.append("help")
        if self.engine.young_account(message.author.created_at, message.created_at):
            flags.append("new_account")
        if self.store.has_rejected_match(check.url_key, content_hash):
            flags.append("suspect")
        within = key == "reach" and self.engine.within_official_window(created)

        sid = self.store.add_submission(
            guild_id=message.guild.id, user_id=uid, channel_id=message.channel.id, message_id=message.id,
            lane=lane, lesson_day=check.lesson_day, content_hash=content_hash, url_key=check.url_key,
            within_window=within, created_at=created, flags=",".join(flags))
        await self._reply(message, xe.MSG_RECEIVED)
        await self._post_card(message, sid, key, check, flags, within)

    async def _post_card(self, message, sid, key, check, flags, within):
        ch = self.bot.get_channel(config.STAFF_REVIEW_CHANNEL_ID)
        if not ch:
            log.error("Submission %s has no review channel to post to.", sid)
            return
        lane = pc.CHANNEL_LANES[key]
        e = discord.Embed(title=f"Proof #{sid}", color=discord.Color.gold(),
                          description=(message.content or "(no text)")[:500])
        e.add_field(name="Member", value=f"{message.author.mention} ({message.author.id})", inline=True)
        e.add_field(name="Lane", value=LANE_TITLES[lane], inline=True)
        e.add_field(name="Channel", value=message.channel.mention, inline=True)
        e.add_field(name="Link", value=f"[Jump to proof]({message.jump_url})", inline=True)
        if check.lesson_day is not None:
            e.add_field(name="Lesson day", value=str(check.lesson_day), inline=True)
        if message.attachments:
            e.add_field(name="Attachments", value=str(len(message.attachments)), inline=True)
        notes = []
        if key == "reach":
            notes.append("Inside an official post window." if within else "Outside any official post window.")
        if "new_account" in flags:
            notes.append(f"New account (under {config.MIN_ACCOUNT_AGE_DAYS} days old).")
        if "suspect" in flags:
            notes.append("Matches earlier proof that was rejected or withdrawn.")
        if notes:
            e.add_field(name="Flags", value="\n".join(notes), inline=False)
        view = ReviewView(self, sid, key, check.subtype)
        try:
            msg = await ch.send(embed=e, view=view)
            self.store.set_review_message(sid, msg.id)
        except Exception as ex:
            log.error("Failed to post review card for submission %s: %s", sid, ex)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        await self._withdraw(payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        for mid in payload.message_ids:
            await self._withdraw(payload.channel_id, mid)

    async def _withdraw(self, channel_id, message_id):
        if channel_id not in config.PROOF_CHANNEL_IDS:
            return
        sub = self.store.get_pending_by_message(channel_id, message_id)
        if not sub or not self.engine.withdraw(sub["id"], now_utc()):
            return
        ch = self.bot.get_channel(config.STAFF_REVIEW_CHANNEL_ID)
        if ch and sub["review_message_id"]:
            try:
                await ch.get_partial_message(sub["review_message_id"]).delete()
            except Exception as e:
                log.warning("could not remove review card: %s", e)

    # ---- invites and referrals -------------------------------------------
    async def _cache_invites(self, guild):
        try:
            live = await guild.invites()
        except discord.Forbidden:
            self._invites_ok[guild.id] = False
            log.warning("No Manage Server permission in '%s': invite tracking is off, members can use /invitedby.", guild.name)
            return
        except Exception as e:
            log.warning("Could not read invites for '%s': %s", guild.name, e)
            return
        self._invites_ok[guild.id] = True
        cache = {i.code: {"uses": i.uses or 0, "max": i.max_uses or 0,
                          "inviter": i.inviter.id if i.inviter else None, "gone": False} for i in live}
        self._invites[guild.id] = cache
        stored = self.store.invite_cache()
        for code, inv in cache.items():
            if stored.get(code) != inv["uses"]:
                self.store.invite_cache_set(code, inv["uses"])
        for code in set(stored) - set(cache):
            self.store.invite_cache_delete(code)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if not invite.guild:
            return
        self._invites.setdefault(invite.guild.id, {})[invite.code] = {
            "uses": invite.uses or 0, "max": invite.max_uses or 0,
            "inviter": invite.inviter.id if invite.inviter else None, "gone": False}
        self.store.invite_cache_set(invite.code, invite.uses or 0)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if not invite.guild:
            return
        cache = self._invites.get(invite.guild.id, {})
        prev = cache.get(invite.code)
        # A single-use invite is deleted the moment it is used. Keep it until the join is
        # processed so that join can still be attributed.
        if prev and prev["max"] and prev["uses"] + 1 >= prev["max"]:
            prev["gone"] = True
        else:
            cache.pop(invite.code, None)
        self.store.invite_cache_delete(invite.code)

    async def _find_inviter(self, guild):
        """The one inviter whose invite use count went up, or None when it is unclear."""
        if not self._invites_ok.get(guild.id):
            return None
        try:
            live = {i.code: i for i in await guild.invites()}
        except Exception as e:
            log.warning("invite lookup failed: %s", e)
            return None
        cache = self._invites.get(guild.id, {})
        candidates = []
        for code, inv in live.items():
            prev = cache.get(code)
            if (prev is None and (inv.uses or 0) > 0) or (prev is not None and (inv.uses or 0) > prev["uses"]):
                candidates.append(inv.inviter.id if inv.inviter else None)
        for code, prev in cache.items():
            if code not in live and prev["max"] and prev["uses"] + 1 >= prev["max"]:
                candidates.append(prev["inviter"])
        self._invites[guild.id] = {c: {"uses": i.uses or 0, "max": i.max_uses or 0,
                                       "inviter": i.inviter.id if i.inviter else None, "gone": False}
                                   for c, i in live.items()}
        for c, i in live.items():
            self.store.invite_cache_set(c, i.uses or 0)
        if len(candidates) == 1 and candidates[0]:
            return candidates[0]
        return None

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        try:
            inviter_id = await self._find_inviter(member.guild)
            if inviter_id:
                await self._attribute(member, inviter_id)
        except Exception:
            log.exception("referral attribution failed for %s", member.id)

    async def _attribute(self, member, inviter_id):
        inviter = member.guild.get_member(inviter_id)
        if inviter is None or inviter.bot:
            return None
        res = self.engine.register_referral(
            member.guild.id, member.id, inviter_id, member.joined_at or now_utc(), member.created_at, now_utc())
        if res["status"] == "attributed":
            log.info("Referral: %s invited by %s (counted=%s, flagged=%s)",
                     member.id, inviter_id, res["counted"], res["flagged"])
            if res["flagged"]:
                await self.staff_alert(
                    f"Referral flagged: <@{member.id}> joined from <@{inviter_id}>'s invite on a very new account. "
                    f"Later referral payouts are held. Use /referralclear to release them.")
        return res

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if not config.ELITE_ROLE_ID:
            return
        had = any(r.id == config.ELITE_ROLE_ID for r in before.roles)
        has = any(r.id == config.ELITE_ROLE_ID for r in after.roles)
        if has and not had:
            for a in self.engine.evaluate_referral(after.id, now_utc(), has_elite=True):
                log.info("Referral stage paid: %s +%s to %s", a.category, a.points, a.user_id)

    # ---- scheduled tasks -------------------------------------------------
    @tasks.loop(hours=1)
    async def _hourly(self):
        elite = set()
        if config.ELITE_ROLE_ID:
            for g in self.bot.guilds:
                role = g.get_role(config.ELITE_ROLE_ID)
                if role:
                    elite |= {m.id for m in role.members}
        for a in self.engine.sweep_referrals(now_utc(), elite):
            log.info("Referral sweep paid: %s +%s to %s", a.category, a.points, a.user_id)

    @_hourly.before_loop
    async def _hourly_ready(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=datetime.time(hour=config.XP_LEADERBOARD_HOUR, minute=0, tzinfo=UTC))
    async def _daily_board(self):
        today = xe.day_key(now_utc())
        if db.kv_get("points_leaderboard_last_date") == today:
            return
        ch = self.bot.get_channel(config.XP_ANNOUNCE_CHANNEL_ID)
        if not ch or is_silent_channel(ch):
            log.warning("XP_ANNOUNCE_CHANNEL_ID is not usable; skipping the daily leaderboard.")
            return
        embed = self.board_summary_embed(ch.guild)
        db.kv_set("points_leaderboard_last_date", today)
        if embed is None:
            return
        try:
            await ch.send(embed=embed)
            log.info("Posted the daily points leaderboard.")
        except Exception as e:
            log.error("Failed to post the daily leaderboard: %s", e)

    @_daily_board.before_loop
    async def _daily_ready(self):
        await self.bot.wait_until_ready()

    def _name(self, guild, uid):
        m = guild.get_member(uid)
        return m.display_name if m else f"User {uid}"

    def board_summary_embed(self, guild):
        now = now_utc()
        hide = self.hide_fn(guild)
        n = self.engine.cycle_of(now)
        start, end = self.engine.cycle_range(n)
        e = discord.Embed(title="Points leaderboard", color=discord.Color.gold(),
                          description=f"Cycle {n}, {_cycle_text(start, end)}.")
        any_rows = False
        for lane in pc.LANES:
            rows = self.engine.leaderboard(lane, "cycle", now, hide, limit=5)
            if rows:
                any_rows = True
            body = "\n".join(f"{r}. **{self._name(guild, u)}** -- {p}" for r, u, p in rows) or "No points yet."
            e.add_field(name=f"{LANE_TITLES[lane]} XP", value=body, inline=True)
        return e if any_rows else None

    # ---- public commands -------------------------------------------------
    @app_commands.command(name="xp", description="Check your Reach and Builder XP for this cycle.")
    @app_commands.describe(member="Whose XP to check. Leave empty for your own.")
    async def xp(self, i: discord.Interaction, member: discord.Member = None):
        target = member or i.user
        s = self.engine.user_summary(target.id, now_utc(), self.hide_fn(i.guild))
        e = discord.Embed(title=f"XP for {target.display_name}", color=discord.Color.blurple(),
                          description=f"Cycle {s['cycle']}, {_cycle_text(s['start'], s['end'])}.")
        for lane, need in ((pc.REACH, s["min_reach"]), (pc.BUILDER, s["min_builder"])):
            rank = f"rank {s[lane + '_rank']}" if s[lane + "_rank"] else "unranked"
            e.add_field(name=f"{LANE_TITLES[lane]} XP",
                        value=f"{s[lane]} of {need} needed ({rank})\nAll time: {s[lane + '_alltime']}", inline=True)
        await i.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="xpleaderboard", description="Top XP earners.")
    @app_commands.describe(lane="Which lane to show.", period="This cycle or all time.")
    @app_commands.choices(
        lane=[app_commands.Choice(name="Reach", value="reach"), app_commands.Choice(name="Builder", value="builder"),
              app_commands.Choice(name="Combined", value="combined")],
        period=[app_commands.Choice(name="This cycle", value="cycle"), app_commands.Choice(name="All time", value="alltime")])
    async def xpleaderboard(self, i: discord.Interaction, lane: str = "combined", period: str = "cycle"):
        await i.response.defer()
        now = now_utc()
        rows = self.engine.leaderboard(lane, period, now, self.hide_fn(i.guild), limit=pc.LEADERBOARD_FETCH)
        if not rows:
            await i.followup.send("No points earned yet.", ephemeral=True)
            return
        size = pc.LEADERBOARD_PAGE_SIZE
        pages = (len(rows) + size - 1) // size
        if period == "cycle":
            n = self.engine.cycle_of(now)
            when = f"cycle {n}, {_cycle_text(*self.engine.cycle_range(n))}"
        else:
            when = "all time"

        def make(page):
            chunk = rows[page * size:(page + 1) * size]
            e = discord.Embed(title=f"{LANE_TITLES[lane]} leaderboard", color=discord.Color.gold(),
                              description="\n".join(f"{r}. **{self._name(i.guild, u)}** -- {p} XP" for r, u, p in chunk))
            e.set_footer(text=f"{when} | page {page + 1} of {pages}")
            return e

        await i.followup.send(embed=make(0), view=LeaderboardView(i.user.id, make, pages))

    @app_commands.command(name="referrals", description="See who you have invited and how far they have got.")
    async def referrals(self, i: discord.Interaction):
        rows = self.store.referrals_by_inviter(i.user.id)
        own = self.store.get_referral(i.user.id)
        lines = []
        for r in rows:
            if not r["counted"]:
                state = "not counted (cycle limit reached)"
            elif r["status"] == "expired":
                state = "expired"
            else:
                bits = ["joined"]
                if r["stage_lesson"]: bits.append("first lesson")
                if r["stage_active"]: bits.append("active")
                if r["stage_elite"]: bits.append("Elite")
                state = ", ".join(bits)
            if r["flagged"]:
                state += " (on hold, new account)"
            lines.append(f"<@{r['invitee_id']}>: {state}")
        text = "\n".join(lines) or "You have not invited anyone yet."
        if own:
            text += f"\n\nYou were invited by <@{own['inviter_id']}>."
        e = discord.Embed(title="Your referrals", description=text[:4000], color=discord.Color.blurple())
        await i.response.send_message(embed=e, ephemeral=True,
                                      allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="invitedby", description="Tell us who invited you, if the invite was not tracked.")
    @app_commands.describe(member="The member who invited you.")
    async def invitedby(self, i: discord.Interaction, member: discord.Member):
        me = i.user
        if member.id == me.id:
            await i.response.send_message("You cannot name yourself.", ephemeral=True)
            return
        if member.bot:
            await i.response.send_message("Pick a member, not a bot.", ephemeral=True)
            return
        joined = me.joined_at or now_utc()
        if not self.engine.invitedby_allowed(joined, now_utc()):
            await i.response.send_message(
                f"You can only use this within {pc.REFERRAL['invitedby_window_days']} days of joining.", ephemeral=True)
            return
        if self.store.get_referral(me.id):
            await i.response.send_message("Your inviter is already recorded.", ephemeral=True)
            return
        res = await self._attribute(me, member.id)
        if not res or res["status"] != "attributed":
            await i.response.send_message("That could not be recorded.", ephemeral=True)
            return
        await i.response.send_message(f"Thanks. {member.display_name} is recorded as your inviter.", ephemeral=True)

    @app_commands.command(name="xpost", description="Announce a new X post and open the proof window.")
    @app_commands.describe(link="Link to the X post.", note="Optional message to go with it.")
    @staff_only()
    async def xpost(self, i: discord.Interaction, link: str, note: str = ""):
        await i.response.defer(ephemeral=True)
        if not xe.is_x_status_link(link):
            await i.followup.send("Give a link to an X post.", ephemeral=True)
            return
        target = i.channel
        if config.XP_ANNOUNCE_CHANNEL_ID:
            target = i.guild.get_channel(config.XP_ANNOUNCE_CHANNEL_ID) or i.channel
        if is_silent_channel(target):
            await i.followup.send(f"{target.mention} is a silent channel, so I will not post there.", ephemeral=True)
            return
        pid, closes = self.engine.open_official_post(link, now_utc())
        reach = i.guild.get_channel(config.REACH_CHANNEL_ID) if config.REACH_CHANNEL_ID else None
        where = reach.mention if reach else "the Reach channel"
        e = discord.Embed(
            title="New post from NEXTGEN",
            description=(note or "Engage with the post, then share your proof.") + f"\n\n{link}",
            color=discord.Color.blurple())
        e.add_field(
            name="How to earn Reach XP",
            value=f"Engage on X, then post your reply or repost link in {where}. "
                  f"A reviewer checks it and awards points. Proof posted before <t:{_unix(closes)}:t> "
                  f"counts as inside the official window.",
            inline=False)
        e.set_footer(text=f"Posted by {i.user.display_name}")
        ping = f"<@&{config.XP_PING_ROLE_ID}>" if config.XP_PING_ROLE_ID else None
        try:
            await target.send(content=ping, embed=e,
                              allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False))
        except discord.Forbidden:
            await i.followup.send("I cannot post there (missing permissions).", ephemeral=True)
            return
        db.log_action(i.guild.id, "OFFICIAL POST", i.user.id, str(i.user), link[:200])
        await i.followup.send(f"Posted in {target.mention}. Window #{pid} is open until <t:{_unix(closes)}:t>.", ephemeral=True)

    @app_commands.command(name="officialpost", description="Open a proof window for an official X post, without announcing it.")
    @app_commands.describe(link="Link to the X post.")
    @staff_only()
    async def officialpost(self, i: discord.Interaction, link: str):
        if not xe.is_x_status_link(link):
            await i.response.send_message("Give a link to an X post.", ephemeral=True)
            return
        pid, closes = self.engine.open_official_post(link, now_utc())
        db.log_action(i.guild.id, "OFFICIAL POST", i.user.id, str(i.user), link[:200])
        await i.response.send_message(
            f"Window #{pid} is open until <t:{_unix(closes)}:t> (<t:{_unix(closes)}:R>).", ephemeral=True)

    # ---- staff commands --------------------------------------------------
    @app_commands.command(name="award", description="Award points for an event or a manual award.")
    @app_commands.describe(member="Who to award.", category="What the award is for.", reason="Short note for the log.")
    @staff_only()
    async def award(self, i: discord.Interaction, member: discord.Member, category: str, reason: str = ""):
        if category not in pc.CATEGORIES or member.bot:
            await i.response.send_message("Pick a valid category and a member, not a bot.", ephemeral=True)
            return
        a = self.engine.award_manual(i.guild.id, member.id, category, reason, i.user.id, now_utc(), i.id)
        db.log_action(i.guild.id, "POINTS AWARD", member.id, str(i.user), f"{category} +{a.points} {reason}"[:200])
        if a.points:
            msg = f"Awarded +{a.points} {a.lane} XP to {member.display_name} for {xe.label(category)}."
        else:
            msg = f"No points awarded: {a.notes[0] if a.notes else 'nothing to award'}."
        await i.response.send_message(msg, ephemeral=True)

    @award.autocomplete("category")
    async def _award_category(self, i: discord.Interaction, current: str):
        cur = current.lower()
        out = []
        for key, v in pc.CATEGORIES.items():
            if cur in key or cur in v["label"].lower():
                out.append(app_commands.Choice(name=f"{v['label']} (+{v['points']} {v['lane']})"[:100], value=key))
        return out[:25]

    @app_commands.command(name="xpadjust", description="Add or remove points with a reason (corrections).")
    @app_commands.describe(member="Who to adjust.", lane="Which lane.", points="Points to add, or a negative number to remove.",
                           reason="Why. This goes in the ledger.")
    @app_commands.choices(lane=[app_commands.Choice(name="Reach", value="reach"), app_commands.Choice(name="Builder", value="builder")])
    @staff_only()
    async def xpadjust(self, i: discord.Interaction, member: discord.Member, lane: str,
                       points: app_commands.Range[int, -1000, 1000], reason: str):
        if points == 0:
            await i.response.send_message("Points cannot be zero.", ephemeral=True)
            return
        self.engine.adjust(i.guild.id, member.id, lane, points, reason, i.user.id, now_utc(), i.id)
        db.log_action(i.guild.id, "POINTS ADJUST", member.id, str(i.user), f"{lane} {points:+d}: {reason}"[:200])
        await i.response.send_message(f"Adjusted {member.display_name} by {points:+d} {lane} XP.", ephemeral=True)

    @app_commands.command(name="xpexclude", description="Take a member out of points and leaderboards.")
    @app_commands.describe(member="Who to exclude.", reason="Why.")
    @staff_only()
    async def xpexclude(self, i: discord.Interaction, member: discord.Member, reason: str = ""):
        self.store.exclude(member.id, reason)
        db.log_action(i.guild.id, "POINTS EXCLUDE", member.id, str(i.user), reason[:200])
        await i.response.send_message(f"{member.display_name} is excluded from points.", ephemeral=True)

    @app_commands.command(name="xpinclude", description="Put an excluded member back into points.")
    @app_commands.describe(member="Who to include again.")
    @staff_only()
    async def xpinclude(self, i: discord.Interaction, member: discord.Member):
        ok = self.store.include(member.id)
        db.log_action(i.guild.id, "POINTS INCLUDE", member.id, str(i.user), "")
        await i.response.send_message(
            f"{member.display_name} is included again." if ok else f"{member.display_name} was not excluded.", ephemeral=True)

    @app_commands.command(name="referralclear", description="Release a held referral after checking the new account.")
    @app_commands.describe(member="The invited member whose referral is on hold.")
    @staff_only()
    async def referralclear(self, i: discord.Interaction, member: discord.Member):
        has_elite = bool(config.ELITE_ROLE_ID and any(r.id == config.ELITE_ROLE_ID for r in member.roles))
        released = self.engine.clear_flag(member.id, now_utc(), has_elite=has_elite)
        if released is None:
            await i.response.send_message("That member has no referral on record.", ephemeral=True)
            return
        db.log_action(i.guild.id, "REFERRAL CLEARED", member.id, str(i.user), f"{len(released)} payouts released")
        await i.response.send_message(
            f"Cleared. {_plural(len(released), 'payout')} released to the inviter.", ephemeral=True)

    # ---- weekly challenge ------------------------------------------------
    challenge = app_commands.Group(name="challenge", description="Weekly challenge.")

    @challenge.command(name="create", description="Start a weekly challenge that runs until Sunday 23:59 UTC.")
    @app_commands.describe(title="Short title.", description="What members need to do.")
    @staff_only()
    async def challenge_create(self, i: discord.Interaction, title: str, description: str):
        await i.response.defer(ephemeral=True)
        ch = i.guild.get_channel(config.CHALLENGE_CHANNEL_ID) if config.CHALLENGE_CHANNEL_ID else None
        if ch is None:
            await i.followup.send("Set CHALLENGE_CHANNEL_ID to a channel I can post in first.", ephemeral=True)
            return
        if is_silent_channel(ch):
            await i.followup.send(f"{ch.mention} is an announcement, ignored or ticket channel, so I will not post there.", ephemeral=True)
            return
        cid, starts, ends = self.engine.create_challenge(title, description, now_utc())
        build = i.guild.get_channel(config.BUILD_CHANNEL_ID) if config.BUILD_CHANNEL_ID else None
        reach = i.guild.get_channel(config.REACH_CHANNEL_ID) if config.REACH_CHANNEL_ID else None
        e = discord.Embed(title=f"Weekly challenge: {title}", description=description[:3500], color=discord.Color.blurple())
        e.add_field(name="Deadline", value=f"<t:{_unix(ends)}:F> (<t:{_unix(ends)}:R>)", inline=False)
        e.add_field(
            name="How to enter",
            value=(f"Build part: post your build in {build.mention if build else 'the Build channel'}.\n"
                   f"Share part: post about it on X, then add the link in {reach.mention if reach else 'the Reach channel'}.\n"
                   f"Finish both parts on time for a bonus."),
            inline=False)
        try:
            msg = await ch.send(embed=e)
            self.store.set_challenge_message(cid, msg.id)
        except discord.Forbidden:
            await i.followup.send(f"I cannot post in {ch.mention} (missing permissions).", ephemeral=True)
            return
        db.log_action(i.guild.id, "CHALLENGE CREATED", i.user.id, str(i.user), title[:200])
        await i.followup.send(f"Challenge #{cid} is live in {ch.mention} until <t:{_unix(ends)}:F>.", ephemeral=True)

    @challenge.command(name="status", description="Show the current weekly challenge.")
    async def challenge_status(self, i: discord.Interaction):
        ch = self.engine.current_challenge(now_utc())
        if not ch:
            await i.response.send_message("No challenge is running right now.", ephemeral=True)
            return
        e = discord.Embed(title=f"Weekly challenge: {ch['title']}", description=ch["description"][:3500],
                          color=discord.Color.blurple())
        e.add_field(name="Time left", value=f"<t:{_unix(ch['ends_at'])}:R> (<t:{_unix(ch['ends_at'])}:F>)", inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    # ---- cycle review ----------------------------------------------------
    cycle = app_commands.Group(name="cycle", description="Cycle tools.")

    @cycle.command(name="review", description="Post the Elite shortlist for this cycle to the staff channel.")
    @staff_only()
    async def cycle_review(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        ch = self.bot.get_channel(config.STAFF_REVIEW_CHANNEL_ID)
        if not ch:
            await i.followup.send("No staff channel is configured.", ephemeral=True)
            return
        guild = i.guild

        def is_elite(uid):
            m = guild.get_member(uid)
            return bool(config.ELITE_ROLE_ID and m and any(r.id == config.ELITE_ROLE_ID for r in m.roles))

        r = self.engine.cycle_review(now_utc(), self.hide_fn(guild), is_elite)
        rules = self.engine.rules
        e = discord.Embed(title=f"Cycle {r['cycle']} review", color=discord.Color.gold(),
                          description=(f"{_cycle_text(r['start'], r['end'])}. Needs {rules.elite_min_reach} Reach "
                                       f"and {rules.elite_min_builder} Builder XP. {_plural(rules.elite_slots, 'slot')}. "
                                       f"This is a shortlist. Choosing and giving the role is a staff decision."))
        sel = "\n".join(
            f"{n}. <@{x['user_id']}>: {x['reach']} Reach, {x['builder']} Builder ({x['total']} total)"
            for n, x in enumerate(r["selected"], 1)) or "Nobody meets both minimums yet."
        if r["waiting"]:
            sel += f"\n{_plural(r['waiting'], 'more member')} also qualify and are waiting for a slot."
        e.add_field(name="Meets both minimums", value=sel[:1024], inline=False)
        near = "\n".join(
            f"<@{x['user_id']}>: {x['reach']} Reach, {x['builder']} Builder (needs more {x['missing']})"
            for x in r["near_misses"][:10]) or "None."
        e.add_field(name="Near misses (one lane met)", value=near[:1024], inline=False)
        for name, key, unit in (("Top referrer", "top_referrer", "referral XP"),
                                ("Top builder", "top_builder", "Builder XP"),
                                ("Top Reach member", "top_reach", "Reach XP")):
            v = r[key]
            e.add_field(name=name, value=(f"<@{v['user_id']}> ({v['points']} {unit})" if v else "Nobody yet."), inline=True)
        try:
            await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
        except Exception as ex:
            await i.followup.send(f"Could not post to the staff channel: {ex}", ephemeral=True)
            return
        db.log_action(i.guild.id, "CYCLE REVIEW", i.user.id, str(i.user), f"cycle {r['cycle']}")
        await i.followup.send(f"Posted in {ch.mention}.", ephemeral=True)


async def setup(bot):
    store = Store(db._run, "pg")
    store.init_schema()
    engine = xe.Engine(store, xe.Rules.from_config(config))
    await bot.add_cog(XPCog(bot, store, engine))
