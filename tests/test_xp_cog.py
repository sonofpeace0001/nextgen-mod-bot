"""Tests for the Discord layer (xp_cog) using fake Discord objects and an in-memory database.

Run from the repo root:  python -m unittest discover -s tests -t .
"""
import datetime
import os
import sqlite3
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

import config  # noqa: E402
import points_config as pc  # noqa: E402
import xp_cog  # noqa: E402
import xp_engine as xe  # noqa: E402
from points_store import Store, make_sqlite_run  # noqa: E402

UTC = datetime.timezone.utc
GUILD, STAFF_CH, REACH, ACADEMY, BUILD = 1, 500, 11, 12, 13
CHANNELS = {"reach": REACH, "academy": ACADEMY, "build": BUILD}
NOW = datetime.datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def member(uid, admin=False, roles=()):
    return SimpleNamespace(
        id=uid, bot=False, mention=f"<@{uid}>", display_name=f"user{uid}",
        created_at=NOW - datetime.timedelta(days=400), joined_at=NOW - datetime.timedelta(days=30),
        guild_permissions=SimpleNamespace(administrator=admin),
        roles=[SimpleNamespace(id=r) for r in roles])


def proof_message(mid, author, channel_id, content, attachments=()):
    ch = SimpleNamespace(id=channel_id, mention=f"<#{channel_id}>")
    return SimpleNamespace(
        id=mid, author=author, guild=SimpleNamespace(id=GUILD), channel=ch, content=content,
        attachments=list(attachments), created_at=NOW, jump_url=f"https://discord.com/channels/{GUILD}/{channel_id}/{mid}",
        reply=AsyncMock())


def interaction(user, guild, values=None):
    return SimpleNamespace(
        user=user, guild=guild, data={"values": values or []},
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock(),
                                 send_modal=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class CogCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        conn = sqlite3.connect(":memory:")
        self.store = Store(make_sqlite_run(conn), dialect="sqlite")
        self.store.init_schema()
        rules = xe.Rules(cycle_start=datetime.date(2026, 10, 5), cycle_length=14, channel_ids=dict(CHANNELS))
        self.engine = xe.Engine(self.store, rules)

        self.cards = {}  # message id -> card message
        self.card_id = 9000

        async def send_card(embed=None, view=None, **kw):
            self.card_id += 1
            msg = SimpleNamespace(id=self.card_id, embeds=[embed], edit=AsyncMock(), delete=AsyncMock())
            self.cards[msg.id] = msg
            return msg

        self.staff = SimpleNamespace(id=STAFF_CH, mention="<#500>", name="staff-review", send=AsyncMock(side_effect=send_card),
                                     fetch_message=AsyncMock(side_effect=lambda mid: self.cards[mid]),
                                     get_partial_message=lambda mid: self.cards[mid])
        self.proof_msgs = {}
        self.proof_channel = SimpleNamespace(id=BUILD, mention="<#13>", name="build", send=AsyncMock(),
                                             fetch_message=AsyncMock(side_effect=lambda mid: self.proof_msgs[mid]))

        patches = [
            mock.patch.object(config, "PROOF_KEY_BY_ID", {v: k for k, v in CHANNELS.items()}),
            mock.patch.object(config, "PROOF_CHANNEL_IDS", set(CHANNELS.values())),
            mock.patch.object(config, "PROOF_CHANNELS", dict(CHANNELS)),
            mock.patch.object(config, "STAFF_REVIEW_CHANNEL_ID", STAFF_CH),
            mock.patch.object(config, "FOUNDER_ID", 1),
            mock.patch.object(config, "MIN_ACCOUNT_AGE_DAYS", 7),
            mock.patch.object(xp_cog, "db", MagicMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.bot.get_channel = lambda cid: self.staff if cid == STAFF_CH else None
        self.cog = xp_cog.XPCog(self.bot, self.store, self.engine)
        self.guild = SimpleNamespace(
            id=GUILD, get_channel=lambda cid: self.proof_channel if cid == BUILD else None,
            get_member=lambda uid: member(uid))

    # a posted proof, run through intake
    async def submit(self, mid, author_id, channel_key, text, attachments=()):
        msg = proof_message(mid, member(author_id), CHANNELS[channel_key], text, attachments)
        self.proof_msgs[mid] = msg
        await self.cog._intake(msg, channel_key)
        return msg

    def reviewer_view(self, sid):
        sub = self.store.get_submission(sid)
        key = config.PROOF_KEY_BY_ID[sub["channel_id"]]
        return xp_cog.ReviewView(self.cog, sid, key, "")


class TestRegistration(CogCase):
    async def test_commands_register_with_discord_py(self):
        await self.bot.add_cog(self.cog)
        names = {c.name for c in self.bot.tree.get_commands()}
        self.assertTrue({"xp", "xpleaderboard", "referrals", "invitedby", "xpost", "officialpost", "award",
                         "xpadjust", "xpexclude", "xpinclude", "referralclear", "challenge", "cycle"} <= names, names)
        groups = {c.name: {s.name for s in c.commands} for c in self.bot.tree.get_commands()
                  if isinstance(c, discord.app_commands.Group)}
        self.assertEqual(groups["challenge"], {"create", "status"})
        self.assertEqual(groups["cycle"], {"review"})
        # the old single-points commands are gone
        self.assertNotIn("xproof", names)
        self.assertNotIn("addxp", names)

    async def test_review_view_ids_carry_the_submission_id_and_menu_matches_channel(self):
        view = xp_cog.ReviewView(self.cog, 42, "reach", "")
        ids = {child.custom_id for child in view.children}
        self.assertEqual(ids, {"xpr:42:cat", "xpr:42:approve", "xpr:42:reject", "xpr:42:zero"})
        self.assertIsNone(view.timeout)
        select = next(c for c in view.children if c.custom_id == "xpr:42:cat")
        self.assertEqual(select.max_values, 1)
        self.assertEqual({o.value for o in select.options},
                         {k for k, v in pc.CATEGORIES.items() if "reach" in v["channels"]})
        academy = xp_cog.ReviewView(self.cog, 43, "academy", "")
        sel = next(c for c in academy.children if c.custom_id == "xpr:43:cat")
        self.assertEqual((sel.max_values, {o.value for o in sel.options}),
                         (3, {"academy_lesson", "academy_test", "academy_proof"}))
        helping = xp_cog.ReviewView(self.cog, 44, "academy", "help")
        self.assertEqual(next(c for c in helping.children if c.custom_id == "xpr:44:cat").max_values, 1)


class TestIntake(CogCase):
    async def test_valid_proof_creates_a_pending_submission_and_a_review_card(self):
        msg = await self.submit(100, 7, "build", "my bot https://example.com/demo")
        msg.reply.assert_awaited_once()
        self.assertEqual(msg.reply.await_args.args[0], xe.MSG_RECEIVED)
        sub = self.store.get_submission(1)
        self.assertEqual((sub["status"], sub["lane"], sub["url_key"]), ("pending", "builder", "example.com/demo"))
        self.assertEqual(sub["review_message_id"], 9001)
        embed = self.staff.send.await_args.kwargs["embed"]
        self.assertIn("Builder", [f.value for f in embed.fields])
        self.assertIsInstance(self.staff.send.await_args.kwargs["view"], xp_cog.ReviewView)

    async def test_reach_card_is_reach_lane_and_shows_the_window_flag(self):
        self.engine.open_official_post("https://x.com/G_NEXTGEN/status/1", NOW - datetime.timedelta(minutes=30))
        await self.submit(101, 7, "reach", "https://x.com/me/status/55")
        sub = self.store.get_submission(1)
        self.assertEqual((sub["lane"], sub["within_window"]), ("reach", 1))
        flags = next(f.value for f in self.staff.send.await_args.kwargs["embed"].fields if f.name == "Flags")
        self.assertIn("Inside an official post window", flags)

    async def test_multi_day_academy_proof_is_rejected_and_recorded(self):
        msg = await self.submit(102, 7, "academy", "day 1-2 https://example.com/x")
        self.assertEqual(msg.reply.await_args.args[0], xe.MSG_MULTI_DAY)
        sub = self.store.get_submission(1)
        self.assertEqual((sub["status"], sub["reason"]), ("rejected", "multi_day"))
        self.staff.send.assert_not_awaited()

    async def test_missing_proof_and_missing_day_get_a_reply_and_no_submission(self):
        a = await self.submit(103, 7, "build", "no proof here")
        self.assertEqual(a.reply.await_args.args[0], xe.MSG_NEEDS_PROOF)
        b = await self.submit(104, 7, "academy", "done https://example.com/x")
        self.assertEqual(b.reply.await_args.args[0], xe.MSG_NEEDS_DAY)
        self.assertIsNone(self.store.get_submission(1))

    async def test_duplicate_proof_from_another_member_is_refused(self):
        await self.submit(105, 7, "build", "https://example.com/same?utm=1")
        dup = await self.submit(106, 8, "build", "https://www.example.com/same")
        self.assertEqual(dup.reply.await_args.args[0], xe.MSG_DUPLICATE)
        self.assertIsNone(self.store.get_submission(2))

    async def test_new_account_is_flagged_on_the_card(self):
        msg = proof_message(107, member(7), BUILD, "https://example.com/new")
        msg.author.created_at = NOW - datetime.timedelta(days=2)
        await self.cog._intake(msg, "build")
        flags = next(f.value for f in self.staff.send.await_args.kwargs["embed"].fields if f.name == "Flags")
        self.assertIn("New account", flags)

    async def test_excluded_members_and_bots_are_ignored(self):
        self.store.exclude(7, "test")
        msg = await self.submit(108, 7, "build", "https://example.com/z")
        msg.reply.assert_not_awaited()
        bot_msg = proof_message(109, member(9), BUILD, "https://example.com/y")
        bot_msg.author.bot = True
        await self.cog.on_message(bot_msg)
        bot_msg.reply.assert_not_awaited()


class TestReview(CogCase):
    async def approve_flow(self, view, reviewer, category):
        sel = interaction(reviewer, self.guild, values=[category])
        await view.on_select(sel)
        click = interaction(reviewer, self.guild)
        await view.on_approve(click)
        return click

    async def test_double_click_on_approve_pays_once(self):
        await self.submit(200, 7, "build", "https://example.com/p")
        view = self.reviewer_view(1)
        boss = member(1)
        first = await self.approve_flow(view, boss, "build_demo")
        second = interaction(boss, self.guild)
        await view.on_approve(second)
        self.assertEqual(sum(r["pts"] for r in self.store.lane_totals("0", "9")), 20)
        self.assertIn("already been reviewed", second.followup.send.await_args.args[0])
        # two replies in total: "Received" at intake and "Approved" once; the second click adds none
        self.assertEqual(self.proof_msgs[200].reply.await_count, 2)
        self.assertEqual(self.proof_msgs[200].reply.await_args.args[0],
                         "Approved. +20 builder XP for build demo.")
        card = self.cards[9001]
        card.edit.assert_awaited_once()
        self.assertIsNone(card.edit.await_args.kwargs["view"])

    async def test_approve_without_a_category_awards_nothing(self):
        await self.submit(201, 7, "build", "https://example.com/q")
        click = interaction(member(1), self.guild)
        await self.reviewer_view(1).on_approve(click)
        self.assertIn("Choose a category", click.followup.send.await_args.args[0])
        self.assertEqual(self.store.get_submission(1)["status"], "pending")

    async def test_selection_survives_a_restart_because_it_is_stored(self):
        await self.submit(202, 7, "build", "https://example.com/r")
        await self.reviewer_view(1).on_select(interaction(member(1), self.guild, values=["build_project"]))
        rebuilt = self.reviewer_view(1)  # a fresh view, as after a restart
        await rebuilt.on_approve(interaction(member(1), self.guild))
        self.assertEqual(self.proof_msgs[202].reply.await_args.args[0], "Approved. +30 builder XP for build project.")

    async def test_reject_sends_not_approved_with_the_reason(self):
        await self.submit(203, 7, "build", "https://example.com/s")
        modal = xp_cog.RejectModal(self.cog, 1)
        modal.reason._value = "screenshot is blurry"
        inter = interaction(member(1), self.guild)
        await modal.on_submit(inter)
        self.assertEqual(self.proof_msgs[203].reply.await_args.args[0], "Not approved: screenshot is blurry.")
        self.assertEqual(self.store.get_submission(1)["status"], "rejected")

    async def test_zero_points_alerts_staff_at_three_strikes(self):
        for n in range(3):
            await self.submit(300 + n, 7, "build", f"https://example.com/spam{n}")
            await self.reviewer_view(n + 1).on_zero(interaction(member(1), self.guild))
        alert = self.staff.send.await_args_list[-1]
        self.assertIn("3 strikes", alert.args[0])
        self.assertEqual(self.store.get_strikes(7), 3)

    async def test_only_reviewers_may_use_the_controls(self):
        await self.submit(400, 7, "build", "https://example.com/t")
        view = self.reviewer_view(1)
        nobody = interaction(member(50), self.guild)
        self.assertFalse(await view.interaction_check(nobody))
        nobody.response.send_message.assert_awaited_once()
        with mock.patch.object(config, "REVIEWER_ROLE_IDS", {777}):
            self.assertTrue(await view.interaction_check(interaction(member(51, roles=[777]), self.guild)))

    async def test_reviewers_cannot_review_themselves_or_people_they_invited(self):
        await self.submit(401, 7, "build", "https://example.com/u")
        view = self.reviewer_view(1)
        own = interaction(member(7, admin=True), self.guild)
        self.assertFalse(await view.interaction_check(own))
        self.assertIn("own submission", own.response.send_message.await_args.args[0])
        self.engine.register_referral(GUILD, 7, 60, NOW, NOW - datetime.timedelta(days=400), NOW)
        inviter = interaction(member(60, admin=True), self.guild)
        self.assertFalse(await view.interaction_check(inviter))
        self.assertIn("invited", inviter.response.send_message.await_args.args[0])
        self.assertTrue(await view.interaction_check(interaction(member(61, admin=True), self.guild)))


class TestLifecycle(CogCase):
    async def test_pending_cards_are_reregistered_after_a_restart(self):
        await self.submit(500, 7, "build", "https://example.com/v")
        await self.submit(501, 8, "build", "https://example.com/w")
        self.bot.add_view = MagicMock()
        await self.cog.cog_load()
        self.assertEqual(self.bot.add_view.call_count, 2)
        ids = sorted(call.kwargs["message_id"] for call in self.bot.add_view.call_args_list)
        self.assertEqual(ids, [9001, 9002])
        for call in self.bot.add_view.call_args_list:
            self.assertIsInstance(call.args[0], xp_cog.ReviewView)

    async def test_reviewed_cards_are_not_reregistered(self):
        await self.submit(502, 7, "build", "https://example.com/x")
        self.engine.reject(1, 1, "no", NOW)
        self.bot.add_view = MagicMock()
        await self.cog.cog_load()
        self.bot.add_view.assert_not_called()

    async def test_deleting_a_pending_proof_withdraws_it_and_removes_the_card(self):
        await self.submit(600, 7, "build", "https://example.com/y")
        await self.cog.on_raw_message_delete(SimpleNamespace(channel_id=BUILD, message_id=600))
        self.assertEqual(self.store.get_submission(1)["status"], "withdrawn")
        self.cards[9001].delete.assert_awaited_once()

    async def test_deleting_an_approved_proof_keeps_the_award(self):
        await self.submit(601, 7, "build", "https://example.com/z")
        await self.reviewer_view(1).on_select(interaction(member(1), self.guild, values=["build_share"]))
        await self.reviewer_view(1).on_approve(interaction(member(1), self.guild))
        await self.cog.on_raw_message_delete(SimpleNamespace(channel_id=BUILD, message_id=601))
        self.assertEqual(self.store.get_submission(1)["status"], "approved")
        self.assertEqual(sum(r["pts"] for r in self.store.lane_totals("0", "9")), 10)

    async def test_deletes_outside_proof_channels_are_ignored(self):
        await self.cog.on_raw_message_delete(SimpleNamespace(channel_id=999, message_id=1))


class TestSilentChannels(unittest.TestCase):
    def test_announcement_ignored_and_ticket_channels_are_silent(self):
        with mock.patch.object(config, "IGNORED_CHANNEL_IDS", {5}), mock.patch.object(config, "ANNOUNCEMENT_CHANNEL_ID", 6):
            self.assertTrue(xp_cog.is_silent_channel(SimpleNamespace(id=5, name="general")))
            self.assertTrue(xp_cog.is_silent_channel(SimpleNamespace(id=6, name="news")))
            self.assertTrue(xp_cog.is_silent_channel(SimpleNamespace(id=7, name="support-ticket-12")))
            self.assertFalse(xp_cog.is_silent_channel(SimpleNamespace(id=8, name="build-showcase")))

    def test_staff_rule(self):
        with mock.patch.object(config, "STAFF_ROLE_IDS", set()), mock.patch.object(config, "IMMUNE_ROLE_IDS", {10}), \
                mock.patch.object(config, "FOUNDER_ID", 1):
            self.assertTrue(xp_cog.is_staff(member(2, roles=[10])))     # immune roles count when STAFF_ROLE_IDS is empty
            self.assertTrue(xp_cog.is_staff(member(3, admin=True)))
            self.assertTrue(xp_cog.is_staff(member(1)))                 # founder
            self.assertFalse(xp_cog.is_staff(member(4, roles=[99])))
        with mock.patch.object(config, "STAFF_ROLE_IDS", {20}), mock.patch.object(config, "IMMUNE_ROLE_IDS", {10}):
            self.assertTrue(xp_cog.is_staff(member(5, roles=[20])))
            self.assertFalse(xp_cog.is_staff(member(6, roles=[10])))


if __name__ == "__main__":
    unittest.main()
