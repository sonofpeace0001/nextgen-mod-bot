"""Points rules. Data only: edit numbers here, no logic lives in this file.

Two lanes:
  reach   - growing NEXTGEN (official post engagement, posting about NEXTGEN, referrals).
  builder - learning and building (Academy, projects, tutorials, prompt results, events, helping).

Everything runs on UTC. A day is 00:00:00 to 23:59:59 UTC, a week is Monday 00:00:00 UTC to
Sunday 23:59:59 UTC, and cycles are counted from CYCLE_START_DATE (see config.py).

Channel keys used below map to channel IDs in config.PROOF_CHANNELS:
  reach, academy, build, tutorial, prompt_result
"""

REACH = "reach"
BUILDER = "builder"
LANES = (REACH, BUILDER)

# Proof channel key -> lane. A submission's lane always comes from the channel it was posted in.
CHANNEL_LANES = {
    "reach": REACH,
    "academy": BUILDER,
    "build": BUILDER,
    "tutorial": BUILDER,
    "prompt_result": BUILDER,
}

# category -> rules
#   lane      which lane the points go to
#   points    base points
#   channels  proof channel keys where a reviewer may pick it ([] = never shown in the menu)
#   label     sentence case name used in bot text
#   staff     True = only awarded through /award (never in the reviewer menu)
#   system    True = awarded by the bot itself (referrals, bonuses)
CATEGORIES = {
    # ---- Reach lane ----
    "official_engage":  dict(lane=REACH, points=10, channels=["reach"], label="official post engagement"),
    "post_value":       dict(lane=REACH, points=15, channels=["reach"], label="valuable post about NEXTGEN"),
    "post_original":    dict(lane=REACH, points=25, channels=["reach"], label="original post about NEXTGEN"),
    "post_thread":      dict(lane=REACH, points=40, channels=["reach"], label="thread about NEXTGEN"),
    "reach_likes_25":   dict(lane=REACH, points=15, channels=["reach"], label="25 likes milestone"),
    "reach_likes_100":  dict(lane=REACH, points=40, channels=["reach"], label="100 likes milestone"),
    "referral_join":    dict(lane=REACH, points=5,  channels=[], label="referral joined", system=True),
    "referral_lesson":  dict(lane=REACH, points=20, channels=[], label="referral first lesson", system=True),
    "referral_active":  dict(lane=REACH, points=25, channels=[], label="referral active", system=True),
    "referral_elite":   dict(lane=REACH, points=50, channels=[], label="referral became Elite", system=True),
    "weekly_challenge_share": dict(lane=REACH, points=25, channels=["reach"], label="weekly challenge share"),
    # Official post buttons (Like / Repost / Comment on a card the bot posts). Claimed with a
    # button, verified by a moderator, never picked from a reviewer menu. A fully engaged post
    # is worth 10, the same as the old single "official engagement" award.
    "official_like":    dict(lane=REACH, points=2, channels=[], label="like on an official post", claim=True),
    "official_retweet": dict(lane=REACH, points=3, channels=[], label="repost of an official post", claim=True),
    "official_comment": dict(lane=REACH, points=5, channels=[], label="comment on an official post", claim=True),

    # ---- Builder lane ----
    "academy_lesson":   dict(lane=BUILDER, points=5,  channels=["academy"], label="Academy lesson"),
    "academy_test":     dict(lane=BUILDER, points=10, channels=["academy"], label="Academy test"),
    "academy_proof":    dict(lane=BUILDER, points=5,  channels=["academy"], label="Academy proof"),
    "build_share":      dict(lane=BUILDER, points=10, channels=["build"], label="build share"),
    "build_demo":       dict(lane=BUILDER, points=20, channels=["build"], label="build demo"),
    "build_project":    dict(lane=BUILDER, points=30, channels=["build"], label="build project"),
    "tutorial_simple":      dict(lane=BUILDER, points=30,  channels=["tutorial"], label="simple tutorial"),
    "tutorial_detailed":    dict(lane=BUILDER, points=50,  channels=["tutorial"], label="detailed tutorial"),
    "tutorial_process":     dict(lane=BUILDER, points=75,  channels=["tutorial"], label="process tutorial"),
    "tutorial_exceptional": dict(lane=BUILDER, points=100, channels=["tutorial"], label="exceptional tutorial"),
    "prompt_result":    dict(lane=BUILDER, points=10, channels=["prompt_result"], label="prompt result"),
    "prompt_detailed":  dict(lane=BUILDER, points=20, channels=["prompt_result"], label="detailed prompt result"),
    "prompt_workflow":  dict(lane=BUILDER, points=30, channels=["prompt_result"], label="prompt workflow"),
    "event_attend":      dict(lane=BUILDER, points=10, channels=[], label="event attendance", staff=True),
    "event_participate": dict(lane=BUILDER, points=15, channels=[], label="event participation", staff=True),
    "event_facilitate":  dict(lane=BUILDER, points=25, channels=[], label="event facilitation", staff=True),
    "help_new_member":   dict(lane=BUILDER, points=15, channels=["academy"], label="helping a new member"),
    "help_academy_task": dict(lane=BUILDER, points=15, channels=["academy"], label="helping with an Academy task"),
    "teach_skill":       dict(lane=BUILDER, points=25, channels=["academy"], label="teaching a skill"),
    "learn_together":    dict(lane=BUILDER, points=10, channels=[], label="learning together", system=True),
    "weekly_challenge_build": dict(lane=BUILDER, points=25, channels=["build"], label="weekly challenge build"),
}

# Academy submissions come in two shapes. A post that links to a Discord message is a
# "helping" submission (menu: HELP_CATEGORIES, single select). Anything else is a lesson
# submission (menu: LESSON_CATEGORIES, multi select) and must say which day it is.
LESSON_CATEGORIES = ["academy_lesson", "academy_test", "academy_proof"]
HELP_CATEGORIES = ["help_new_member", "help_academy_task", "teach_skill"]

# Daily caps, counted per UTC day. mode "points" caps the points awarded in the group
# (a partial award is trimmed to fit); mode "count" caps how many counted awards exist.
DAILY_CAPS = {
    "official_engage": dict(categories=["official_engage", "official_like", "official_retweet", "official_comment"],
                            mode="points", limit=30),
    "own_posts": dict(categories=["post_value", "post_original", "post_thread"], mode="count", limit=3),
}

# Reach proof is tied to a post URL. Each category pays once per URL per member, and a URL can
# only earn one of the own-post categories. (The likes milestones are the "once per post URL"
# rule from the brief; extending it to the other Reach categories stops a resubmitted URL
# from paying twice.)
ONCE_PER_URL_CATEGORIES = [
    "official_engage", "post_value", "post_original", "post_thread",
    "reach_likes_25", "reach_likes_100",
]
ONE_OF_PER_URL_GROUPS = [["post_value", "post_original", "post_thread"]]

# Weekly streak bonuses (awarded once per UTC week, keyed streak:{lane}:{week}:{user}).
#   categories  counted from approved ledger rows with points above zero
#   channels    counted from approved submissions in these proof channels
STREAK_BONUSES = {
    "streak_posts": dict(lane=REACH, categories=["post_value", "post_original", "post_thread"],
                         distinct_days=5, points=30, label="own-post streak"),
    "streak_academy": dict(lane=BUILDER, channels=["academy"],
                           distinct_days=5, points=20, label="Academy streak"),
}

# Referrals
REFERRAL = dict(
    cap_per_cycle=5,          # counted referrals per inviter, by the cycle the invitee joined in
    lesson_window_days=7,     # stage 2: first approved Academy lesson within this many days of joining
    active_min_age_days=14,   # stage 3: invitee must be at least this old...
    active_min_days=3,        # ...with approved submissions on this many distinct UTC days
    expire_days=30,           # stage 3 still unmet at this age: the referral expires
    invitedby_window_days=7,  # /invitedby is usable for this long after joining
)

# Learn together: both people linked by a referral have an approved Academy lesson with the
# same lesson_day in the same UTC week. Once per pair per week.
LEARN_TOGETHER = dict(category="learn_together")

# Weekly challenge
CHALLENGE = dict(
    both_bonus_builder=10,    # both parts approved on time for the same challenge
    both_bonus_reach=10,
    streak_length=4,          # consecutive challenges completed (both parts each time)
    streak_bonus=40,          # Builder points each time the streak reaches a multiple of streak_length
    build_category="weekly_challenge_build",
    share_category="weekly_challenge_share",
)

# Official post engagement: the buttons on the card the bot posts, and the action each one claims.
ENGAGE = dict(
    actions={"like": "official_like", "retweet": "official_retweet", "comment": "official_comment"},
    button_labels={"like": "Like", "retweet": "Retweet", "comment": "Comment"},
)

# One-time import of the old single XP balance (member_xp) into the ledger. Each balance is split
# into equal halves across `lanes` (an odd point goes to the first lane, Builder), so a member with
# 285 old points gets 143 Builder and 142 Reach. Members who hold the Elite role start from zero and
# are skipped. Each lane row has its own key legacy:{guild}:{user}:{lane}, so running it again
# changes nothing. Rows are stamped at the start of the cycle that is current when the import runs,
# so they count toward that cycle and toward all-time totals.
LEGACY_IMPORT = dict(lanes=[BUILDER, REACH], category="legacy_import", reason="imported from the old XP balance")

# One-off corrections, each applied once (a kv done flag, plus a unique key per member and lane).
# A correction scales every lane balance by factor_num/factor_den, rounding UP (so 5 becomes 3 when
# halving), then caps the result at `cap`. It only ever lowers a balance, and it is written as an
# ordinary ledger adjustment row, so the history stays visible and nothing is deleted.
CORRECTIONS = [
    dict(key="halve-cap-2026-10-06", factor_num=1, factor_den=2, cap=100,
         reason="owner correction 2026-10-06: halved, then capped at 100 per lane"),
]

# Builder channels accept any post (a link, a screenshot or just text such as "task completed").
# Text-only posts shorter than this are ignored so a quick "ok" does not create a review.
MIN_TEXT_ONLY_CHARS = 8

# Live leaderboards: one message per lane, edited in place.
BOARD = dict(top=10, kv_prefix="points_board_msg", refresh_delay_seconds=5)

# Strikes (zero-points / spam). Staff are alerted at this many.
STRIKE_ALERT_AT = 3

# Leaderboards
LEADERBOARD_PAGE_SIZE = 10
LEADERBOARD_FETCH = 100        # rows pulled before hidden members are filtered out
