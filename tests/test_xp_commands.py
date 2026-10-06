"""Tests for the slash command callbacks and a few intake guards, using fake Discord objects.

Run from the repo root:  python -m unittest discover -s tests -t .
"""
import datetime
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import discord  # noqa: E402
import config  # noqa: E402
import points_config as pc  # noqa: E402
import xp_cog  # noqa: E402
import xp_engine as xe  # noqa: E402
from tests.test_xp_cog import BUILD, CHANNELS, GUILD, NOW, REACH, CogCase, interaction, member, proof_message  # noqa: E402

UTC = datetime.timezone.utc
XP_CH, CHALLENGE_CH = 700, 800


def channel(cid, name="general"):
    return SimpleNamespace(id=cid, name=name, mention=f"<#{cid}>",
                           send=AsyncMock(return_value=SimpleNamespace(id=555)))


def cmd(command):
    """The raw callback behind a slash command or subcommand."""
    return command.callback


class CommandCase(CogCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channels = {BUILD: self.proof_channel, REACH: channel(REACH, "reach"),
                         CHALLENGE_CH: channel(CHALLENGE_CH, "challenges")}
        self.guild.get_channel = lambda cid: self.channels.get(cid)
        self.guild.name = "NEXTGEN"
        self.admin = member(1, admin=True)
        self.cog.request_board_refresh = mock.MagicMock()
        extra = [
            mock.patch.object(config, "XP_PING_ROLE_ID", 55),
            mock.patch.object(config, "REACH_CHANNEL_ID", REACH),
            mock.patch.object(config, "BUILD_CHANNEL_ID", BUILD),
            mock.patch.object(config, "CHALLENGE_CHANNEL_ID", CHALLENGE_CH),
            mock.patch.object(config, "IGNORED_CHANNEL_IDS", set()),
            mock.patch.object(config, "ANNOUNCEMENT_CHANNEL_ID", 0),
            mock.patch.object(config, "ELITE_ROLE_ID", 0),
        ]
        for p in extra:
            p.start()
            self.addCleanup(p.stop)

    def inter(self, user=None):
        i = interaction(user or self.admin, self.guild)
        i.id = id(i)
        i.channel = self.channels[BUILD]
        return i

    def give(self, user, lane, points, key):
        self.store.insert_award(guild_id=GUILD, user_id=user, lane=lane, category="adjustment", points=points,
                                reason="", submission_id=None, award_key=key, awarded_by=0, created_at=xe.ts(NOW))

    @staticmethod
    def sent(i):
        return i.response.send_message.await_args


class TestPublicCommands(CommandCase):
    async def test_xp_shows_both_lanes_progress_and_rank(self):
        self.give(7, "reach", 60, "a")
        self.give(8, "reach", 90, "b")
        self.give(7, "builder", 30, "c")
        i = self.inter(member(7))
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.xp)(self.cog, i, None)
        embed = self.sent(i).kwargs["embed"]
        self.assertTrue(self.sent(i).kwargs["ephemeral"])
        text = " ".join(f.value for f in embed.fields)
        self.assertIn("60 of 100 needed (rank 2)", text)
        self.assertIn("30 of 100 needed (rank 1)", text)
        self.assertIn("All-time XP: 60", text)

    async def test_leaderboard_is_paginated_and_skips_excluded_members(self):
        for n in range(25):
            self.give(100 + n, "builder", 200 - n, f"k{n}")
        self.store.exclude(100, "test")
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            i.channel_id = BUILD
            await cmd(xp_cog.XPCog.buildleaderboard)(self.cog, i, "cycle")
        i.response.defer.assert_awaited()
        kw = i.followup.send.await_args.kwargs
        lines = kw["embed"].description.split("\n")
        self.assertEqual(len(lines), 10)
        self.assertNotIn("user100 ", kw["embed"].description)
        self.assertIn("page 1 of 3", kw["embed"].footer.text)
        view = kw["view"]
        self.assertTrue(view.prev.disabled)
        self.assertFalse(view.next.disabled)
        nxt = interaction(self.admin, self.guild)
        await view.next.callback(nxt)
        self.assertIn("page 2 of 3", nxt.response.edit_message.await_args.kwargs["embed"].footer.text)

    async def test_leaderboard_when_empty(self):
        i = self.inter()
        await cmd(xp_cog.XPCog.reachleaderboard)(self.cog, i, "alltime")
        self.assertIn("No points", i.followup.send.await_args.args[0])

    async def test_invitedby_rules(self):
        me = member(7)
        i = self.inter(me)
        await cmd(xp_cog.XPCog.invitedby)(self.cog, i, member(7))
        self.assertIn("yourself", self.sent(i).args[0])
        robot = member(8)
        robot.bot = True
        i2 = self.inter(me)
        await cmd(xp_cog.XPCog.invitedby)(self.cog, i2, robot)
        self.assertIn("not a bot", self.sent(i2).args[0])
        old = member(9)
        old.joined_at = NOW - datetime.timedelta(days=9)
        i3 = self.inter(old)
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.invitedby)(self.cog, i3, member(60))
        self.assertIn("within 7 days", self.sent(i3).args[0])

    async def test_invitedby_records_once(self):
        newbie = member(7)
        newbie.joined_at = NOW - datetime.timedelta(days=2)
        inviter = member(60)
        self.guild.get_member = lambda uid: inviter if uid == 60 else member(uid)
        i = self.inter(newbie)
        newbie.guild = self.guild
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.invitedby)(self.cog, i, inviter)
            self.assertIn("recorded as your inviter", self.sent(i).args[0])
            i2 = self.inter(newbie)
            await cmd(xp_cog.XPCog.invitedby)(self.cog, i2, inviter)
        self.assertIn("already recorded", self.sent(i2).args[0])
        self.assertEqual(self.store.get_referral(7)["inviter_id"], 60)

    async def test_referrals_lists_stages(self):
        self.engine.register_referral(GUILD, 200, 7, NOW, NOW - datetime.timedelta(days=500), NOW)
        i = self.inter(member(7))
        await cmd(xp_cog.XPCog.referrals)(self.cog, i)
        self.assertIn("<@200>: joined", self.sent(i).kwargs["embed"].description)


class TestStaffCommands(CommandCase):
    async def test_xpost_posts_an_engagement_card_in_the_reach_channel(self):
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.xpost)(self.cog, i, "https://x.com/G_NEXTGEN/status/5", "Push this one")
        send = self.channels[REACH].send.await_args
        self.assertEqual(send.kwargs["content"], "<@&55> https://x.com/G_NEXTGEN/status/5")  # link in the text: Discord shows the post preview
        embed = send.kwargs["embed"]
        self.assertEqual(embed.description, "Push this one")
        self.assertIn("Like +2, Retweet +3, Comment +5", embed.fields[0].value)
        ids = [child.custom_id for child in send.kwargs["view"].children]
        post_id = self.store.latest_official_post()["id"]
        self.assertEqual(ids, [f"xpe:{post_id}:like", f"xpe:{post_id}:retweet", f"xpe:{post_id}:comment"])
        self.assertIn("claim Like, Retweet and Comment points", i.followup.send.await_args.args[0])

    async def test_xpost_refuses_a_non_x_link_and_a_silent_channel_without_opening_a_window(self):
        i = self.inter()
        await cmd(xp_cog.XPCog.xpost)(self.cog, i, "https://example.com/post", "")
        self.assertIn("link to an X post", i.followup.send.await_args.args[0])
        with mock.patch.object(config, "IGNORED_CHANNEL_IDS", {REACH}):
            i2 = self.inter()
            await cmd(xp_cog.XPCog.xpost)(self.cog, i2, "https://x.com/G_NEXTGEN/status/5", "")
        self.assertIn("silent channel", i2.followup.send.await_args.args[0])
        self.channels[REACH].send.assert_not_awaited()
        self.assertIsNone(self.store.latest_official_post())

    async def test_xpost_always_goes_to_the_reach_channel_wherever_it_is_run(self):
        i = self.inter()
        i.channel = self.channels[BUILD]                     # run from the Build channel
        await cmd(xp_cog.XPCog.xpost)(self.cog, i, "https://x.com/G_NEXTGEN/status/8", "")
        self.channels[REACH].send.assert_awaited_once()
        self.channels[BUILD].send.assert_not_awaited()

    async def test_xpost_refuses_rather_than_post_elsewhere_when_the_reach_channel_is_not_set(self):
        i = self.inter()
        with mock.patch.object(config, "REACH_CHANNEL_ID", 0):
            await cmd(xp_cog.XPCog.xpost)(self.cog, i, "https://x.com/G_NEXTGEN/status/9", "")
        self.assertIn("Reach channel is not set", i.followup.send.await_args.args[0])
        self.channels[BUILD].send.assert_not_awaited()
        self.assertIsNone(self.store.latest_official_post())

    async def test_officialpost_opens_a_window_quietly(self):
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.officialpost)(self.cog, i, "https://x.com/G_NEXTGEN/status/6")
        self.assertIn("is open until", self.sent(i).args[0])
        self.channels[REACH].send.assert_not_awaited()

    async def test_award_adjust_exclude_include(self):
        target = member(7)
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.award)(self.cog, i, target, "event_participate", "ran a space")
            self.assertIn("+15 builder XP", self.sent(i).args[0])
            await cmd(xp_cog.XPCog.xpadjust)(self.cog, self.inter(), target, "reach", -5, "typo")
            await cmd(xp_cog.XPCog.xpexclude)(self.cog, self.inter(), target, "farming")
            self.assertTrue(self.store.is_excluded(7))
            blocked = self.inter()
            await cmd(xp_cog.XPCog.award)(self.cog, blocked, target, "event_attend", "")
            self.assertIn("No points awarded", self.sent(blocked).args[0])
            back = self.inter()
            await cmd(xp_cog.XPCog.xpinclude)(self.cog, back, target)
            self.assertIn("included again", self.sent(back).args[0])
        rows = {r["category"]: r["points"] for r in self.store.ledger_for_user(7)}
        self.assertEqual(rows, {"event_participate": 15, "adjustment": -5})

    async def test_award_rejects_unknown_categories_and_bots(self):
        i = self.inter()
        await cmd(xp_cog.XPCog.award)(self.cog, i, member(7), "nonsense", "")
        self.assertIn("valid category", self.sent(i).args[0])
        robot = member(8)
        robot.bot = True
        i2 = self.inter()
        await cmd(xp_cog.XPCog.award)(self.cog, i2, robot, "event_attend", "")
        self.assertIn("valid category", self.sent(i2).args[0])

    async def test_award_autocomplete_filters_by_text(self):
        choices = await xp_cog.XPCog._award_category(self.cog, self.inter(), "tutorial")
        self.assertEqual({c.value for c in choices}, {"tutorial_simple", "tutorial_detailed", "tutorial_process",
                                                      "tutorial_exceptional"})

    async def test_referralclear_releases_held_payouts(self):
        self.engine.register_referral(GUILD, 200, 60, NOW, NOW - datetime.timedelta(hours=5), NOW)  # flagged
        self.store.insert_award(guild_id=GUILD, user_id=200, lane="builder", category="academy_lesson", points=5,
                                reason="", submission_id=None, award_key="x", awarded_by=0, created_at=xe.ts(NOW))
        sid = self.store.add_submission(
            guild_id=GUILD, user_id=200, channel_id=CHANNELS["academy"], message_id=1, lane="builder", lesson_day=1,
            content_hash=None, url_key=None, within_window=False, created_at=xe.ts(NOW), status="approved")
        self.store.insert_award(guild_id=GUILD, user_id=200, lane="builder", category="academy_lesson", points=5,
                                reason="", submission_id=sid, award_key=f"{sid}:academy_lesson", awarded_by=0,
                                created_at=xe.ts(NOW))
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.referralclear)(self.cog, i, member(200))
        self.assertIn("1 payout released", self.sent(i).args[0])
        none = self.inter()
        await cmd(xp_cog.XPCog.referralclear)(self.cog, none, member(999))
        self.assertIn("no referral", self.sent(none).args[0])


class TestChallengeCommands(CommandCase):
    async def test_create_posts_an_embed_with_discord_timestamps(self):
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=datetime.datetime(2026, 10, 7, 10, 0, tzinfo=UTC)):
            await cmd(xp_cog.XPCog.challenge_create)(self.cog, i, "Build a bot", "Make something and share it.")
        embed = self.channels[CHALLENGE_CH].send.await_args.kwargs["embed"]
        deadline = next(f.value for f in embed.fields if f.name == "Deadline")
        unix = int(datetime.datetime(2026, 10, 11, 23, 59, 59, tzinfo=UTC).timestamp())
        self.assertEqual(deadline, f"<t:{unix}:F> (<t:{unix}:R>)")
        challenge = self.store.get_challenge(1)
        self.assertEqual((challenge["title"], challenge["ends_at"], challenge["message_id"]),
                         ("Build a bot", "2026-10-11 23:59:59", 555))

    async def test_create_refuses_silent_or_missing_channels_and_creates_nothing(self):
        with mock.patch.object(config, "IGNORED_CHANNEL_IDS", {CHALLENGE_CH}):
            i = self.inter()
            await cmd(xp_cog.XPCog.challenge_create)(self.cog, i, "T", "D")
        self.assertIn("will not post there", i.followup.send.await_args.args[0])
        with mock.patch.object(config, "ANNOUNCEMENT_CHANNEL_ID", CHALLENGE_CH):
            i2 = self.inter()
            await cmd(xp_cog.XPCog.challenge_create)(self.cog, i2, "T", "D")
        self.assertIn("will not post there", i2.followup.send.await_args.args[0])
        with mock.patch.object(config, "CHALLENGE_CHANNEL_ID", 0):
            i3 = self.inter()
            await cmd(xp_cog.XPCog.challenge_create)(self.cog, i3, "T", "D")
        self.assertIn("CHALLENGE_CHANNEL_ID", i3.followup.send.await_args.args[0])
        self.assertEqual(self.store.all_challenges(), [])
        self.channels[CHALLENGE_CH].send.assert_not_awaited()

    async def test_status_shows_time_left_or_none(self):
        i = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.challenge_status)(self.cog, i)
        self.assertIn("No challenge", self.sent(i).args[0])
        self.engine.create_challenge("Ship it", "go", NOW)
        i2 = self.inter()
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW + datetime.timedelta(days=1)):
            await cmd(xp_cog.XPCog.challenge_status)(self.cog, i2)
        self.assertIn("Time left", self.sent(i2).kwargs["embed"].fields[0].name)


class TestCycleReviewCommand(CommandCase):
    async def test_review_posts_to_the_staff_channel_only(self):
        for u, r, b in ((1001, 150, 120), (1002, 100, 100), (1003, 200, 90)):
            self.give(u, "reach", r, f"r{u}")
            self.give(u, "builder", b, f"b{u}")
        elite = member(1002, roles=[777])
        self.guild.get_member = lambda uid: elite if uid == 1002 else member(uid)
        i = self.inter()
        with mock.patch.object(config, "ELITE_ROLE_ID", 777), mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await cmd(xp_cog.XPCog.cycle_review)(self.cog, i)
        posted = self.staff.send.await_args.kwargs["embed"]
        names = {f.name: f.value for f in posted.fields}
        self.assertIn("<@1001>", names["Meets both minimums"])
        self.assertNotIn("<@1002>", names["Meets both minimums"])      # already Elite
        self.assertNotIn("<@1003>", names["Meets both minimums"])      # only one lane
        self.assertIn("<@1003>", names["Near misses (one lane met)"])
        self.assertIn("Choosing and giving the role is a staff decision", posted.description)
        i.followup.send.assert_awaited()
        self.assertEqual(self.guild.get_channel(BUILD).send.await_count, 0)  # nothing public


class TestLegacyStartup(CommandCase):
    SUMMARY = {"imported": 12, "points": 2580, "builder": 1290, "reach": 1290, "already": 0, "excluded": 0, "elite": 1,
               "stamp": "2026-10-05 00:00:00"}

    def setUp(self):
        self.elite_members = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
        self.elite_role = SimpleNamespace(id=777, position=5, members=self.elite_members)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        guilds = mock.PropertyMock(return_value=[SimpleNamespace(get_role=lambda rid: self.elite_role if rid == 777 else None)])
        guilds_patch = mock.patch.object(discord.Client, "guilds", guilds)
        guilds_patch.start()
        self.addCleanup(guilds_patch.stop)
        self.cog.engine = mock.MagicMock()
        self.cog.engine.import_legacy_xp.return_value = dict(self.SUMMARY)
        xp_cog.db.kv_get.return_value = None
        patch = mock.patch.object(config, "ELITE_ROLE_ID", 777)
        patch.start()
        self.addCleanup(patch.stop)

    async def test_import_runs_once_with_the_elites_skipped_then_sets_the_done_flag(self):
        await self.cog._run_legacy_import()
        self.cog.engine.import_legacy_xp.assert_called_once()
        self.assertEqual(self.cog.engine.import_legacy_xp.call_args.args[1], {10, 11})
        xp_cog.db.kv_set.assert_called_once_with(self.cog.LEGACY_FLAG, "1")

    async def test_it_waits_until_an_elite_role_is_configured(self):
        with mock.patch.object(config, "ELITE_ROLE_ID", 0):
            await self.cog._run_legacy_import()
        self.cog.engine.import_legacy_xp.assert_not_called()
        xp_cog.db.kv_set.assert_not_called()

    async def test_it_waits_if_the_elite_role_is_not_in_the_server(self):
        self.elite_role = None
        await self.cog._run_legacy_import()
        self.cog.engine.import_legacy_xp.assert_not_called()
        xp_cog.db.kv_set.assert_not_called()

    async def test_nothing_happens_once_the_flag_is_set(self):
        xp_cog.db.kv_get.return_value = "1"
        await self.cog._run_legacy_import()
        self.cog.engine.import_legacy_xp.assert_not_called()
        xp_cog.db.kv_set.assert_not_called()

    async def test_it_only_runs_once_per_process(self):
        await self.cog._run_legacy_import()
        await self.cog._run_legacy_import()
        self.cog.engine.import_legacy_xp.assert_called_once()

    async def test_a_failed_import_never_stops_startup_and_is_retried_next_time(self):
        self.cog.engine.import_legacy_xp.side_effect = RuntimeError("table missing")
        await self.cog._run_legacy_import()  # must not raise
        xp_cog.db.kv_set.assert_not_called()  # flag stays unset
        self.cog.engine.import_legacy_xp.side_effect = None
        await self.cog._run_legacy_import()   # the next ready event retries
        xp_cog.db.kv_set.assert_called_once()

    async def test_the_founder_and_staff_are_hidden_from_leaderboards_but_elites_are_visible(self):
        elite_member = member(20, roles=[777])
        staff_member = member(21, roles=[888])
        both = {20: elite_member, 21: staff_member}
        self.guild.get_member = lambda uid: both.get(uid) or member(uid)
        with mock.patch.object(config, "IMMUNE_ROLE_IDS", {777, 888}):
            hide = self.cog.hide_fn(self.guild)
            self.assertFalse(hide(20))              # Elite: visible
            self.assertTrue(hide(21))               # another immune role: hidden
            self.assertTrue(hide(config.FOUNDER_ID))
            self.assertFalse(hide(7))
        with mock.patch.object(config, "IMMUNE_ROLE_IDS", {777, 888}), mock.patch.object(config, "ELITE_ROLE_ID", 0):
            self.assertTrue(self.cog.hide_fn(self.guild)(20))   # no Elite role configured: hidden as before
        self.guild.get_member = lambda uid: None if uid == 30 else member(uid)
        self.assertTrue(self.cog.hide_fn(self.guild)(30))        # left the server


class TestCorrectionStartup(CommandCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.kv = {xp_cog.XPCog.LEGACY_FLAG: "1"}
        xp_cog.db.kv_get.side_effect = lambda k, d=None: self.kv.get(k, d)
        xp_cog.db.kv_set.side_effect = lambda k, v: self.kv.__setitem__(k, str(v))
        self.cog.engine = mock.MagicMock()
        self.cog.engine.apply_correction.return_value = {"changed": 6, "removed": 2000, "unchanged": 6}
        self.flag = f"points_correction_done:{pc.CORRECTIONS[0]['key']}"

    async def test_it_runs_once_and_sets_its_done_flag(self):
        await self.cog._run_corrections()
        self.cog.engine.apply_correction.assert_called_once()
        self.assertEqual(self.cog.engine.apply_correction.call_args.args[0], pc.CORRECTIONS[0])
        self.assertEqual(self.kv[self.flag], "1")
        await self.cog._run_corrections()
        self.cog.engine.apply_correction.assert_called_once()   # not again

    async def test_it_waits_until_the_legacy_import_is_done(self):
        del self.kv[xp_cog.XPCog.LEGACY_FLAG]
        await self.cog._run_corrections()
        self.cog.engine.apply_correction.assert_not_called()
        self.assertNotIn(self.flag, self.kv)

    async def test_a_failure_never_stops_startup_and_is_retried(self):
        self.cog.engine.apply_correction.side_effect = RuntimeError("db down")
        await self.cog._run_corrections()          # must not raise
        self.assertNotIn(self.flag, self.kv)       # so the next ready event tries again
        self.cog.engine.apply_correction.side_effect = None
        await self.cog._run_corrections()
        self.assertEqual(self.kv[self.flag], "1")


class TestIntakeGuards(CommandCase):
    async def test_intake_stays_silent_in_ignored_announcement_and_ticket_channels(self):
        msg = proof_message(900, member(7), BUILD, "no proof here")
        msg.channel.name = "build"
        with mock.patch.object(config, "IGNORED_CHANNEL_IDS", {BUILD}):
            await self.cog.on_message(msg)
        msg.reply.assert_not_awaited()
        with mock.patch.object(config, "ANNOUNCEMENT_CHANNEL_ID", BUILD):
            await self.cog.on_message(msg)
        msg.reply.assert_not_awaited()
        ticket = proof_message(901, member(7), BUILD, "no proof here")
        ticket.channel.name = "support-12"
        await self.cog.on_message(ticket)
        ticket.reply.assert_not_awaited()
        normal = proof_message(902, member(7), BUILD, "no proof here")
        normal.channel.name = "build"
        await self.cog.on_message(normal)
        normal.reply.assert_awaited_once()

    async def test_reject_reason_does_not_double_the_full_stop(self):
        await self.submit(910, 7, "build", "https://example.com/rr")
        modal = xp_cog.RejectModal(self.cog, 1)
        modal.reason._value = "blurry screenshot."
        await modal.on_submit(self.inter())
        self.assertEqual(self.proof_msgs[910].reply.await_args.args[0], "Not approved: blurry screenshot.")

    async def test_a_bad_pending_row_cannot_stop_startup(self):
        await self.submit(911, 7, "build", "https://example.com/bad1")
        await self.submit(912, 8, "build", "https://example.com/bad2")
        self.bot.add_view = mock.MagicMock(side_effect=[RuntimeError("boom"), None])
        await self.cog.cog_load()  # must not raise
        self.assertEqual(self.bot.add_view.call_count, 2)
        # a card in a channel that is no longer a proof channel is skipped, not fatal
        with mock.patch.object(config, "PROOF_KEY_BY_ID", {}):
            self.bot.add_view.reset_mock()
            await self.cog.cog_load()
            self.bot.add_view.assert_not_called()


if __name__ == "__main__":
    unittest.main()
