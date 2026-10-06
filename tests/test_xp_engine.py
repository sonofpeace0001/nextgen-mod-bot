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

    def test_needs_a_link_or_an_attachment(self):
        self.assertEqual(xe.check_intake("build", "look at my thing", 0).reply, xe.MSG_NEEDS_PROOF)
        self.assertTrue(xe.check_intake("build", "look at my thing", 1).ok)
        self.assertTrue(xe.check_intake("build", "see https://example.com/x", 0).ok)

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
