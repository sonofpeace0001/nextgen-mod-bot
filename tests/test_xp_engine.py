"""Unit tests for the points engine, against an in-memory SQLite database.

Run from the repo root:  python -m unittest discover -s tests -t .
"""
import datetime
import os
import re
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import points_config as pc  # noqa: E402
import xp_engine as xe  # noqa: E402
from points_store import Store, make_sqlite_run  # noqa: E402

UTC = datetime.timezone.utc
CHANNELS = {"reach": 1, "academy": 2, "build": 3, "tutorial": 4, "prompt_result": 5}
REVIEWER = 9999


def read_source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def dt(y, m, d, h=12, mi=0, s=0):
    return datetime.datetime(y, m, d, h, mi, s, tzinfo=UTC)


# Monday 5 October 2026 is the cycle start used throughout.
MON = dt(2026, 10, 5)


def make_engine(**kw):
    conn = sqlite3.connect(":memory:")
    store = Store(make_sqlite_run(conn), dialect="sqlite")
    store.init_schema()
    rules = xe.Rules(cycle_start=datetime.date(2026, 10, 5), cycle_length=14,
                     channel_ids=dict(CHANNELS), **kw)
    return xe.Engine(store, rules)


class Base(unittest.TestCase):
    def setUp(self):
        self.e = make_engine()
        self.s = self.e.store
        self._mid = 0

    # a pending submission posted at `when`
    def post(self, user, channel, when, url=None, day=None, flags="", file_hash=None):
        self._mid += 1
        return self.s.add_submission(
            guild_id=1, user_id=user, channel_id=CHANNELS[channel], message_id=self._mid,
            lane=pc.CHANNEL_LANES[channel], lesson_day=day, content_hash=file_hash, url_key=url,
            within_window=False, created_at=xe.ts(when), flags=flags)

    def approve(self, sid, cats, now=None):
        return self.e.approve(sid, cats if isinstance(cats, list) else [cats], REVIEWER, now or dt(2026, 12, 1))

    def pts(self, user, lane=None, start="0000", end="9999"):
        rows = [r for r in self.s.lane_totals(start, end, lane) if r["user_id"] == user]
        return sum(r["pts"] for r in rows)

    def award_of(self, res, category):
        return next(a for a in res.awards if a.category == category)


class TestTimeBoundaries(Base):
    def test_week_runs_monday_to_sunday_utc(self):
        sun = dt(2026, 10, 11, 23, 59, 59)
        mon = dt(2026, 10, 12, 0, 0, 0)
        self.assertEqual(xe.week_key(sun), "2026-10-05")
        self.assertEqual(xe.week_key(mon), "2026-10-12")
        self.assertEqual(xe.week_bounds(sun), ("2026-10-05 00:00:00", "2026-10-12 00:00:00"))

    def test_cycle_rolls_over_at_midnight_utc_on_its_start_date(self):
        self.assertEqual(self.e.cycle_of(dt(2026, 10, 5, 0, 0, 0)), 0)
        self.assertEqual(self.e.cycle_of(dt(2026, 10, 18, 23, 59, 59)), 0)
        self.assertEqual(self.e.cycle_of(dt(2026, 10, 19, 0, 0, 0)), 1)
        self.assertEqual(self.e.cycle_of(dt(2026, 10, 4, 23, 59, 59)), -1)
        self.assertEqual(self.e.cycle_range(1), ("2026-10-19 00:00:00", "2026-11-02 00:00:00"))

    def test_naive_datetimes_are_read_as_utc(self):
        self.assertEqual(xe.ts(datetime.datetime(2026, 10, 5, 7, 0, 0)), "2026-10-05 07:00:00")
        lagos_like = datetime.datetime(2026, 10, 5, 23, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
        self.assertEqual(xe.ts(lagos_like), "2026-10-05 22:30:00")

    def test_daily_cap_resets_at_midnight_utc(self):
        late = dt(2026, 10, 6, 23, 59, 59)
        for n in range(3):
            res = self.approve(self.post(1, "reach", late, url=f"x.com/a/status/{n}"), "official_engage")
            self.assertEqual(res.awards[0].points, 10)
        capped = self.approve(self.post(1, "reach", late, url="x.com/a/status/9"), "official_engage")
        self.assertEqual(capped.awards[0].points, 0)
        nxt = self.approve(self.post(1, "reach", dt(2026, 10, 7, 0, 0, 0), url="x.com/a/status/10"), "official_engage")
        self.assertEqual(nxt.awards[0].points, 10)

    def test_sunday_proof_counts_for_the_closing_week_even_if_approved_on_monday(self):
        # Mon-Thu plus Sunday 23:59:59 is five days in the week of 5 Oct.
        days = [dt(2026, 10, 5), dt(2026, 10, 6), dt(2026, 10, 7), dt(2026, 10, 8), dt(2026, 10, 11, 23, 59, 59)]
        bonus = []
        for n, when in enumerate(days):
            res = self.approve(self.post(1, "reach", when, url=f"x.com/a/status/{n}"), "post_value",
                               now=dt(2026, 10, 12, 9))  # reviewer is a day late
            bonus += res.bonuses
        self.assertEqual([b.category for b in bonus], ["streak_posts"])

    def test_monday_midnight_proof_belongs_to_the_next_week(self):
        days = [dt(2026, 10, 5), dt(2026, 10, 6), dt(2026, 10, 7), dt(2026, 10, 8), dt(2026, 10, 12, 0, 0, 0)]
        bonus = []
        for n, when in enumerate(days):
            bonus += self.approve(self.post(1, "reach", when, url=f"x.com/a/status/{n}"), "post_value").bonuses
        self.assertEqual(bonus, [])

    def test_ledger_is_stamped_with_post_time_so_cycles_use_event_time(self):
        sid = self.post(1, "build", dt(2026, 10, 18, 23, 59, 59))  # last second of cycle 0
        self.approve(sid, "build_share", now=dt(2026, 10, 19, 8))   # approved in cycle 1
        c0 = self.e.cycle_range(0)
        c1 = self.e.cycle_range(1)
        self.assertEqual(self.pts(1, "builder", *c0), 10)
        self.assertEqual(self.pts(1, "builder", *c1), 0)

    def test_no_timezone_other_than_utc_in_points_code(self):
        banned = re.compile(r"Africa/|Lagos|ZoneInfo|zoneinfo|pytz|PROMPT_TZ|US/|Europe/|Asia/")
        for name in ("xp_engine.py", "xp_cog.py", "points_store.py", "points_config.py"):
            text = read_source(name)
            self.assertIsNone(banned.search(text), f"{name} mentions a non-UTC timezone")


class TestCapsAndUrls(Base):
    def test_official_engage_cap_is_30_points_per_day(self):
        got = [self.approve(self.post(1, "reach", MON, url=f"x.com/a/status/{n}"), "official_engage").awards[0].points
               for n in range(4)]
        self.assertEqual(got, [10, 10, 10, 0])

    def test_cap_trims_a_partial_award(self):
        self.s.insert_award(guild_id=1, user_id=1, lane="reach", category="official_engage", points=25,
                            reason="", submission_id=None, award_key="seed", awarded_by=0, created_at=xe.ts(MON))
        a = self.approve(self.post(1, "reach", MON, url="x.com/a/status/1"), "official_engage").awards[0]
        self.assertEqual(a.points, 5)
        self.assertIn("trimmed to the daily cap", a.notes)

    def test_three_counted_own_posts_per_day_across_all_post_categories(self):
        cats = ["post_value", "post_original", "post_thread", "post_value"]
        got = [self.approve(self.post(1, "reach", MON, url=f"x.com/a/status/{n}"), c).awards[0].points
               for n, c in enumerate(cats)]
        self.assertEqual(got, [15, 25, 40, 0])

    def test_caps_are_per_member(self):
        for n in range(3):
            self.approve(self.post(1, "reach", MON, url=f"x.com/a/status/{n}"), "official_engage")
        other = self.approve(self.post(2, "reach", MON, url="x.com/b/status/1"), "official_engage")
        self.assertEqual(other.awards[0].points, 10)

    def test_likes_milestone_pays_once_per_post_url(self):
        url = "x.com/a/status/77"
        first = self.approve(self.post(1, "reach", MON, url=url), "reach_likes_25").awards[0]
        again = self.approve(self.post(1, "reach", MON + datetime.timedelta(days=1), url=url), "reach_likes_25").awards[0]
        bigger = self.approve(self.post(1, "reach", MON + datetime.timedelta(days=2), url=url), "reach_likes_100").awards[0]
        self.assertEqual((first.points, again.points, bigger.points), (15, 0, 40))
        self.assertIn("already paid for this link", again.notes)

    def test_one_post_award_per_url(self):
        url = "x.com/a/status/88"
        self.assertEqual(self.approve(self.post(1, "reach", MON, url=url), "post_original").awards[0].points, 25)
        second = self.approve(self.post(1, "reach", MON + datetime.timedelta(days=1), url=url), "post_thread").awards[0]
        self.assertEqual(second.points, 0)

    def test_double_approve_pays_once(self):
        sid = self.post(1, "build", MON)
        first = self.approve(sid, "build_demo")
        second = self.approve(sid, "build_demo")
        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.error, "not_pending")
        self.assertEqual(self.pts(1), 20)

    def test_award_key_blocks_a_second_insert(self):
        kw = dict(guild_id=1, user_id=1, lane="builder", category="build_share", points=10, reason="",
                  submission_id=1, award_key="1:build_share", awarded_by=0, created_at=xe.ts(MON))
        self.assertIsNotNone(self.s.insert_award(**kw))
        self.assertIsNone(self.s.insert_award(**kw))

    def test_excluded_members_earn_nothing(self):
        self.s.exclude(1, "test")
        res = self.approve(self.post(1, "build", MON), "build_share")
        self.assertEqual(res.awards[0].points, 0)
        self.assertEqual(self.pts(1), 0)

    def test_category_must_be_valid_for_the_channel(self):
        res = self.approve(self.post(1, "build", MON), "post_thread")
        self.assertEqual((res.ok, res.error), (False, "bad_category"))
        # the failed attempt must not consume the submission
        self.assertTrue(self.approve(self.s.get_submission(1)["id"], "build_share").ok)

    def test_only_academy_lessons_take_several_categories(self):
        sid = self.post(1, "build", MON)
        self.assertEqual(self.approve(sid, ["build_share", "build_demo"]).error, "single_only")
        lesson = self.post(1, "academy", MON, day=1)
        res = self.approve(lesson, ["academy_lesson", "academy_test", "academy_proof"])
        self.assertEqual([a.points for a in res.awards], [5, 10, 5])

    def test_menus_show_only_categories_valid_for_the_channel(self):
        reach, multi = xe.menu_categories("reach")
        self.assertFalse(multi)
        self.assertTrue(set(reach) <= {k for k, v in pc.CATEGORIES.items() if "reach" in v["channels"]})
        self.assertNotIn("referral_join", reach)
        self.assertNotIn("event_attend", xe.menu_categories("build")[0])
        self.assertEqual(xe.menu_categories("academy"), (pc.LESSON_CATEGORIES, True))
        self.assertEqual(xe.menu_categories("academy", "help"), (pc.HELP_CATEGORIES, False))
        self.assertEqual(xe.menu_categories("tutorial")[0],
                         ["tutorial_simple", "tutorial_detailed", "tutorial_process", "tutorial_exceptional"])


class TestStreaks(Base):
    def test_post_streak_pays_30_reach_once_per_week(self):
        got = []
        for n in range(7):
            when = MON + datetime.timedelta(days=n)  # Mon..Sun, 7 distinct days
            got += self.approve(self.post(1, "reach", when, url=f"x.com/a/status/{n}"), "post_value").bonuses
        self.assertEqual([(b.category, b.points, b.lane) for b in got], [("streak_posts", 30, "reach")])
        self.assertEqual(self.s.get_award("streak:reach:2026-10-05:1")["points"], 30)

    def test_four_days_is_not_enough(self):
        bonuses = []
        for n in range(4):
            bonuses += self.approve(self.post(1, "reach", MON + datetime.timedelta(days=n), url=f"x.com/a/status/{n}"),
                                    "post_value").bonuses
        self.assertEqual(bonuses, [])

    def test_two_posts_on_one_day_count_as_one_day(self):
        bonuses = []
        for n in range(3):
            bonuses += self.approve(self.post(1, "reach", MON, url=f"x.com/a/status/{n}"), "post_value").bonuses
        for n in range(1, 4):
            when = MON + datetime.timedelta(days=n)
            bonuses += self.approve(self.post(1, "reach", when, url=f"x.com/b/status/{n}"), "post_value").bonuses
        self.assertEqual(bonuses, [])  # 4 distinct days

    def test_streak_resets_each_week(self):
        bonuses = []
        for week in range(2):
            for n in range(5):
                when = MON + datetime.timedelta(days=7 * week + n)
                bonuses += self.approve(self.post(1, "reach", when, url=f"x.com/a/status/{week}{n}"), "post_value").bonuses
        self.assertEqual(len(bonuses), 2)

    def test_academy_streak_pays_20_builder_on_five_days(self):
        got = []
        for n in range(5):
            res = self.approve(self.post(1, "academy", MON + datetime.timedelta(days=n), day=n + 1), "academy_lesson")
            got += res.bonuses
        self.assertEqual([(b.category, b.points, b.lane) for b in got], [("streak_academy", 20, "builder")])


class TestReferrals(Base):
    INVITER, INVITEE = 100, 200

    def refer(self, invitee=None, inviter=None, joined=MON, created=dt(2025, 1, 1)):
        return self.e.register_referral(1, invitee or self.INVITEE, inviter or self.INVITER, joined, created, joined)

    def test_stage_one_pays_5_reach(self):
        r = self.refer()
        self.assertEqual((r["status"], r["counted"], r["flagged"]), ("attributed", True, False))
        self.assertEqual(self.pts(self.INVITER, "reach"), 5)

    def test_all_four_stages_pay_once_each(self):
        self.refer()
        # stage 2: first approved lesson within 7 days of joining
        self.approve(self.post(self.INVITEE, "academy", MON + datetime.timedelta(days=2), day=1), "academy_lesson",
                     now=MON + datetime.timedelta(days=2))
        self.assertEqual(self.pts(self.INVITER, "reach"), 5 + 20)
        # more lessons must not pay stage 2 again
        self.approve(self.post(self.INVITEE, "academy", MON + datetime.timedelta(days=3), day=2), "academy_lesson",
                     now=MON + datetime.timedelta(days=3))
        self.assertEqual(self.pts(self.INVITER, "reach"), 25)
        # stage 3: 14 days old with approved submissions on 3 distinct days (two already above)
        self.approve(self.post(self.INVITEE, "build", MON + datetime.timedelta(days=5)), "build_share")
        later = MON + datetime.timedelta(days=15)
        self.assertEqual([a.category for a in self.e.sweep_referrals(later)], ["referral_active"])
        self.assertEqual(self.e.sweep_referrals(later + datetime.timedelta(hours=1)), [])
        self.assertEqual(self.pts(self.INVITER, "reach"), 5 + 20 + 25)
        # stage 4: invitee gains Elite
        self.assertEqual([a.category for a in self.e.evaluate_referral(self.INVITEE, later, has_elite=True)],
                         ["referral_elite"])
        self.assertEqual(self.e.evaluate_referral(self.INVITEE, later, has_elite=True), [])
        self.assertEqual(self.pts(self.INVITER, "reach"), 5 + 20 + 25 + 50)

    def test_late_first_lesson_pays_no_stage_two(self):
        self.refer()
        self.approve(self.post(self.INVITEE, "academy", MON + datetime.timedelta(days=8), day=1), "academy_lesson")
        self.assertEqual(self.pts(self.INVITER, "reach"), 5)

    def test_first_attribution_wins_and_self_referral_is_blocked(self):
        self.refer()
        self.assertEqual(self.refer(inviter=300)["status"], "exists")
        self.assertEqual(self.s.get_referral(self.INVITEE)["inviter_id"], self.INVITER)
        self.assertEqual(self.e.register_referral(1, 5, 5, MON, dt(2025, 1, 1), MON)["status"], "self")
        self.assertIsNone(self.s.get_referral(5))

    def test_sixth_referral_in_a_cycle_pays_nothing(self):
        for n in range(6):
            r = self.refer(invitee=1000 + n, joined=MON + datetime.timedelta(days=n))
            self.assertEqual(r["counted"], n < 5)
        self.assertEqual(self.pts(self.INVITER, "reach"), 5 * 5)
        self.assertEqual(self.s.get_referral(1005)["counted"], 0)
        # the sixth never pays later stages either
        self.approve(self.post(1005, "academy", MON + datetime.timedelta(days=6), day=1), "academy_lesson")
        self.assertEqual(self.pts(self.INVITER, "reach"), 25)

    def test_cap_resets_in_the_next_cycle(self):
        for n in range(5):
            self.refer(invitee=1000 + n, joined=MON + datetime.timedelta(days=n))
        later = self.refer(invitee=2000, joined=dt(2026, 10, 19, 0, 0, 0))  # first second of cycle 1
        self.assertTrue(later["counted"])

    def test_young_account_is_flagged_and_stages_2_to_4_wait_for_staff(self):
        r = self.refer(created=MON - datetime.timedelta(days=1))
        self.assertTrue(r["flagged"])
        self.assertEqual(self.pts(self.INVITER, "reach"), 5)  # stage 1 still pays
        self.approve(self.post(self.INVITEE, "academy", MON + datetime.timedelta(days=1), day=1), "academy_lesson")
        self.assertEqual(self.pts(self.INVITER, "reach"), 5)
        released = self.e.clear_flag(self.INVITEE, MON + datetime.timedelta(days=2))
        self.assertEqual([a.category for a in released], ["referral_lesson"])
        self.assertEqual(self.pts(self.INVITER, "reach"), 25)
        self.assertEqual(self.e.clear_flag(self.INVITEE, MON + datetime.timedelta(days=3)), [])

    def test_unmet_referral_expires_at_30_days_and_then_pays_nothing(self):
        self.refer()
        self.e.sweep_referrals(MON + datetime.timedelta(days=31))
        self.assertEqual(self.s.get_referral(self.INVITEE)["status"], "expired")
        self.assertEqual(self.e.evaluate_referral(self.INVITEE, MON + datetime.timedelta(days=32), has_elite=True), [])

    def test_invitedby_window_is_seven_days(self):
        self.assertTrue(self.e.invitedby_allowed(MON, MON + datetime.timedelta(days=7)))
        self.assertFalse(self.e.invitedby_allowed(MON, MON + datetime.timedelta(days=7, seconds=1)))


class TestLearnTogether(Base):
    def setUp(self):
        super().setUp()
        self.e.register_referral(1, 200, 100, MON, dt(2025, 1, 1), MON)  # 100 invited 200

    def lesson(self, user, when, day):
        return self.approve(self.post(user, "academy", when, day=day), "academy_lesson")

    def test_same_lesson_day_in_the_same_week_pays_both_10_builder(self):
        self.lesson(100, MON, 3)
        self.assertEqual(self.pts(200, "builder"), 0)
        res = self.lesson(200, MON + datetime.timedelta(days=2), 3)
        learn = [b for b in res.bonuses if b.category == "learn_together"]
        self.assertEqual(sorted(b.user_id for b in learn), [100, 200])
        self.assertEqual(self.pts(100, "builder"), 5 + 10)
        self.assertEqual(self.pts(200, "builder"), 5 + 10)

    def test_works_in_either_direction_and_only_once_per_pair_per_week(self):
        self.lesson(200, MON, 4)
        self.lesson(100, MON + datetime.timedelta(days=1), 4)
        self.lesson(200, MON + datetime.timedelta(days=2), 5)
        res = self.lesson(100, MON + datetime.timedelta(days=3), 5)
        self.assertEqual([b for b in res.bonuses if b.category == "learn_together"], [])
        self.assertEqual(self.pts(100, "builder"), 5 + 5 + 10)

    def test_different_weeks_or_different_days_do_not_pair(self):
        self.lesson(100, MON, 6)
        res = self.lesson(200, MON + datetime.timedelta(days=7), 6)       # next week
        self.assertEqual([b for b in res.bonuses if b.category == "learn_together"], [])
        res = self.lesson(200, MON + datetime.timedelta(days=1), 7)       # other lesson day
        self.assertEqual([b for b in res.bonuses if b.category == "learn_together"], [])

    def test_strangers_do_not_pair(self):
        self.lesson(100, MON, 8)
        res = self.lesson(300, MON, 8)
        self.assertEqual([b for b in res.bonuses if b.category == "learn_together"], [])


class TestChallenge(Base):
    def new_challenge(self, when, title="Build a bot"):
        return self.e.create_challenge(title, "do it", when)

    def both(self, user, cid_when, build_when=None, share_when=None, tag=""):
        b = self.approve(self.post(user, "build", build_when or cid_when), "weekly_challenge_build")
        s = self.approve(self.post(user, "reach", share_when or cid_when, url=f"x.com/u{user}/status/{tag}{cid_when.timestamp()}"),
                         "weekly_challenge_share")
        return b, s

    def test_challenge_ends_on_the_coming_sunday_at_23_59_59_utc(self):
        cid, starts, ends = self.new_challenge(dt(2026, 10, 7, 10))  # Wednesday
        self.assertEqual(ends, "2026-10-11 23:59:59")
        _, _, ends2 = self.new_challenge(dt(2026, 10, 11, 10))       # a Sunday: the next one
        self.assertEqual(ends2, "2026-10-18 23:59:59")

    def test_entry_at_sunday_235959_is_on_time_and_monday_midnight_is_late(self):
        self.new_challenge(dt(2026, 10, 5, 9))
        on_time = self.approve(self.post(1, "build", dt(2026, 10, 11, 23, 59, 59)), "weekly_challenge_build",
                               now=dt(2026, 10, 13))
        late = self.approve(self.post(2, "build", dt(2026, 10, 12, 0, 0, 0)), "weekly_challenge_build",
                            now=dt(2026, 10, 13))
        self.assertEqual(on_time.awards[0].points, 25)
        self.assertEqual(late.awards[0].points, 0)
        self.assertIn("posted after the challenge closed", late.awards[0].notes)

    def test_monday_midnight_counts_for_the_next_challenge_when_one_has_started(self):
        self.new_challenge(dt(2026, 10, 5, 9))
        self.new_challenge(dt(2026, 10, 12, 0, 0, 0), title="Next one")
        res = self.approve(self.post(1, "build", dt(2026, 10, 12, 0, 0, 0)), "weekly_challenge_build")
        self.assertEqual(res.awards[0].points, 25)

    def test_lateness_uses_posted_time_not_approval_time(self):
        self.new_challenge(dt(2026, 10, 5, 9))
        res = self.approve(self.post(1, "build", dt(2026, 10, 9, 12)), "weekly_challenge_build", now=dt(2026, 10, 20))
        self.assertEqual(res.awards[0].points, 25)

    def test_no_challenge_means_no_points(self):
        res = self.approve(self.post(1, "build", MON), "weekly_challenge_build")
        self.assertEqual(res.awards[0].points, 0)

    def test_both_parts_on_time_add_10_builder_and_10_reach_once(self):
        self.new_challenge(dt(2026, 10, 5, 9))
        _, s = self.both(1, dt(2026, 10, 6, 12))
        self.assertEqual(sorted((b.lane, b.points) for b in s.bonuses), [("builder", 10), ("reach", 10)])
        self.assertEqual(self.pts(1, "builder"), 25 + 10)
        self.assertEqual(self.pts(1, "reach"), 25 + 10)
        # extra entries for the same challenge do not repeat the bonus
        again = self.approve(self.post(1, "build", dt(2026, 10, 7, 12)), "weekly_challenge_build")
        self.assertEqual(again.bonuses, [])

    def test_one_part_alone_earns_no_bonus(self):
        self.new_challenge(dt(2026, 10, 5, 9))
        res = self.approve(self.post(1, "build", dt(2026, 10, 6, 12)), "weekly_challenge_build")
        self.assertEqual(res.bonuses, [])

    def test_four_in_a_row_pays_40_builder_and_a_miss_resets_the_streak(self):
        weeks = [MON + datetime.timedelta(days=7 * n) for n in range(6)]
        for n, start in enumerate(weeks):
            self.new_challenge(start)
        results = []
        for n in range(4):
            _, s = self.both(1, weeks[n] + datetime.timedelta(days=1), tag=f"w{n}")
            results.append([b for b in s.bonuses if b.category == "challenge_streak"])
        self.assertEqual([len(r) for r in results], [0, 0, 0, 1])
        self.assertEqual(results[3][0].points, 40)
        self.assertEqual(results[3][0].lane, "builder")
        # skip challenge 5 entirely, finish 6: the streak starts again at one
        _, s = self.both(1, weeks[5] + datetime.timedelta(days=1), tag="w5")
        self.assertEqual([b for b in s.bonuses if b.category == "challenge_streak"], [])


class TestIntake(unittest.TestCase):
    def test_multi_day_patterns_are_rejected_in_academy(self):
        for text in ("Day 1-2 done https://x.com/a", "day 1 to 5 https://x.com/a", "Days 1, 2 https://x.com/a",
                     "day 1 and 2 https://x.com/a", "Day 3 & 4 https://x.com/a", "day1-day2 https://x.com/a",
                     "Day 1–2 https://x.com/a"):
            r = xe.check_intake("academy", text, 0)
            self.assertFalse(r.ok, text)
            self.assertEqual(r.code, "multi_day", text)
            self.assertTrue(r.store_rejected)
            self.assertEqual(r.reply, xe.MSG_MULTI_DAY)

    def test_multi_day_rule_is_academy_only(self):
        r = xe.check_intake("build", "day 1-2 of my build https://example.com/demo", 0)
        self.assertTrue(r.ok)

    def test_single_day_is_accepted_and_parsed(self):
        r = xe.check_intake("academy", "Day 12 complete https://example.com/proof", 0)
        self.assertTrue(r.ok)
        self.assertEqual((r.lesson_day, r.subtype), (12, "lesson"))

    def test_builder_channels_take_text_only_posts_and_ignore_one_word_chatter(self):
        for key in ("build", "tutorial", "prompt_result"):
            r = xe.check_intake(key, "task completed, shipped the landing page", 0)
            self.assertTrue(r.ok, key)
            self.assertTrue(r.text_only, key)
            self.assertIsNone(r.url_key)
            quick = xe.check_intake(key, "ok thx", 0)
            self.assertEqual((quick.ok, quick.code, quick.reply), (False, "ignore", ""), key)
        self.assertFalse(xe.check_intake("build", "look at my thing", 1).text_only)  # has a screenshot
        self.assertFalse(xe.check_intake("build", "see https://example.com/x", 0).text_only)

    def test_academy_and_reach_still_need_a_link_or_attachment(self):
        self.assertEqual(xe.check_intake("academy", "Day 3 done and dusted", 0).reply, xe.MSG_NEEDS_PROOF)
        self.assertTrue(xe.check_intake("academy", "Day 3 done", 1).ok)
        self.assertEqual(xe.check_intake("reach", "my post about NEXTGEN", 1).reply, xe.MSG_NEEDS_PROOF)

    def test_reach_needs_an_x_status_link(self):
        self.assertFalse(xe.check_intake("reach", "https://example.com/post", 0).ok)
        self.assertFalse(xe.check_intake("reach", "my post", 1).ok)
        self.assertTrue(xe.check_intake("reach", "https://x.com/me/status/123?s=20", 0).ok)
        self.assertTrue(xe.check_intake("reach", "https://twitter.com/me/status/123", 0).ok)
        self.assertFalse(xe.check_intake("reach", "https://x.com/me", 0).ok)

    def test_academy_lesson_needs_a_day_but_helping_posts_do_not(self):
        r = xe.check_intake("academy", "finished it https://example.com/proof", 0)
        self.assertEqual((r.code, r.reply), ("no_day", xe.MSG_NEEDS_DAY))
        h = xe.check_intake("academy", "helped here https://discord.com/channels/1/2/3", 0)
        self.assertTrue(h.ok)
        self.assertEqual(h.subtype, "help")

    def test_url_normalisation_strips_queries_and_unifies_x_and_twitter(self):
        a = xe.normalise_url("https://twitter.com/Me/status/55?s=20&t=abc")
        b = xe.normalise_url("https://www.x.com/me/status/55/photo/1")
        self.assertEqual(a, b)
        self.assertEqual(a, "x.com/me/status/55")
        self.assertEqual(xe.normalise_url("https://Example.com/Path/?q=1#frag"), "example.com/Path")


class TestDuplicates(Base):
    def test_duplicates_are_rejected_across_members_by_link_and_by_image(self):
        self.post(1, "build", MON, url="example.com/demo", file_hash="abc")
        self.assertIsNotNone(self.e.check_duplicate(2, "build", "example.com/demo", None))
        self.assertIsNotNone(self.e.check_duplicate(2, "build", None, "abc"))
        self.assertIsNone(self.e.check_duplicate(2, "build", "example.com/other", "zzz"))

    def test_rejected_or_withdrawn_proof_can_be_resubmitted(self):
        sid = self.post(1, "build", MON, url="example.com/demo")
        self.e.reject(sid, REVIEWER, "blurry", MON)
        self.assertIsNone(self.e.check_duplicate(1, "build", "example.com/demo", None))
        self.assertTrue(self.s.has_rejected_match("example.com/demo", None))

    def test_reach_member_may_resubmit_their_own_approved_link_for_a_milestone(self):
        sid = self.post(1, "reach", MON, url="x.com/me/status/1")
        self.approve(sid, "post_original")
        self.assertIsNone(self.e.check_duplicate(1, "reach", "x.com/me/status/1", None))
        self.assertIsNotNone(self.e.check_duplicate(2, "reach", "x.com/me/status/1", None))
        pending = self.post(1, "reach", MON, url="x.com/me/status/2")
        self.assertIsNotNone(self.e.check_duplicate(1, "reach", "x.com/me/status/2", None))

    def test_official_window_flag(self):
        self.e.open_official_post("https://x.com/G_NEXTGEN/status/9", MON)
        self.assertTrue(self.e.within_official_window(xe.ts(MON + datetime.timedelta(minutes=119))))
        self.assertFalse(self.e.within_official_window(xe.ts(MON + datetime.timedelta(minutes=121))))

    def test_young_account_flag(self):
        self.assertTrue(self.e.young_account(MON - datetime.timedelta(days=3), MON))
        self.assertFalse(self.e.young_account(MON - datetime.timedelta(days=8), MON))


class TestReviewActions(Base):
    def test_zero_points_records_a_zero_row_and_counts_strikes(self):
        counts = []
        for n in range(3):
            sid = self.post(1, "build", MON + datetime.timedelta(days=n))
            counts.append(self.e.zero_points(sid, REVIEWER, MON))
        self.assertEqual(counts, [1, 2, 3])
        self.assertEqual(self.s.get_award("1:zero")["points"], 0)
        self.assertEqual(self.s.get_submission(1)["status"], "rejected")
        self.assertIsNone(self.e.zero_points(1, REVIEWER, MON))  # already reviewed

    def test_reject_withdraw_and_state_changes_are_one_way(self):
        sid = self.post(1, "build", MON)
        self.assertTrue(self.e.reject(sid, REVIEWER, "no", MON))
        self.assertFalse(self.e.reject(sid, REVIEWER, "no", MON))
        self.assertEqual(self.approve(sid, "build_share").error, "not_pending")
        sid2 = self.post(1, "build", MON)
        self.assertTrue(self.e.withdraw(sid2, MON))
        self.assertEqual(self.approve(sid2, "build_share").error, "not_pending")

    def test_failed_award_does_not_leave_a_half_approved_submission(self):
        sid = self.post(1, "build", MON)
        original = self.e._grant
        self.e._grant = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self.approve(sid, "build_share")
        self.e._grant = original
        self.assertEqual(self.s.get_submission(sid)["status"], "pending")
        self.assertTrue(self.approve(sid, "build_share").ok)

    def test_manual_award_and_adjustment_write_ledger_rows(self):
        a = self.e.award_manual(1, 7, "event_facilitate", "ran the space", REVIEWER, MON, key="abc")
        self.assertEqual((a.points, a.lane), (25, "builder"))
        again = self.e.award_manual(1, 7, "event_facilitate", "ran the space", REVIEWER, MON, key="abc")
        self.assertEqual(again.points, 0)
        self.assertTrue(self.e.adjust(1, 7, "reach", -5, "typo", REVIEWER, MON, key="xyz"))
        self.assertEqual(self.pts(7, "builder"), 25)
        self.assertEqual(self.pts(7, "reach"), -5)


class TestLeaderboardAndReview(Base):
    def give(self, user, lane, points, when=MON, key=None):
        self.s.insert_award(guild_id=1, user_id=user, lane=lane, category="adjustment", points=points, reason="",
                            submission_id=None, award_key=key or f"{user}:{lane}:{points}:{when}", awarded_by=0,
                            created_at=xe.ts(when))

    def test_leaderboard_is_a_sum_over_the_ledger_and_skips_excluded_and_hidden(self):
        self.give(1, "reach", 50)
        self.give(2, "reach", 80)
        self.give(3, "reach", 70)
        self.give(2, "builder", 10)
        self.s.exclude(3, "test")
        rows = self.e.leaderboard("reach", "cycle", dt(2026, 10, 6), hide=lambda u: u == 1)
        self.assertEqual(rows, [(1, 2, 80)])
        combined = self.e.leaderboard("combined", "cycle", dt(2026, 10, 6))
        self.assertEqual([(r, u, p) for r, u, p in combined], [(1, 2, 90), (2, 1, 50)])

    def test_cycle_leaderboard_ignores_other_cycles_but_alltime_does_not(self):
        self.give(1, "builder", 40, when=dt(2026, 10, 6))
        self.give(1, "builder", 60, when=dt(2026, 10, 20))
        self.assertEqual(self.e.leaderboard("builder", "cycle", dt(2026, 10, 21))[0][2], 60)
        self.assertEqual(self.e.leaderboard("builder", "alltime", dt(2026, 10, 21))[0][2], 100)

    def test_ties_share_a_rank(self):
        self.give(1, "reach", 10)
        self.give(2, "reach", 10)
        self.give(3, "reach", 5)
        self.assertEqual([r for r, _, _ in self.e.leaderboard("reach", "cycle", MON)], [1, 1, 3])

    def test_user_summary_shows_progress_and_rank(self):
        self.give(1, "reach", 60)
        self.give(2, "reach", 90)
        self.give(1, "builder", 30)
        s = self.e.user_summary(1, dt(2026, 10, 6))
        self.assertEqual((s["reach"], s["builder"], s["reach_rank"], s["builder_rank"]), (60, 30, 2, 1))
        self.assertEqual(s["reach_alltime"], 60)
        self.assertEqual((s["min_reach"], s["min_builder"]), (100, 100))

    def test_cycle_review_lists_only_members_meeting_both_minimums_and_not_elite(self):
        self.e.rules.elite_slots = 2
        for u, r, b in ((1, 150, 120), (2, 100, 100), (3, 200, 90), (4, 90, 300), (5, 120, 130), (6, 500, 500), (7, 110, 110)):
            self.give(u, "reach", r)
            self.give(u, "builder", b)
        self.s.exclude(7, "test")
        rep = self.e.cycle_review(dt(2026, 10, 10), is_elite=lambda u: u == 6)
        self.assertEqual([x["user_id"] for x in rep["selected"]], [1, 5])       # ranked by combined total, 2 slots
        self.assertEqual(rep["waiting"], 1)                                      # user 2 also qualifies
        self.assertEqual(sorted(x["user_id"] for x in rep["near_misses"]), [3, 4])
        self.assertEqual(rep["top_builder"]["user_id"], 4)
        self.assertEqual(rep["top_reach"]["user_id"], 3)

    def test_cycle_review_top_referrer(self):
        self.e.register_referral(1, 200, 100, MON, dt(2025, 1, 1), MON)
        self.e.register_referral(1, 201, 100, MON, dt(2025, 1, 1), MON)
        self.e.register_referral(1, 300, 101, MON, dt(2025, 1, 1), MON)
        self.assertEqual(self.e.cycle_review(dt(2026, 10, 10))["top_referrer"], {"user_id": 100, "points": 10})


class TestLegacyImport(Base):
    OLD = [(10, 1, 3000), (11, 1, 860), (12, 1, 535), (13, 1, 10), (15, 1, 285), (16, 1, 1)]
    ELITES = {10}   # member 10 holds the Elite role

    def setUp(self):
        super().setUp()
        self.s._exec("CREATE TABLE member_xp (user_id BIGINT, guild_id BIGINT, xp INTEGER, PRIMARY KEY (user_id, guild_id))")
        for u, g, xp in self.OLD + [(14, 1, 0)]:
            self.s._exec("INSERT INTO member_xp (user_id, guild_id, xp) VALUES (%s,%s,%s)", (u, g, xp))

    def old_rows(self):
        return self.s._all("SELECT user_id, guild_id, xp FROM member_xp ORDER BY user_id")

    def run_import(self, when=dt(2026, 10, 6, 9)):
        return self.e.import_legacy_xp(when, skip_ids=self.ELITES)

    def test_each_balance_is_split_in_half_between_builder_and_reach(self):
        res = self.run_import()
        self.assertEqual((self.pts(11, "builder"), self.pts(11, "reach")), (430, 430))     # 860
        self.assertEqual((self.pts(13, "builder"), self.pts(13, "reach")), (5, 5))         # 10
        self.assertEqual((res["imported"], res["points"], res["builder"], res["reach"]), (5, 1691, 847, 844))
        row = self.s.get_award("legacy:1:11:reach")
        self.assertEqual((row["lane"], row["category"], row["points"], row["created_at"], row["submission_id"]),
                         ("reach", "legacy_import", 430, "2026-10-05 00:00:00", None))
        self.assertEqual(res["stamp"], "2026-10-05 00:00:00")

    def test_an_odd_point_goes_to_builder(self):
        self.run_import()
        self.assertEqual((self.pts(12, "builder"), self.pts(12, "reach")), (268, 267))     # 535
        self.assertEqual((self.pts(15, "builder"), self.pts(15, "reach")), (143, 142))     # 285
        for uid in (11, 12, 13, 15):
            self.assertEqual(self.pts(uid, "builder") + self.pts(uid, "reach"),
                             dict((u, x) for u, _g, x in self.OLD)[uid])                   # nothing lost, nothing added

    def test_a_single_point_goes_to_builder_and_no_empty_reach_row_is_written(self):
        self.run_import()
        self.assertEqual((self.pts(16, "builder"), self.pts(16, "reach")), (1, 0))
        self.assertIsNone(self.s.get_award("legacy:1:16:reach"))

    def test_a_zero_balance_is_not_imported(self):
        self.run_import()
        self.assertIsNone(self.s.get_award("legacy:1:14:builder"))
        self.assertIsNone(self.s.get_award("legacy:1:14:reach"))

    def test_elites_start_from_zero(self):
        res = self.run_import()
        self.assertEqual(res["elite"], 1)
        self.assertEqual(self.pts(10), 0)  # the old 3,000 is not carried over for an Elite
        self.assertIsNone(self.s.get_award("legacy:1:10:builder"))

    def test_it_is_idempotent(self):
        first = self.run_import()
        again = self.run_import(dt(2026, 10, 7))
        self.assertEqual((again["imported"], again["already"], again["points"]), (0, 5, 0))
        self.assertEqual(self.pts(11, "builder"), 430)
        self.assertEqual(sum(r["pts"] for r in self.s.lane_totals("0", "9")), first["points"])

    def test_a_half_finished_import_is_completed_without_double_paying(self):
        self.s.insert_award(guild_id=1, user_id=11, lane="builder", category="legacy_import", points=430, reason="",
                            submission_id=None, award_key="legacy:1:11:builder", awarded_by=None,
                            created_at="2026-10-05 00:00:00")
        res = self.run_import()
        self.assertEqual((self.pts(11, "builder"), self.pts(11, "reach")), (430, 430))
        self.assertEqual(res["imported"], 5)

    def test_a_later_import_is_stamped_in_the_cycle_it_runs_in(self):
        res = self.run_import(dt(2026, 10, 20))
        self.assertEqual(res["stamp"], "2026-10-19 00:00:00")
        self.assertEqual(self.pts(11, "reach", *self.e.cycle_range(1)), 430)
        self.assertEqual(self.pts(11, "builder", *self.e.cycle_range(0)), 0)

    def test_excluded_members_are_skipped(self):
        self.s.exclude(11, "test")
        res = self.run_import()
        self.assertEqual((res["imported"], res["excluded"]), (4, 1))
        self.assertEqual(self.pts(11), 0)

    def test_the_old_table_is_left_exactly_as_it_was(self):
        before = self.old_rows()
        self.run_import()
        self.assertEqual(self.old_rows(), before)

    def test_imported_points_show_on_both_boards_and_an_elite_can_still_appear_by_earning(self):
        self.run_import()
        build = self.e.leaderboard("builder", "cycle", dt(2026, 10, 6))
        reach = self.e.leaderboard("reach", "cycle", dt(2026, 10, 6))
        self.assertEqual([(r, u, p) for r, u, p in build][:3], [(1, 11, 430), (2, 12, 268), (3, 15, 143)])
        self.assertEqual([(r, u, p) for r, u, p in reach][:3], [(1, 11, 430), (2, 12, 267), (3, 15, 142)])
        # the Elite is visible as soon as they earn something, starting from zero
        self.approve(self.post(10, "build", dt(2026, 10, 7)), "build_demo")
        self.assertIn((4, 10, 20), self.e.leaderboard("builder", "cycle", dt(2026, 10, 8)))

    def test_members_with_200_or_more_old_points_meet_both_minimums_straight_away(self):
        self.run_import()
        review = self.e.cycle_review(dt(2026, 10, 6), is_elite=lambda u: u in self.ELITES)
        self.assertEqual([x["user_id"] for x in review["selected"]], [11, 12, 15])           # 860, 535, 285 (each half is at least 100)
        self.assertEqual(review["selected"][0]["reach"], 430)
        self.assertEqual(review["near_misses"], [])                                          # 10 and 1 are nowhere near

    def test_members_under_200_old_points_are_near_misses_not_qualifiers(self):
        self.s._exec("INSERT INTO member_xp (user_id, guild_id, xp) VALUES (%s,%s,%s)", (17, 1, 170))
        self.s._exec("INSERT INTO member_xp (user_id, guild_id, xp) VALUES (%s,%s,%s)", (18, 1, 199))
        self.run_import()
        review = self.e.cycle_review(dt(2026, 10, 6), is_elite=lambda u: u in self.ELITES)
        self.assertNotIn(17, [x["user_id"] for x in review["selected"]])                     # 85 + 85
        self.assertNotIn(18, [x["user_id"] for x in review["selected"]])                     # 100 + 99: one short on Reach
        self.assertEqual((self.pts(18, "builder"), self.pts(18, "reach")), (100, 99))

    def test_imported_xp_does_not_use_up_daily_caps_or_trigger_bonuses(self):
        self.run_import(dt(2026, 10, 5, 12))
        res = self.approve(self.post(11, "reach", dt(2026, 10, 5, 13), url="x.com/a/status/1"), "official_engage")
        self.assertEqual((res.awards[0].points, res.bonuses), (10, []))


class TestHalveAndCapCorrection(Base):
    CORR = dict(key="halve-cap-test", factor_num=1, factor_den=2, cap=100, reason="test correction")

    def give(self, user, lane, points):
        self.s.insert_award(guild_id=1, user_id=user, lane=lane, category="legacy_import", points=points, reason="",
                            submission_id=None, award_key=f"seed:{user}:{lane}", awarded_by=None,
                            created_at="2026-10-05 00:00:00")

    def balance(self, user, lane):
        return self.pts(user, lane)

    def test_balances_are_halved_rounding_up_then_capped_at_100(self):
        cases = {5: 3, 20: 10, 60: 30, 100: 50, 185: 93, 199: 100, 201: 100, 1500: 100}
        for n, (start, _end) in enumerate(cases.items()):
            self.give(100 + n, "builder", start)
        res = self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        for n, (start, end) in enumerate(cases.items()):
            self.assertEqual(self.balance(100 + n, "builder"), end, f"{start} -> {end}")
        self.assertEqual(res["changed"], len(cases))
        self.assertEqual(res["removed"], sum(start - end for start, end in cases.items()))

    def test_nobody_ends_above_100_in_either_lane(self):
        for n, amount in enumerate((5, 185, 370, 860, 1500, 99999)):
            self.give(200 + n, "builder", amount)
            self.give(200 + n, "reach", amount)
        self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        self.assertTrue(all(b["pts"] <= 100 for b in self.s.lane_balances()))

    def test_both_lanes_are_handled_independently(self):
        self.give(7, "builder", 185)
        self.give(7, "reach", 20)
        self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        self.assertEqual((self.balance(7, "builder"), self.balance(7, "reach")), (93, 10))

    def test_it_only_ever_lowers_a_balance(self):
        self.give(8, "builder", 1)       # half of 1 rounds up to 1: nothing to remove
        self.give(9, "builder", 0)
        self.give(10, "builder", -5)     # a negative balance is left alone
        res = self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        self.assertEqual((self.balance(8, "builder"), self.balance(9, "builder"), self.balance(10, "builder")), (1, 0, -5))
        self.assertEqual((res["changed"], res["removed"]), (0, 0))

    def test_it_is_written_as_visible_adjustment_rows_and_nothing_is_deleted(self):
        self.give(11, "reach", 60)
        self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        rows = {r["category"]: r for r in self.s.ledger_for_user(11)}
        self.assertEqual(rows["legacy_import"]["points"], 60)                      # the original row is untouched
        adj = rows["adjustment"]
        self.assertEqual((adj["points"], adj["lane"], adj["reason"], adj["award_key"], adj["created_at"]),
                         (-30, "reach", "test correction", "adjust:halve-cap-test:11:reach", "2026-10-06 14:00:00"))

    def test_running_it_twice_changes_nothing_the_second_time(self):
        self.give(12, "builder", 185)
        self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        again = self.e.apply_correction(self.CORR, dt(2026, 10, 6, 15))
        self.assertEqual((again["changed"], again["removed"]), (0, 0))
        self.assertEqual(self.balance(12, "builder"), 93)

    def test_leaderboards_and_the_shortlist_reflect_the_correction(self):
        for uid, amount in ((13, 860), (14, 40)):
            self.give(uid, "builder", amount)
            self.give(uid, "reach", amount)
        self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        board = self.e.leaderboard("builder", "cycle", dt(2026, 10, 6, 15))
        self.assertEqual([(r, u, p) for r, u, p in board], [(1, 13, 100), (2, 14, 20)])
        review = self.e.cycle_review(dt(2026, 10, 6, 15))
        self.assertEqual([x["user_id"] for x in review["selected"]], [13])         # exactly at both minimums

    def test_points_earned_after_the_correction_are_kept_in_full(self):
        self.give(15, "builder", 60)
        self.e.apply_correction(self.CORR, dt(2026, 10, 6, 14))
        self.approve(self.post(15, "build", dt(2026, 10, 7)), "build_project")      # +30 afterwards
        self.assertEqual(self.balance(15, "builder"), 30 + 30)

    def test_the_configured_correction_is_the_one_the_owner_asked_for(self):
        corr = pc.CORRECTIONS[0]
        self.assertEqual((corr["factor_num"], corr["factor_den"], corr["cap"]), (1, 2, 100))


class TestPointsCap(Base):
    """A permanent (for now) ceiling of 100 per member per lane."""

    def setUp(self):
        super().setUp()
        self.e.rules.points_cap = 100

    def seed(self, user, lane, points, key=None):
        self.s.insert_award(guild_id=1, user_id=user, lane=lane, category="adjustment", points=points, reason="",
                            submission_id=None, award_key=key or f"seed:{user}:{lane}", awarded_by=None,
                            created_at="2026-10-05 00:00:00")

    def test_an_award_that_would_pass_the_cap_is_trimmed_to_what_fits(self):
        self.seed(7, "builder", 90)
        a = self.approve(self.post(7, "build", MON), "build_demo").awards[0]       # worth 20
        self.assertEqual(a.points, 10)
        self.assertIn("trimmed to the 100 point cap", a.notes)
        self.assertEqual(self.pts(7, "builder"), 100)

    def test_at_the_cap_an_award_pays_nothing_and_says_so(self):
        self.seed(7, "builder", 100)
        a = self.approve(self.post(7, "build", MON), "build_project").awards[0]
        self.assertEqual(a.points, 0)
        self.assertIn("points cap reached", a.notes)
        self.assertEqual(self.pts(7, "builder"), 100)
        self.assertEqual(self.s.get_award(f"{a_sid(self)}:build_project")["points"], 0)   # the zero row is on record

    def test_the_cap_is_per_lane(self):
        self.seed(7, "builder", 100)
        a = self.approve(self.post(7, "reach", MON, url="x.com/a/status/1"), "post_original").awards[0]
        self.assertEqual((a.points, self.pts(7, "builder"), self.pts(7, "reach")), (25, 100, 25))

    def test_it_holds_across_cycles_because_it_is_the_all_time_balance(self):
        self.seed(7, "builder", 100)
        later = dt(2026, 10, 25)                       # cycle 1
        a = self.approve(self.post(7, "build", later), "build_share", now=later).awards[0]
        self.assertEqual(a.points, 0)

    def test_engagement_claims_are_capped_too(self):
        self.seed(7, "reach", 99)
        pid, _ = self.e.open_official_post("https://x.com/G_NEXTGEN/status/1", MON)
        _, cid = self.e.claim_engagement(1, pid, 7, "comment", "h", "", MON)
        a = self.e.approve_claim(cid, REVIEWER, MON).awards[0]
        self.assertEqual((a.points, self.pts(7, "reach")), (1, 100))

    def test_bonuses_and_referral_payouts_are_capped_too(self):
        self.seed(100, "reach", 98)
        r = self.e.register_referral(1, 200, 100, MON, dt(2025, 1, 1), MON)           # referral_join pays 5
        self.assertEqual((r["award"].points, self.pts(100, "reach")), (2, 100))
        self.seed(8, "reach", 90)
        bonus = self.e._bonus(sub_or_none=None, guild_id=1, user_id=8, lane="reach", category="streak_posts", points=30,
                              reason="streak", key="streak:test", created_at=xe.ts(MON))
        self.assertEqual((bonus.points, bonus.base, self.pts(8, "reach")), (10, 30, 100))
        self.assertIn("trimmed to the 100 point cap", bonus.notes)

    def test_staff_awards_are_capped_but_staff_adjustments_are_not(self):
        self.seed(9, "builder", 95)
        a = self.e.award_manual(1, 9, "event_facilitate", "ran a space", REVIEWER, MON, key="k")   # worth 25
        self.assertEqual(a.points, 5)
        self.assertTrue(self.e.adjust(1, 9, "builder", 50, "owner decision", REVIEWER, MON, key="adj"))
        self.assertEqual(self.pts(9, "builder"), 150)                                   # an explicit correction wins
        self.assertTrue(self.e.adjust(1, 9, "builder", -60, "fix", REVIEWER, MON, key="adj2"))
        self.assertEqual(self.pts(9, "builder"), 90)

    def test_points_can_be_earned_again_after_a_correction_lowers_a_balance(self):
        self.seed(10, "builder", 100)
        self.e.adjust(1, 10, "builder", -40, "fix", REVIEWER, MON, key="adj")
        a = self.approve(self.post(10, "build", MON), "build_demo").awards[0]
        self.assertEqual((a.points, self.pts(10, "builder")), (20, 80))

    def test_a_cap_of_zero_turns_it_off(self):
        self.e.rules.points_cap = 0
        self.seed(11, "builder", 500)
        self.assertEqual(self.approve(self.post(11, "build", MON), "build_project").awards[0].points, 30)

    def test_the_import_and_the_one_off_correction_are_not_capped(self):
        self.s._exec("CREATE TABLE member_xp (user_id BIGINT, guild_id BIGINT, xp INTEGER, PRIMARY KEY (user_id, guild_id))")
        self.s._exec("INSERT INTO member_xp (user_id, guild_id, xp) VALUES (%s,%s,%s)", (12, 1, 1500))
        self.e.import_legacy_xp(dt(2026, 10, 6))
        self.assertEqual((self.pts(12, "builder"), self.pts(12, "reach")), (750, 750))
        self.e.apply_correction(pc.CORRECTIONS[0], dt(2026, 10, 6, 14))
        self.assertEqual((self.pts(12, "builder"), self.pts(12, "reach")), (100, 100))

    def test_the_daily_caps_and_the_points_cap_work_together(self):
        self.seed(13, "reach", 90)
        self.approve(self.post(13, "reach", MON, url="x.com/a/status/1"), "official_engage")   # +10, now 100
        a = self.approve(self.post(13, "reach", MON, url="x.com/a/status/2"), "official_engage").awards[0]
        self.assertEqual(a.points, 0)

    def test_the_default_setting_is_100(self):
        import config
        self.assertEqual(xe.Rules.from_config(config).points_cap, config.POINTS_CAP)
        self.assertEqual(config.POINTS_CAP, 100)


def a_sid(case):
    """The id of the most recent submission."""
    return case.s._one("SELECT MAX(id) AS n FROM submissions")["n"]


class TestEngagementClaims(Base):
    def setUp(self):
        super().setUp()
        self.post_id, _ = self.e.open_official_post("https://x.com/G_NEXTGEN/status/1", MON)

    def claim(self, user, action, when=MON, post=None, handle="someone", proof=""):
        return self.e.claim_engagement(1, post or self.post_id, user, action, handle, proof, when)

    def test_each_button_is_worth_its_configured_points(self):
        got = {}
        for action in ("like", "retweet", "comment"):
            status, cid = self.claim(7, action)
            self.assertEqual(status, "pending")
            res = self.e.approve_claim(cid, REVIEWER, MON)
            got[action] = (res.ok, res.awards[0].points, res.awards[0].lane, res.awards[0].category)
        self.assertEqual(got, {"like": (True, 2, "reach", "official_like"),
                               "retweet": (True, 3, "reach", "official_retweet"),
                               "comment": (True, 5, "reach", "official_comment")})
        self.assertEqual(self.pts(7, "reach"), 10)   # a fully engaged post is worth 10
        self.assertEqual(self.pts(7, "builder"), 0)

    def test_nothing_is_awarded_until_a_moderator_approves(self):
        status, cid = self.claim(7, "comment")
        self.assertEqual(self.pts(7), 0)
        self.assertEqual(self.s.get_claim(cid)["status"], "pending")
        self.e.approve_claim(cid, REVIEWER, MON)
        self.assertEqual(self.pts(7, "reach"), 5)

    def test_one_live_claim_per_member_post_and_action(self):
        _, cid = self.claim(7, "like")
        again = self.claim(7, "like")
        self.assertEqual(again, ("exists", "pending"))
        self.e.approve_claim(cid, REVIEWER, MON)
        self.assertEqual(self.claim(7, "like"), ("exists", "approved"))
        # a different action, member or post is fine
        self.assertEqual(self.claim(7, "retweet")[0], "pending")
        self.assertEqual(self.claim(8, "like")[0], "pending")
        other, _ = self.e.open_official_post("https://x.com/G_NEXTGEN/status/2", MON)
        self.assertEqual(self.claim(7, "like", post=other)[0], "pending")

    def test_double_click_on_approve_pays_once(self):
        _, cid = self.claim(7, "comment")
        first = self.e.approve_claim(cid, REVIEWER, MON)
        second = self.e.approve_claim(cid, REVIEWER, MON)
        self.assertEqual((first.ok, second.ok, second.error), (True, False, "not_pending"))
        self.assertEqual(self.pts(7), 5)
        self.assertIsNotNone(self.s.get_award(f"claim:{cid}"))

    def test_declined_claims_pay_nothing_and_can_be_tried_again(self):
        _, cid = self.claim(7, "retweet")
        self.assertTrue(self.e.decline_claim(cid, REVIEWER, MON))
        self.assertFalse(self.e.decline_claim(cid, REVIEWER, MON))
        self.assertEqual(self.e.approve_claim(cid, REVIEWER, MON).error, "not_pending")
        self.assertEqual(self.pts(7), 0)
        status, cid2 = self.claim(7, "retweet")
        self.assertEqual(status, "pending")
        self.assertNotEqual(cid, cid2)

    def test_the_daily_official_engagement_cap_is_shared_with_claims(self):
        self.s.insert_award(guild_id=1, user_id=7, lane="reach", category="official_engage", points=29, reason="",
                            submission_id=None, award_key="seed", awarded_by=0, created_at=xe.ts(MON))
        _, cid = self.claim(7, "comment")
        a = self.e.approve_claim(cid, REVIEWER, MON).awards[0]
        self.assertEqual(a.points, 1)
        self.assertIn("trimmed to the daily cap", a.notes)
        _, cid2 = self.claim(7, "like")
        self.assertEqual(self.e.approve_claim(cid2, REVIEWER, MON).awards[0].points, 0)

    def test_a_claim_counts_for_the_day_it_was_made_not_the_day_it_was_approved(self):
        _, cid = self.claim(7, "comment", when=dt(2026, 10, 18, 23, 59, 59))
        self.e.approve_claim(cid, REVIEWER, dt(2026, 10, 19, 9))
        self.assertEqual(self.pts(7, "reach", *self.e.cycle_range(0)), 5)
        self.assertEqual(self.pts(7, "reach", *self.e.cycle_range(1)), 0)

    def test_excluded_members_cannot_claim_and_nothing_is_paid_if_excluded_later(self):
        self.s.exclude(7, "test")
        self.assertEqual(self.claim(7, "like")[0], "excluded")
        _, cid = self.claim(8, "like")
        self.s.exclude(8, "test")
        self.assertEqual(self.e.approve_claim(cid, REVIEWER, MON).awards[0].points, 0)

    def test_unknown_posts_and_actions_are_refused(self):
        self.assertEqual(self.claim(7, "like", post=999)[0], "no_post")
        self.assertEqual(self.claim(7, "share")[0], "invalid")

    def test_a_failed_award_puts_the_claim_back_to_pending(self):
        _, cid = self.claim(7, "like")
        original = self.e._grant_claim
        self.e._grant_claim = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self.e.approve_claim(cid, REVIEWER, MON)
        self.e._grant_claim = original
        self.assertEqual(self.s.get_claim(cid)["status"], "pending")
        self.assertTrue(self.e.approve_claim(cid, REVIEWER, MON).ok)

    def test_x_usernames_are_parsed_from_handles_and_profile_links(self):
        for text in ("@Sonofpeace", "Sonofpeace", "  @Sonofpeace ", "https://x.com/Sonofpeace",
                     "https://twitter.com/Sonofpeace/", "https://x.com/Sonofpeace/status/1?s=20"):
            self.assertEqual(xe.parse_x_handle(text), "Sonofpeace", text)
        for text in ("", "two words", "@toolongusername1234", "https://example.com/me", "a-b"):
            self.assertIsNone(xe.parse_x_handle(text), text)

    def test_the_claim_rows_are_idempotent_at_the_database_level(self):
        # the partial unique index lets a second live claim through only after a decline
        self.s.add_claim(guild_id=1, post_id=self.post_id, user_id=9, action="like", handle="h", proof="", created_at=xe.ts(MON))
        self.assertIsNone(self.s.add_claim(guild_id=1, post_id=self.post_id, user_id=9, action="like", handle="h",
                                           proof="", created_at=xe.ts(MON)))


class TestConfigData(unittest.TestCase):
    def test_catalogue_matches_the_brief(self):
        c = pc.CATEGORIES
        expect = {
            "official_engage": 10, "post_value": 15, "post_original": 25, "post_thread": 40,
            "reach_likes_25": 15, "reach_likes_100": 40, "referral_join": 5, "referral_lesson": 20,
            "referral_active": 25, "referral_elite": 50, "weekly_challenge_share": 25,
            "academy_lesson": 5, "academy_test": 10, "academy_proof": 5, "build_share": 10, "build_demo": 20,
            "build_project": 30, "tutorial_simple": 30, "tutorial_detailed": 50, "tutorial_process": 75,
            "tutorial_exceptional": 100, "prompt_result": 10, "prompt_detailed": 20, "prompt_workflow": 30,
            "event_attend": 10, "event_participate": 15, "event_facilitate": 25, "help_new_member": 15,
            "help_academy_task": 15, "teach_skill": 25, "learn_together": 10, "weekly_challenge_build": 25,
            "official_like": 2, "official_retweet": 3, "official_comment": 5,
        }
        self.assertEqual({k: v["points"] for k, v in c.items()}, expect)
        self.assertEqual(pc.DAILY_CAPS["official_engage"]["limit"], 30)
        self.assertEqual(pc.DAILY_CAPS["own_posts"]["limit"], 3)
        self.assertEqual(pc.STREAK_BONUSES["streak_posts"]["points"], 30)
        self.assertEqual(pc.STREAK_BONUSES["streak_academy"]["points"], 20)
        self.assertEqual(pc.REFERRAL["cap_per_cycle"], 5)

    def test_every_category_sits_in_the_right_lane(self):
        for key, v in pc.CATEGORIES.items():
            for ch in v["channels"]:
                self.assertEqual(pc.CHANNEL_LANES[ch], v["lane"], key)
        self.assertEqual(pc.CHANNEL_LANES["reach"], "reach")
        self.assertTrue(all(pc.CHANNEL_LANES[k] == "builder" for k in ("academy", "build", "tutorial", "prompt_result")))

    def test_bot_text_has_no_emoji_or_em_dashes(self):
        texts = [xe.MSG_MULTI_DAY, xe.MSG_NEEDS_PROOF, xe.MSG_NEEDS_DAY, xe.MSG_DUPLICATE, xe.MSG_RECEIVED]
        texts += [v["label"] for v in pc.CATEGORIES.values()]
        for t in texts:
            self.assertNotIn("—", t)
            self.assertTrue(all(ord(ch) < 0x2190 for ch in t), t)
        for name in ("xp_cog.py", "xp_engine.py"):
            source = read_source(name)
            self.assertNotIn("—", source, name)
            self.assertFalse([ch for ch in source if ord(ch) > 0x2100], name)


if __name__ == "__main__":
    unittest.main()
