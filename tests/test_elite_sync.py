"""Tests for the automatic Elite role: granted when cycle XP reaches both minimums, removed when a
cycle closes without them.

Run from the repo root:  python -m unittest discover -s tests -t .
"""
import datetime
import os
import sys
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import discord  # noqa: E402
import config  # noqa: E402
import xp_cog  # noqa: E402
import xp_engine as xe  # noqa: E402
from tests.test_xp_cog import GUILD, CogCase, member  # noqa: E402

UTC = datetime.timezone.utc
ELITE = 900
CYCLE0 = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
CYCLE1 = datetime.datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class TestEliteSync(CogCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = mock.patch.object(config, "ELITE_ROLE_ID", ELITE)
        p.start()
        self.addCleanup(p.stop)
        self.flags = {}
        xp_cog.db.kv_get.side_effect = lambda k: self.flags.get(k)
        xp_cog.db.kv_set.side_effect = lambda k, v: self.flags.__setitem__(k, v)
        self.role = SimpleNamespace(id=ELITE, position=5, members=[])
        self.members = {}
        self.guild.get_role = lambda rid: self.role if rid == ELITE else None
        self.guild.get_member = lambda uid: self.members.get(uid)
        self.bot_guilds = mock.PropertyMock(return_value=[self.guild])
        p = mock.patch.object(discord.Client, "guilds", self.bot_guilds)
        p.start()
        self.addCleanup(p.stop)
        self.now = CYCLE0
        p = mock.patch.object(xp_cog, "now_utc", lambda: self.now)
        p.start()
        self.addCleanup(p.stop)

    def add(self, uid, elite=False, position=1):
        m = member(uid, guild=self.guild)
        m.roles = [SimpleNamespace(id=7, position=position)]
        m.add_roles = AsyncMock(side_effect=lambda r, reason=None: self._give(m, r))
        m.remove_roles = AsyncMock(side_effect=lambda r, reason=None: self._take(m, r))
        self.members[uid] = m
        if elite:
            self._give(m, self.role)
        return m

    def _give(self, m, role):
        m.roles.append(role)
        self.role.members.append(m)

    def _take(self, m, role):
        m.roles.remove(role)
        self.role.members.remove(m)

    def earn(self, uid, reach, builder, when=CYCLE0, key=None):
        for lane, pts in (("reach", reach), ("builder", builder)):
            self.store.insert_award(guild_id=GUILD, user_id=uid, lane=lane, category="adjustment", points=pts,
                                    reason="", submission_id=None, award_key=f"{key or uid}:{lane}:{when:%d}",
                                    awarded_by=0, created_at=xe.ts(when))

    async def test_reaching_both_minimums_mid_cycle_grants_the_role_at_once(self):
        m = self.add(20)
        self.earn(20, 100, 100)
        await self.cog.elite_sync()
        self.assertIn(self.role, m.roles)
        self.staff.send.assert_awaited()

    async def test_one_lane_is_not_enough(self):
        m = self.add(21)
        self.earn(21, 100, 99)
        await self.cog.elite_sync()
        self.assertNotIn(self.role, m.roles)

    async def test_old_imported_points_never_grant_the_role(self):
        m = self.add(22)
        self.store.insert_award(guild_id=GUILD, user_id=22, lane="builder", category="legacy_import", points=400,
                                reason="", submission_id=None, award_key="legacy:1:22:builder", awarded_by=None,
                                created_at="2026-10-05 00:00:00")
        self.store.insert_award(guild_id=GUILD, user_id=22, lane="reach", category="legacy_import", points=400,
                                reason="", submission_id=None, award_key="legacy:1:22:reach", awarded_by=None,
                                created_at="2026-10-05 00:00:00")
        await self.cog.elite_sync()
        self.assertNotIn(self.role, m.roles)

    async def test_nobody_loses_the_role_during_the_first_cycle(self):
        m = self.add(23, elite=True)
        await self.cog.elite_sync()
        self.assertIn(self.role, m.roles)
        m.remove_roles.assert_not_awaited()

    async def test_when_a_cycle_closes_elites_under_the_minimums_lose_the_role(self):
        weak, strong = self.add(24, elite=True), self.add(25, elite=True)
        self.earn(24, 100, 60)
        self.earn(25, 100, 100)
        self.now = CYCLE1
        await self.cog.elite_sync()
        self.assertNotIn(self.role, weak.roles)
        self.assertIn(self.role, strong.roles)
        self.assertEqual(self.flags["elite_review_done:0"], "1")

    async def test_the_cycle_review_runs_once_per_cycle(self):
        weak = self.add(26, elite=True)
        self.now = CYCLE1
        await self.cog.elite_sync()
        self._give(weak, self.role)                       # staff gives it back by hand
        await self.cog.elite_sync()
        self.assertIn(self.role, weak.roles)

    async def test_a_removed_elite_gets_the_role_back_the_moment_they_reach_the_minimums_again(self):
        m = self.add(27, elite=True)
        self.earn(27, 100, 100, when=CYCLE1)
        self.now = CYCLE1
        await self.cog.elite_sync()
        self.assertNotIn(self.role, m.roles)              # cycle 0 review: no cycle XP then
        await self.cog.elite_sync()
        self.earn(27, 100, 100, when=CYCLE1)
        await self.cog.elite_sync()
        self.assertIn(self.role, m.roles)                 # regained as soon as they hit the minimums again

    async def test_staff_above_elite_are_never_touched(self):
        boss = self.add(28, elite=True, position=9)
        self.now = CYCLE1
        await self.cog.elite_sync()
        self.assertIn(self.role, boss.roles)

    async def test_a_missing_permission_is_retried_next_time(self):
        weak = self.add(29, elite=True)
        weak.remove_roles = AsyncMock(side_effect=discord.Forbidden(mock.MagicMock(status=403), "no"))
        self.now = CYCLE1
        await self.cog.elite_sync()
        self.assertNotIn("elite_review_done:0", self.flags)
        weak.remove_roles = AsyncMock(side_effect=lambda r, reason=None: self._take(weak, r))
        await self.cog.elite_sync()
        self.assertNotIn(self.role, weak.roles)
        self.assertEqual(self.flags["elite_review_done:0"], "1")
