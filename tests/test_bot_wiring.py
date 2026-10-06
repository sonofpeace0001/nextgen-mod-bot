"""Wiring tests: the points cog loads next to the existing cogs, and proof channels stay quiet.

Run from the repo root:  python -m unittest discover -s tests -t .
"""
import asyncio
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

import bot as bot_module  # noqa: E402
import config  # noqa: E402
from points_store import Store, make_sqlite_run  # noqa: E402

PROOF = 4242
OTHER = 7777


_REAL_INIT = Store.__init__


def _sqlite_init(self, run=None, dialect="pg"):
    """Point any Store at a throwaway in-memory SQLite database. (discord.py re-executes an
    extension's module on load, so the Store class itself is what has to be patched.)"""
    _REAL_INIT(self, make_sqlite_run(sqlite3.connect(":memory:")), "sqlite")


class TestCommandTree(unittest.IsolatedAsyncioTestCase):
    async def test_points_cog_loads_before_commands_cog_and_the_tree_has_no_clashes(self):
        b = bot_module.ModerationBot()
        b.tree.sync = AsyncMock()
        b.loop = asyncio.get_running_loop()  # discord.py sets this during login
        b._restore = AsyncMock()
        order = []
        real_load = b.load_extension

        async def tracking_load(name, **kw):
            order.append(name)
            return await real_load(name, **kw)

        b.load_extension = tracking_load
        with mock.patch.object(bot_module.db, "init_db"), mock.patch.object(Store, "__init__", _sqlite_init), \
                mock.patch.object(bot_module.db, "kv_get", return_value="1"):  # legacy import already done
            await b.setup_hook()
        self.assertEqual(order, ["xp_cog", "commands_cog"])
        names = [c.name for c in b.tree.get_commands()]
        self.assertEqual(len(names), len(set(names)), "duplicate command names in the tree")
        # existing moderation commands are all still there
        for old in ("warn", "mute", "unmute", "purge", "warnings", "clearwarnings", "modlog", "slowmode", "lookup",
                    "guide", "announce", "ignore", "unignore", "ignoredchannels", "report", "note", "reactionrole"):
            self.assertIn(old, names)
        # the new points commands are there, and the retired single-points ones are not
        for new in ("xp", "xpleaderboard", "referrals", "invitedby", "officialpost", "xpost", "award", "xpadjust",
                    "xpexclude", "xpinclude", "referralclear", "challenge", "cycle"):
            self.assertIn(new, names)
        for gone in ("xproof", "addxp", "removexp"):
            self.assertNotIn(gone, names)
        # the sync inside commands_cog ran after the points commands were added
        b.tree.sync.assert_awaited()
        self.assertIn(b.get_cog("XPCog"), b.cogs.values())
        await b.close()

    def test_invites_intent_is_on_and_presence_is_still_off(self):
        self.assertTrue(bot_module.intents.invites)
        self.assertTrue(bot_module.intents.members)
        self.assertFalse(bot_module.intents.presences)


class TestProofChannelsStayQuiet(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = bot_module.ModerationBot()
        self.bot.process_commands = AsyncMock()
        self.bot._connection.user = SimpleNamespace(id=999, mentioned_in=lambda m: True)  # every message tags the bot
        self.patches = [
            mock.patch.object(config, "PROOF_CHANNEL_IDS", {PROOF}),
            mock.patch.object(config, "IGNORED_CHANNEL_IDS", set()),
            mock.patch.object(bot_module.db, "record_message"),
            mock.patch.object(bot_module.moderation, "handle_message", AsyncMock()),
            mock.patch.object(bot_module.tutor, "maybe_handle", AsyncMock(return_value=True)),
            mock.patch.object(bot_module.prompthelper, "maybe_handle", AsyncMock(return_value=True)),
            mock.patch.object(bot_module.prompthelper, "has_active_session", MagicMock(return_value=True)),
            mock.patch.object(bot_module.chat, "handle_teach", AsyncMock()),
            mock.patch.object(bot_module.chat, "schedule_delayed_reply", AsyncMock()),
            mock.patch.object(bot_module.welcome, "answer_question", AsyncMock(return_value=True)),
            mock.patch.object(bot_module.tickets, "is_ticket_channel", MagicMock(return_value=False)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def message(self, channel_id):
        return SimpleNamespace(
            author=SimpleNamespace(id=5, bot=False), guild=SimpleNamespace(id=1), content="hello there",
            channel=SimpleNamespace(id=channel_id, name="x"), mention_everyone=False)

    async def test_nothing_but_moderation_runs_in_a_proof_channel(self):
        await self.bot.on_message(self.message(PROOF))
        bot_module.moderation.handle_message.assert_awaited_once()
        bot_module.tutor.maybe_handle.assert_not_awaited()
        bot_module.prompthelper.maybe_handle.assert_not_awaited()
        bot_module.chat.handle_teach.assert_not_awaited()
        bot_module.chat.schedule_delayed_reply.assert_not_awaited()
        bot_module.welcome.answer_question.assert_not_awaited()

    async def test_other_channels_still_reach_the_tutor_and_chat(self):
        await self.bot.on_message(self.message(OTHER))
        bot_module.prompthelper.maybe_handle.assert_awaited()  # session active: straight to the prompt helper
        self.assertTrue(bot_module.moderation.handle_message.await_count >= 1)


class TestDefaults(unittest.TestCase):
    def test_daily_prompt_default_is_18_00_utc_which_is_19_00_lagos(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PROMPT_TZ", "PROMPT_HOUR")}
        with mock.patch.dict(os.environ, env, clear=True):
            import importlib
            cfg = importlib.reload(config)
            self.assertEqual((cfg.PROMPT_TZ, cfg.PROMPT_HOUR), ("UTC", 18))
            utc = datetime.datetime(2026, 10, 6, 18, 0, tzinfo=datetime.timezone.utc)
            lagos = utc.astimezone(datetime.timezone(datetime.timedelta(hours=1)))
            self.assertEqual(lagos.hour, 19)
        importlib.reload(config)

    def test_new_env_defaults(self):
        env = {k: v for k, v in os.environ.items() if k not in (
            "BUILD_CHANNEL_ID", "TUTORIAL_CHANNEL_ID", "PROMPT_RESULT_CHANNEL_ID", "REACH_CHANNEL_ID",
            "ACADEMY_CHANNEL_ID", "CYCLE_LENGTH_DAYS", "ELITE_MIN_REACH", "ELITE_MIN_BUILDER", "ELITE_SLOTS_PER_CYCLE",
            "OFFICIAL_POST_WINDOW_MINUTES", "MIN_ACCOUNT_AGE_DAYS", "POINTS_TZ", "STAFF_REVIEW_CHANNEL_ID")}
        with mock.patch.dict(os.environ, env, clear=True):
            import importlib
            cfg = importlib.reload(config)
            self.assertEqual(cfg.BUILD_CHANNEL_ID, 1529120140711432344)
            self.assertEqual(cfg.TUTORIAL_CHANNEL_ID, 1520157303054139492)
            self.assertEqual(cfg.PROMPT_RESULT_CHANNEL_ID, 1492637326541590790)
            self.assertEqual((cfg.REACH_CHANNEL_ID, cfg.ACADEMY_CHANNEL_ID), (0, 0))
            self.assertEqual((cfg.CYCLE_LENGTH_DAYS, cfg.ELITE_MIN_REACH, cfg.ELITE_MIN_BUILDER, cfg.ELITE_SLOTS_PER_CYCLE),
                             (14, 100, 100, 5))
            self.assertEqual((cfg.OFFICIAL_POST_WINDOW_MINUTES, cfg.MIN_ACCOUNT_AGE_DAYS, cfg.POINTS_TZ), (120, 7, "UTC"))
            self.assertEqual(set(cfg.PROOF_CHANNELS), {"build", "tutorial", "prompt_result"})
        importlib.reload(config)

    def test_staff_review_channel_falls_back_to_the_staff_channel(self):
        env = {k: v for k, v in os.environ.items() if k != "STAFF_REVIEW_CHANNEL_ID"}
        env["STAFF_CHANNEL_ID"] = "321"
        with mock.patch.dict(os.environ, env, clear=True):
            import importlib
            self.assertEqual(importlib.reload(config).STAFF_REVIEW_CHANNEL_ID, 321)
        importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
