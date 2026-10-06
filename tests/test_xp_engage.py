"""Tests for official post cards (Like / Retweet / Comment claims), in-channel review buttons for
roles above Elite, and the two live leaderboards, using fake Discord objects.

Run from the repo root:  python -m unittest discover -s tests -t .
"""
import datetime
import os
import re
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import discord  # noqa: E402

import config  # noqa: E402
import points_config as pc  # noqa: E402
import xp_cog  # noqa: E402
import xp_engine as xe  # noqa: E402
from tests.test_xp_cog import ACADEMY, BUILD, CHANNELS, GUILD, NOW, REACH, STAFF_CH, CogCase, interaction, member, proof_message  # noqa: E402

ELITE, MOD = 700, 701
POSITIONS = {ELITE: 5, MOD: 9}
BOARD_CH = 801
LINK = "https://x.com/G_NEXTGEN/status/55"


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "gone")


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no")


class FakeChannel:
    """A channel that remembers what was sent and can hand the messages back."""

    def __init__(self, cid, name, guild):
        self.id, self.name, self.mention, self.guild = cid, name, f"<#{cid}>", guild
        self.messages = {}
        self._n = 0
        self.send = AsyncMock(side_effect=self._send)
        self.fetch_message = AsyncMock(side_effect=self._fetch)

    async def _send(self, *args, **kw):
        self._n += 1
        msg = SimpleNamespace(id=self.id * 100 + self._n, embeds=[kw.get("embed")] if kw.get("embed") else [],
                              edit=AsyncMock(), delete=AsyncMock(), content=kw.get("content"))
        self.messages[msg.id] = msg
        return msg

    async def _fetch(self, mid):
        if mid not in self.messages:
            raise not_found()
        return self.messages[mid]

    def get_partial_message(self, mid):
        return self.messages[mid]


class EngageCase(CogCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.kv = {}
        xp_cog.db.kv_get.side_effect = lambda k, d=None: self.kv.get(k, d)
        xp_cog.db.kv_set.side_effect = lambda k, v: self.kv.__setitem__(k, str(v))

        self.elite_role = SimpleNamespace(id=ELITE, position=POSITIONS[ELITE])
        self.people = {}
        self.guild.get_role = lambda rid: self.elite_role if rid == ELITE else None
        self.guild.get_member = lambda uid: self.people.get(uid) or member(uid, guild=self.guild)

        self.mod = self.person(50, roles=[MOD])
        self.elite = self.person(51, roles=[ELITE])
        self.plain = self.person(7)

        self.reach_ch = FakeChannel(REACH, "reach", self.guild)
        self.build_ch = self.proof_channel  # the harness's Build channel
        self.build_ch.guild = self.guild
        self.build_ch.messages = self.proof_msgs
        self.build_ch.get_partial_message = lambda mid: self.proof_msgs[mid]
        self.board_ch = FakeChannel(BOARD_CH, "leaderboard", self.guild)
        self.staff.guild = self.guild
        chans = {STAFF_CH: self.staff, REACH: self.reach_ch, BUILD: self.build_ch,
                 BOARD_CH: self.board_ch}
        self.bot.get_channel = lambda cid: chans.get(cid)
        self.guild.get_channel = lambda cid: chans.get(cid)

        for p in (
            mock.patch.object(config, "ELITE_ROLE_ID", ELITE),
            mock.patch.object(config, "STAFF_ROLE_IDS", set()),
            mock.patch.object(config, "REVIEWER_ROLE_IDS", set()),
            mock.patch.object(config, "XP_PING_ROLE_ID", 55),
            mock.patch.object(config, "IGNORED_CHANNEL_IDS", set()),
            mock.patch.object(config, "ANNOUNCEMENT_CHANNEL_ID", 0),
            mock.patch.object(config, "IMMUNE_ROLE_IDS", {ELITE, MOD}),
            mock.patch.object(config, "LEADERBOARD_CHANNEL_ID", BOARD_CH),
            mock.patch.object(config, "LEADERBOARD_COMMAND_CHANNEL_ID", BOARD_CH),
        ):
            p.start()
            self.addCleanup(p.stop)

    def person(self, uid, roles=()):
        m = member(uid, roles=roles, positions=POSITIONS, guild=self.guild)
        self.people[uid] = m
        return m

    def inter(self, user):
        i = interaction(user, self.guild)
        i.id = id(i)
        i.message = None
        return i

    def reach_message(self, mid, author, text):
        msg = proof_message(mid, author, REACH, text)
        msg.guild = self.guild
        msg.channel = self.reach_ch
        msg.delete = AsyncMock()
        return msg

    def give(self, user, lane, points, key):
        self.store.insert_award(guild_id=GUILD, user_id=user, lane=lane, category="adjustment", points=points,
                                reason="", submission_id=None, award_key=key, awarded_by=0, created_at=xe.ts(NOW))


class TestOfficialPostCards(EngageCase):
    async def test_a_link_from_a_role_above_elite_becomes_a_card_with_buttons_and_a_preview(self):
        msg = self.reach_message(900, self.mod, f"Good morning! {LINK}?s=20 push it")
        await self.cog.on_message(msg)
        send = self.reach_ch.send.await_args
        self.assertEqual(send.kwargs["content"], f"<@&55> {LINK}?s=20")      # the link is in the text so Discord shows the preview
        self.assertIn("Good morning!", send.kwargs["embed"].description)
        self.assertNotIn("x.com", send.kwargs["embed"].description)
        labels = [c.item.label for c in send.kwargs["view"].children]
        self.assertEqual(labels, ["Like (+2)", "Retweet (+3)", "Comment (+5)"])
        msg.delete.assert_awaited_once()                                      # the original message is replaced
        self.assertIsNotNone(self.store.latest_official_post())
        self.assertIsNone(self.store.get_submission(1))                      # not treated as proof

    async def test_a_card_with_no_extra_text_has_a_default_description(self):
        await self.cog.on_message(self.reach_message(901, self.mod, LINK))
        self.assertIn("press the buttons below", self.reach_ch.send.await_args.kwargs["embed"].description)

    async def test_elites_and_members_posting_a_link_get_normal_proof_review_not_a_card(self):
        for author, mid in ((self.elite, 902), (self.plain, 903)):
            msg = self.reach_message(mid, author, f"my post about NEXTGEN https://x.com/member{mid}/status/{mid}")
            await self.cog.on_message(msg)
            msg.delete.assert_not_awaited()
            self.assertEqual(msg.reply.await_args.args[0], xe.MSG_RECEIVED)
        self.reach_ch.send.assert_not_awaited()
        self.assertEqual(self.store.get_submission(1)["status"], "pending")

    async def test_a_message_from_above_elite_with_no_x_link_is_left_alone(self):
        msg = self.reach_message(904, self.mod, "good morning team")
        await self.cog.on_message(msg)
        self.reach_ch.send.assert_not_awaited()
        msg.delete.assert_not_awaited()

    async def test_if_the_card_cannot_be_posted_the_original_link_stays(self):
        self.reach_ch.send.side_effect = forbidden()
        msg = self.reach_message(905, self.mod, LINK)
        await self.cog.on_message(msg)
        msg.delete.assert_not_awaited()

    async def test_if_the_original_cannot_be_deleted_the_card_is_still_posted(self):
        msg = self.reach_message(906, self.mod, LINK)
        msg.delete = AsyncMock(side_effect=Exception("missing Manage Messages"))
        await self.cog.on_message(msg)
        self.reach_ch.send.assert_awaited_once()

    async def test_no_card_in_a_silent_channel(self):
        with mock.patch.object(config, "IGNORED_CHANNEL_IDS", {REACH}):
            await self.cog.on_message(self.reach_message(907, self.mod, LINK))
        self.reach_ch.send.assert_not_awaited()


class ClaimBase(EngageCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.post_id, _ = self.engine.open_official_post(LINK, NOW)

    async def link_handle(self, user, text="@someone", proof=""):
        modal = xp_cog.HandleModal(self.cog, self.post_id, "like")
        modal.handle._value = text
        modal.proof._value = proof
        i = self.inter(user)
        await modal.on_submit(i)
        return i

    def pts_total(self, user):
        return sum(r["pts"] for r in self.store.lane_totals("0", "9") if r["user_id"] == user)


class TestClaimingPoints(ClaimBase):
    async def test_first_click_asks_for_the_x_username(self):
        i = self.inter(self.plain)
        await self.cog.on_engage(i, self.post_id, "like")
        i.response.send_modal.assert_awaited_once()
        self.assertIsInstance(i.response.send_modal.await_args.args[0], xp_cog.HandleModal)
        self.assertIsNone(self.store.live_claim(self.post_id, 7, "like"))

    async def test_submitting_the_username_creates_a_claim_and_sends_it_to_the_mod_channel(self):
        i = await self.link_handle(self.plain, "@someone")
        self.assertEqual(self.store.get_handle(7), "someone")
        claim = self.store.live_claim(self.post_id, 7, "like")
        self.assertEqual(claim["status"], "pending")
        self.assertEqual(self.store.get_claim(claim["id"])["handle"], "someone")
        self.assertIn("Verification in progress", i.followup.send.await_args.args[0])
        self.assertTrue(i.followup.send.await_args.kwargs["ephemeral"])
        card = self.staff.send.await_args.kwargs
        self.assertEqual(card["embed"].title, f"Engagement claim #{claim['id']}")
        fields = {f.name: f.value for f in card["embed"].fields}
        self.assertIn("https://x.com/someone", fields["X account"])
        self.assertEqual(fields["Claims"], "Like (+2 Reach XP)")
        self.assertEqual(fields["Post"], LINK)
        self.assertEqual([c.item.custom_id for c in card["view"].children],
                         [f"xpc:{claim['id']}:approve", f"xpc:{claim['id']}:decline"])
        self.assertEqual(self.pts_total(7), 0)  # nothing is awarded yet

    async def test_a_bad_username_is_refused_and_nothing_is_created(self):
        i = await self.link_handle(self.plain, "not a handle!!")
        self.assertIn("does not look like an X username", i.response.send_message.await_args.args[0])
        self.assertIsNone(self.store.get_handle(7))
        self.assertIsNone(self.store.live_claim(self.post_id, 7, "like"))
        self.staff.send.assert_not_awaited()

    async def test_the_optional_proof_link_is_shown_to_the_moderator(self):
        await self.link_handle(self.plain, "someone", proof="https://x.com/someone/status/9")
        fields = {f.name: f.value for f in self.staff.send.await_args.kwargs["embed"].fields}
        self.assertEqual(fields["Proof"], "https://x.com/someone/status/9")

    async def test_later_clicks_use_the_saved_username_with_no_form(self):
        await self.link_handle(self.plain)
        i = self.inter(self.plain)
        await self.cog.on_engage(i, self.post_id, "retweet")
        i.response.send_modal.assert_not_awaited()
        i.response.defer.assert_awaited()
        self.assertEqual(self.store.live_claim(self.post_id, 7, "retweet")["status"], "pending")
        self.assertIn("Verification in progress", i.followup.send.await_args.args[0])

    async def test_claiming_the_same_thing_twice_is_refused_politely(self):
        await self.link_handle(self.plain)
        i = self.inter(self.plain)
        await self.cog.on_engage(i, self.post_id, "like")
        self.assertIn("still being verified", i.response.send_message.await_args.args[0])
        self.assertEqual(self.staff.send.await_count, 1)

    async def test_unknown_posts_and_excluded_members_are_turned_away(self):
        i = self.inter(self.plain)
        await self.cog.on_engage(i, 999, "like")
        self.assertIn("not open for claims", i.response.send_message.await_args.args[0])
        self.store.exclude(7, "test")
        i2 = self.inter(self.plain)
        await self.cog.on_engage(i2, self.post_id, "like")
        self.assertIn("not part of points", i2.response.send_message.await_args.args[0])

    async def test_a_declined_claim_can_be_claimed_again(self):
        await self.link_handle(self.plain)
        cid = self.store.live_claim(self.post_id, 7, "like")["id"]
        self.engine.decline_claim(cid, 50, NOW)
        i = self.inter(self.plain)
        await self.cog.on_engage(i, self.post_id, "like")
        self.assertEqual(self.store.live_claim(self.post_id, 7, "like")["status"], "pending")


    async def test_if_the_mod_channel_cannot_be_reached_the_claim_is_not_left_pending(self):
        original = self.staff.send.side_effect
        self.staff.send.side_effect = Exception("Discord is down")
        i = await self.link_handle(self.plain)
        self.assertIn("could not reach the moderators", i.followup.send.await_args.args[0])
        self.assertIsNone(self.store.live_claim(self.post_id, 7, "like"))      # not stuck behind a card nobody can see
        self.staff.send.side_effect = original
        retry = self.inter(self.plain)
        await self.cog.on_engage(retry, self.post_id, "like")
        self.assertEqual(self.store.live_claim(self.post_id, 7, "like")["status"], "pending")


class TestClaimVerification(ClaimBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cog.request_board_refresh = MagicMock()
        await self.link_handle(self.plain, "@someone")
        self.cid = self.store.live_claim(self.post_id, 7, "like")["id"]

    def review_inter(self, user):
        i = self.inter(user)
        i.message = SimpleNamespace(embeds=[discord.Embed(title="Engagement claim")], edit=AsyncMock())
        return i

    async def test_a_moderator_above_elite_approves_and_the_points_are_added(self):
        i = self.review_inter(self.mod)
        await self.cog.on_claim_review(i, self.cid, "approve")
        self.assertEqual(self.pts_total(7), 2)
        self.assertEqual(self.store.get_claim(self.cid)["status"], "approved")
        edit = i.message.edit.await_args.kwargs
        self.assertIsNone(edit["view"])
        self.assertEqual(edit["embed"].color, discord.Color.green())
        self.assertIn("Approved by", edit["embed"].footer.text)
        self.assertIn("+2 reach XP", self.people[7].send.await_args.args[0])
        self.cog.request_board_refresh.assert_called()                         # leaderboard updates automatically

    async def test_the_member_who_claimed_is_told_it_was_approved(self):
        await self.cog.on_claim_review(self.review_inter(self.mod), self.cid, "approve")
        self.assertIn("Your like on the official post was approved", self.people[7].send.await_args.args[0])

    async def test_approving_twice_pays_once(self):
        await self.cog.on_claim_review(self.review_inter(self.mod), self.cid, "approve")
        again = self.review_inter(self.mod)
        await self.cog.on_claim_review(again, self.cid, "approve")
        self.assertIn("already been reviewed", again.followup.send.await_args.args[0])
        self.assertEqual(self.pts_total(7), 2)

    async def test_decline_pays_nothing_and_tells_the_member(self):
        i = self.review_inter(self.mod)
        await self.cog.on_claim_review(i, self.cid, "decline")
        self.assertEqual(self.pts_total(7), 0)
        self.assertEqual(self.store.get_claim(self.cid)["status"], "declined")
        self.assertEqual(i.message.edit.await_args.kwargs["embed"].color, discord.Color.red())
        self.assertIn("could not be verified", self.people[7].send.await_args.args[0])

    async def test_only_roles_above_elite_can_verify(self):
        for user in (self.elite, self.plain):
            i = self.review_inter(user)
            await self.cog.on_claim_review(i, self.cid, "approve")
            self.assertIn("above Elite", i.response.send_message.await_args.args[0])
        self.assertEqual(self.store.get_claim(self.cid)["status"], "pending")
        self.assertEqual(self.pts_total(7), 0)

    async def test_nobody_verifies_their_own_claim_or_one_from_someone_they_invited(self):
        own = self.mod
        self.store.add_claim(guild_id=GUILD, post_id=self.post_id, user_id=own.id, action="comment", handle="mod",
                             proof="", created_at=xe.ts(NOW))
        mine = self.store.live_claim(self.post_id, own.id, "comment")["id"]
        i = self.review_inter(own)
        await self.cog.on_claim_review(i, mine, "approve")
        self.assertIn("your own claim", i.response.send_message.await_args.args[0])
        self.engine.register_referral(GUILD, 7, self.mod.id, NOW, NOW - datetime.timedelta(days=400), NOW)
        i2 = self.review_inter(self.mod)
        await self.cog.on_claim_review(i2, self.cid, "approve")
        self.assertIn("someone you invited", i2.response.send_message.await_args.args[0])
        self.assertEqual(self.store.get_claim(self.cid)["status"], "pending")

    async def test_a_closed_dm_does_not_break_approval(self):
        self.people[7].send = AsyncMock(side_effect=Exception("DMs closed"))
        await self.cog.on_claim_review(self.review_inter(self.mod), self.cid, "approve")
        self.assertEqual(self.pts_total(7), 2)

    async def test_the_daily_cap_shows_in_the_message_to_the_member(self):
        self.store.insert_award(guild_id=GUILD, user_id=7, lane="reach", category="official_engage", points=30, reason="",
                                submission_id=None, award_key="seed", awarded_by=0, created_at=xe.ts(NOW))
        await self.cog.on_claim_review(self.review_inter(self.mod), self.cid, "approve")
        self.assertIn("no points were added", self.people[7].send.await_args.args[0])
        self.assertIn("daily cap", self.people[7].send.await_args.args[0])


class TestButtonsSurviveRestarts(EngageCase):
    async def test_dynamic_buttons_are_registered_at_startup(self):
        self.bot.add_dynamic_items = MagicMock()
        await self.cog.cog_load()
        self.bot.add_dynamic_items.assert_called_once_with(xp_cog.EngageButton, xp_cog.ClaimButton)

    async def test_buttons_are_rebuilt_from_their_custom_ids(self):
        m = xp_cog.EngageButton.__discord_ui_compiled_template__.fullmatch("xpe:42:retweet")
        btn = await xp_cog.EngageButton.from_custom_id(None, None, m)
        self.assertEqual((btn.post_id, btn.action, btn.item.custom_id), (42, "retweet", "xpe:42:retweet"))
        m = xp_cog.ClaimButton.__discord_ui_compiled_template__.fullmatch("xpc:7:decline")
        btn = await xp_cog.ClaimButton.from_custom_id(None, None, m)
        self.assertEqual((btn.claim_id, btn.verb, btn.item.label), (7, "decline", "Decline"))
        for bad in ("xpe:42:share", "xpe:x:like", "xpc:7:maybe", "xpr:1:approve"):
            self.assertIsNone(xp_cog.EngageButton.__discord_ui_compiled_template__.fullmatch(bad) and
                              xp_cog.ClaimButton.__discord_ui_compiled_template__.fullmatch(bad))

    async def test_a_button_press_reaches_the_cog(self):
        post_id, _ = self.engine.open_official_post(LINK, NOW)
        btn = xp_cog.EngageButton(post_id, "like")
        i = self.inter(self.plain)
        i.client = SimpleNamespace(get_cog=lambda name: self.cog)
        await btn.callback(i)
        i.response.send_modal.assert_awaited_once()  # first click: asks for the X username


class TestInChannelReview(EngageCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        patch = mock.patch.object(config, "REVIEW_IN_CHANNEL_KEYS", {"build", "academy", "tutorial", "prompt_result"})
        patch.start()
        self.addCleanup(patch.stop)
        self.cog.request_board_refresh = MagicMock()

    async def submit_inline(self, mid, author, text, key="build", young=False):
        msg = proof_message(mid, author, CHANNELS[key], text)
        msg.channel = self.build_ch
        msg.guild = self.guild
        if young:
            msg.author.created_at = NOW - datetime.timedelta(days=1)
        card = SimpleNamespace(id=mid + 5000, edit=AsyncMock(), delete=AsyncMock(), embeds=[])
        msg.reply = AsyncMock(return_value=card)
        self.proof_msgs[mid] = msg
        self.proof_msgs[card.id] = card
        await self.cog._intake(msg, key)
        return msg, card

    def view_of(self, msg):
        return msg.reply.await_args.kwargs["view"]

    async def test_buttons_appear_right_under_the_submission_with_no_command(self):
        msg, card = await self.submit_inline(100, self.plain, "built the bot https://example.com/demo")
        msg.reply.assert_awaited_once()
        self.assertEqual(msg.reply.await_args.args[0], xe.MSG_RECEIVED)
        view = self.view_of(msg)
        self.assertIsInstance(view, xp_cog.ReviewView)
        labels = [getattr(c, "label", None) for c in view.children]
        self.assertIn("Approve", labels)
        self.assertIn("Decline", labels)
        self.staff.send.assert_not_awaited()                                  # nothing goes to the staff channel
        sub = self.store.get_submission(1)
        self.assertEqual((sub["review_message_id"], sub["review_channel_id"]), (card.id, BUILD))

    async def test_text_only_task_completed_gets_buttons_too(self):
        msg, _ = await self.submit_inline(101, self.plain, "task completed, finished the landing page")
        self.assertIsInstance(self.view_of(msg), xp_cog.ReviewView)

    async def test_one_word_chatter_gets_nothing(self):
        msg, _ = await self.submit_inline(102, self.plain, "lol ok")
        msg.reply.assert_not_awaited()
        self.assertIsNone(self.store.get_submission(1))

    async def test_a_moderator_above_elite_approves_in_the_channel(self):
        msg, card = await self.submit_inline(103, self.plain, "demo https://example.com/d")
        view = self.view_of(msg)
        await view.on_select(interaction(self.mod, self.guild, values=["build_demo"]))
        click = self.inter(self.mod)
        self.assertTrue(await view.interaction_check(click))
        await view.on_approve(click)
        edit = card.edit.await_args.kwargs
        self.assertEqual(edit["content"], "Approved. +20 builder XP for build demo.")
        self.assertIsNone(edit["view"])
        self.assertIn("Approved by", edit["embed"].footer.text)
        self.assertEqual(msg.reply.await_count, 1)                            # the card edit is the answer, no second reply
        self.cog.request_board_refresh.assert_called()
        self.assertEqual(sum(r["pts"] for r in self.store.lane_totals("0", "9")), 20)

    async def test_decline_needs_no_reason(self):
        msg, card = await self.submit_inline(104, self.plain, "demo https://example.com/e")
        modal = xp_cog.RejectModal(self.cog, 1)
        modal.reason._value = ""
        await modal.on_submit(self.inter(self.mod))
        self.assertEqual(card.edit.await_args.kwargs["content"], "Not approved: not enough proof.")
        self.assertEqual(self.store.get_submission(1)["status"], "rejected")

    async def test_decline_shows_the_reason_when_given(self):
        msg, card = await self.submit_inline(105, self.plain, "demo https://example.com/f")
        modal = xp_cog.RejectModal(self.cog, 1)
        modal.reason._value = "link is broken."
        await modal.on_submit(self.inter(self.mod))
        self.assertEqual(card.edit.await_args.kwargs["content"], "Not approved: link is broken.")

    async def test_only_roles_above_elite_can_press_the_buttons(self):
        msg, card = await self.submit_inline(106, self.plain, "demo https://example.com/g")
        view = self.view_of(msg)
        for user in (self.elite, self.plain, self.person(60)):
            i = self.inter(user)
            self.assertFalse(await view.interaction_check(i))
            self.assertIn("role above Elite", i.response.send_message.await_args.args[0])
        self.assertTrue(await view.interaction_check(self.inter(self.mod)))
        self.assertEqual(self.store.get_submission(1)["status"], "pending")

    async def test_zero_points_closes_the_card_without_a_public_telling_off(self):
        msg, card = await self.submit_inline(107, self.plain, "spam spam https://example.com/h")
        await self.view_of(msg).on_zero(self.inter(self.mod))
        self.assertEqual(card.edit.await_args.kwargs["content"], "No points awarded for this one.")
        self.assertEqual(self.store.get_strikes(7), 1)

    async def test_flags_are_kept_out_of_the_public_channel_but_staff_are_told(self):
        msg, card = await self.submit_inline(108, self.plain, "demo https://example.com/i", young=True)
        self.assertEqual(msg.reply.await_args.args[0], xe.MSG_RECEIVED)
        note = self.staff.send.await_args.args[0]
        self.assertIn("New account", note)
        self.assertNotIn("New account", str(msg.reply.await_args))

    async def test_deleting_the_submission_removes_the_card_from_the_channel(self):
        msg, card = await self.submit_inline(109, self.plain, "demo https://example.com/j")
        await self.cog.on_raw_message_delete(SimpleNamespace(channel_id=BUILD, message_id=109))
        self.assertEqual(self.store.get_submission(1)["status"], "withdrawn")
        card.delete.assert_awaited_once()

    async def test_if_the_reply_fails_the_card_falls_back_to_the_staff_channel(self):
        msg = proof_message(110, self.plain, BUILD, "demo https://example.com/k")
        msg.channel, msg.guild = self.build_ch, self.guild
        msg.reply = AsyncMock(side_effect=[Exception("cannot reply"), SimpleNamespace(id=1)])
        self.proof_msgs[110] = msg
        await self.cog._intake(msg, "build")
        self.staff.send.assert_awaited_once()
        self.assertEqual(self.store.get_submission(1)["review_channel_id"], STAFF_CH)

    async def test_cards_under_posts_are_restored_after_a_restart(self):
        msg, card = await self.submit_inline(111, self.plain, "demo https://example.com/l")
        self.bot.add_view = MagicMock()
        await self.cog.cog_load()
        self.assertEqual(self.bot.add_view.call_args.kwargs["message_id"], card.id)

    async def test_reach_proof_still_goes_to_the_staff_channel(self):
        msg = proof_message(112, self.plain, REACH, f"my post about NEXTGEN {LINK}")
        msg.channel, msg.guild = self.reach_ch, self.guild
        await self.cog._intake(msg, "reach")
        self.staff.send.assert_awaited_once()
        self.assertEqual(self.store.get_submission(1)["review_channel_id"], STAFF_CH)

    async def test_academy_still_needs_a_day_and_proof(self):
        msg = proof_message(113, self.plain, ACADEMY, "finished it, great lesson")
        msg.channel, msg.guild = self.build_ch, self.guild
        await self.cog._intake(msg, "academy")
        self.assertEqual(msg.reply.await_args.args[0], xe.MSG_NEEDS_PROOF)


class TestLiveLeaderboards(EngageCase):
    def posted(self):
        """The embeds sent to the leaderboard channel, in order: Build first, then Reach."""
        return [c.kwargs["embed"] for c in self.board_ch.send.await_args_list]

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.person(20, roles=[ELITE])      # Elite: visible
        self.person(21, roles=[MOD])        # above Elite: hidden
        self.give(7, "builder", 30, "a")
        self.give(20, "builder", 20, "b")
        self.give(21, "builder", 500, "c")
        self.give(8, "reach", 40, "d")
        self.give(7, "reach", 5, "e")
        self.give(20, "reach", 15, "f")

    async def test_both_boards_live_in_the_one_leaderboard_channel(self):
        await self.cog.update_boards()
        self.assertEqual(self.board_ch.send.await_count, 2)
        build, reach = self.posted()
        self.assertEqual((build.title, reach.title), ("Build leaderboard", "Engagement leaderboard"))
        self.assertIn("1. **user7** -- 30 XP", build.description)
        self.assertIn("2. **user20** (Elite) -- 20 XP", build.description)
        self.assertNotIn("user8", build.description)                          # Reach-only points stay off the Build board
        self.assertIn("1. **user8** -- 40 XP", reach.description)
        self.assertIn("2. **user20** (Elite) -- 15 XP", reach.description)

    async def test_elites_are_visible_and_people_above_elite_are_hidden(self):
        await self.cog.update_boards()
        build = self.posted()[0].description
        self.assertIn("user20", build)       # Elite shows up, starting from zero and earning
        self.assertNotIn("user21", build)    # a moderator's 500 does not

    async def test_elites_are_tagged_everywhere_and_only_elites(self):
        embed = self.cog.board_embed(self.guild, pc.BUILDER)
        self.assertIn("1. **user7** -- 30 XP", embed.description)           # not an Elite: no tag
        self.assertIn("2. **user20** (Elite) -- 20 XP", embed.description)
        self.assertIn("**user20** (Elite) -- 20 XP", embed.fields[0].value)  # the all-time list too
        self.assertNotIn("user7** (Elite)", embed.description + embed.fields[0].value)

    async def test_the_command_tags_elites_too(self):
        i = self.inter(self.plain)
        i.channel_id = BOARD_CH
        with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
            await xp_cog.XPCog.buildleaderboard.callback(self.cog, i, "cycle")
        self.assertIn("**user20** (Elite) -- 20 XP", i.followup.send.await_args.kwargs["embed"].description)

    async def test_without_an_elite_role_configured_nobody_is_tagged(self):
        with mock.patch.object(config, "ELITE_ROLE_ID", 0):
            self.assertNotIn("(Elite)", self.cog._who(self.guild, 20))

    async def test_the_messages_are_edited_in_place_not_reposted(self):
        await self.cog.update_boards()
        first_id = int(self.kv[f"{pc.BOARD['kv_prefix']}:builder"])
        self.give(8, "builder", 99, "g")
        await self.cog.update_boards()
        self.assertEqual(self.board_ch.send.await_count, 2)                   # still just the two original posts
        edit = self.board_ch.messages[first_id].edit.await_args.kwargs["embed"]
        self.assertIn("1. **user8** -- 99 XP", edit.description)              # the new points are on the board

    async def test_a_deleted_board_message_is_posted_again(self):
        await self.cog.update_boards()
        first_id = int(self.kv[f"{pc.BOARD['kv_prefix']}:reach"])
        del self.board_ch.messages[first_id]
        await self.cog.update_boards()
        self.assertEqual(self.board_ch.send.await_count, 3)
        self.assertNotEqual(int(self.kv[f"{pc.BOARD['kv_prefix']}:reach"]), first_id)

    async def test_an_unset_or_silent_channel_is_skipped(self):
        with mock.patch.object(config, "LEADERBOARD_CHANNEL_ID", 0):
            await self.cog.update_boards()
        with mock.patch.object(config, "IGNORED_CHANNEL_IDS", {BOARD_CH}):
            await self.cog.update_boards()
        self.board_ch.send.assert_not_awaited()

    async def test_a_board_with_no_points_shows_no_points_yet(self):
        self.store._exec("DELETE FROM xp_ledger")
        await self.cog.update_boards()
        for embed in self.posted():
            self.assertIn("No points yet.", embed.description)

    async def test_approvals_in_a_row_share_one_refresh(self):
        self.cog.update_boards = AsyncMock()
        with mock.patch.dict(pc.BOARD, {"refresh_delay_seconds": 0}):
            self.cog.request_board_refresh()
            self.cog.request_board_refresh()
            self.cog.request_board_refresh()
            await self.cog._board_task
        self.cog.update_boards.assert_awaited_once()

    async def test_the_daily_job_refreshes_the_boards(self):
        self.cog.update_boards = AsyncMock()
        await xp_cog.XPCog._daily_board.coro(self.cog)
        self.cog.update_boards.assert_awaited_once()

    async def test_one_board_failing_never_raises_and_the_other_still_updates(self):
        self.board_ch.send.side_effect = [Exception("Discord is down"), SimpleNamespace(id=1)]
        await self.cog.update_boards()  # must not raise
        self.assertEqual(self.board_ch.send.await_count, 2)

    async def test_the_hourly_referral_sweep_refreshes_the_boards_when_it_pays(self):
        self.cog.request_board_refresh = MagicMock()
        self.cog.engine.sweep_referrals = MagicMock(return_value=[SimpleNamespace(category="referral_active", points=25, user_id=7)])
        await xp_cog.XPCog._hourly.coro(self.cog)
        self.cog.request_board_refresh.assert_called_once()
        self.cog.request_board_refresh.reset_mock()
        self.cog.engine.sweep_referrals = MagicMock(return_value=[])
        await xp_cog.XPCog._hourly.coro(self.cog)
        self.cog.request_board_refresh.assert_not_called()

    async def test_the_xphandle_command_saves_a_username(self):
        i = self.inter(self.plain)
        await xp_cog.XPCog.xphandle.callback(self.cog, i, "@Sonofpeace")
        self.assertEqual(self.store.get_handle(7), "Sonofpeace")
        bad = self.inter(self.plain)
        await xp_cog.XPCog.xphandle.callback(self.cog, bad, "no way!!")
        self.assertIn("does not look like", bad.response.send_message.await_args.args[0])
        self.assertEqual(self.store.get_handle(7), "Sonofpeace")


ELSEWHERE = 999


class TestLeaderboardCommandsOnlyWorkInTheLeaderboardChannel(EngageCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.give(7, "builder", 30, "a")
        self.give(8, "reach", 40, "b")

    def inter_in(self, user, channel_id):
        i = self.inter(user)
        i.channel_id = channel_id
        return i

    async def run_check(self, user, channel_id):
        """True if the command's channel check lets this member run it there."""
        check = xp_cog.XPCog.buildleaderboard.checks[0]  # the check really attached to the command
        i = self.inter_in(user, channel_id)
        return await check(i), i

    async def test_members_can_use_it_in_the_leaderboard_channel(self):
        ok, _ = await self.run_check(self.plain, BOARD_CH)
        self.assertTrue(ok)

    async def test_members_are_turned_away_everywhere_else_with_a_private_note(self):
        for where in (ELSEWHERE, REACH, BUILD, STAFF_CH):
            ok, i = await self.run_check(self.plain, where)
            self.assertFalse(ok, where)
            msg = i.response.send_message.await_args
            self.assertEqual(msg.args[0], f"Leaderboard commands only work in <#{BOARD_CH}>.")
            self.assertTrue(msg.kwargs["ephemeral"])

    async def test_elites_are_not_moderators_so_the_restriction_applies_to_them_too(self):
        ok, _ = await self.run_check(self.elite, ELSEWHERE)
        self.assertFalse(ok)

    async def test_moderators_above_elite_can_use_it_anywhere(self):
        ok, i = await self.run_check(self.mod, ELSEWHERE)
        self.assertTrue(ok)
        i.response.send_message.assert_not_awaited()

    async def test_both_commands_are_covered_and_show_their_own_lane(self):
        for command, expect in ((xp_cog.XPCog.buildleaderboard, "user7"), (xp_cog.XPCog.reachleaderboard, "user8")):
            self.assertEqual(len(command.checks), 1, command.name)             # the channel check is attached
            i = self.inter_in(self.plain, BOARD_CH)
            with mock.patch.object(xp_cog, "now_utc", return_value=NOW):
                await command.callback(self.cog, i, "cycle")
            self.assertIn(expect, i.followup.send.await_args.kwargs["embed"].description)

    async def test_a_separate_command_channel_can_be_set(self):
        with mock.patch.object(config, "LEADERBOARD_COMMAND_CHANNEL_ID", ELSEWHERE):
            ok_there, _ = await self.run_check(self.plain, ELSEWHERE)
            ok_board, i = await self.run_check(self.plain, BOARD_CH)
        self.assertTrue(ok_there)
        self.assertFalse(ok_board)
        self.assertIn(f"<#{ELSEWHERE}>", i.response.send_message.await_args.args[0])

    async def test_with_no_channel_set_there_is_no_restriction(self):
        with mock.patch.object(config, "LEADERBOARD_COMMAND_CHANNEL_ID", 0):
            ok, _ = await self.run_check(self.plain, ELSEWHERE)
        self.assertTrue(ok)

    async def test_the_old_combined_command_is_gone(self):
        self.assertFalse(hasattr(xp_cog.XPCog, "xpleaderboard"))


if __name__ == "__main__":
    unittest.main()
