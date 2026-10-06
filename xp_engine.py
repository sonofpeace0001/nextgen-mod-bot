"""Points engine. Deterministic, no LLM, no Discord objects: everything here takes plain data
and a Store, so it can be unit tested against an in-memory SQLite database.

All time is UTC. A day is 00:00:00 to 23:59:59 UTC, a week is Monday 00:00:00 UTC to Sunday
23:59:59 UTC, and cycles count from CYCLE_START_DATE read as 00:00:00 UTC. Timestamps are
stored as 'YYYY-MM-DD HH:MM:SS' text in UTC. Nothing in here uses another timezone.

Event time: a ledger row for a submission is stamped with the time the proof was POSTED, not
the time a reviewer got to it. That keeps a Sunday 23:59 proof in the closing week, day and
cycle even if it is approved on Monday.
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field

import points_config as pc
from points_store import ALL_TIME_END, ALL_TIME_START

log = logging.getLogger("xp_engine")

UTC = datetime.timezone.utc
TS_FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Time helpers (UTC only)
# ---------------------------------------------------------------------------


def to_utc(dt):
    """Aware UTC datetime. A naive datetime is read as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def ts(dt):
    return to_utc(dt).strftime(TS_FMT)


def parse_ts(s):
    return datetime.datetime.strptime(s, TS_FMT).replace(tzinfo=UTC)


def day_key(dt):
    return to_utc(dt).strftime("%Y-%m-%d")


def day_bounds(dt):
    """(start, end) of the UTC day as timestamps, end exclusive."""
    d = to_utc(dt).replace(hour=0, minute=0, second=0, microsecond=0)
    return ts(d), ts(d + datetime.timedelta(days=1))


def week_start(dt):
    d = to_utc(dt).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - datetime.timedelta(days=d.weekday())  # Monday


def week_key(dt):
    return week_start(dt).strftime("%Y-%m-%d")


def week_bounds(dt):
    """(start, end) of the UTC week, Monday 00:00:00 to the next Monday, end exclusive."""
    s = week_start(dt)
    return ts(s), ts(s + datetime.timedelta(days=7))


def next_sunday_end(dt):
    """Sunday 23:59:59 UTC at the end of the coming Sunday. On a Sunday it is the following one."""
    d = to_utc(dt)
    days_ahead = 6 - d.weekday()
    if days_ahead == 0:
        days_ahead = 7
    end = (d + datetime.timedelta(days=days_ahead)).replace(hour=23, minute=59, second=59, microsecond=0)
    return end


def cycle_number(dt, start_date, length_days):
    """floor((UTC date - CYCLE_START_DATE) / CYCLE_LENGTH_DAYS). Negative before the start."""
    return (to_utc(dt).date() - start_date).days // length_days


def cycle_bounds(n, start_date, length_days):
    """(start, end) timestamps of cycle n, end exclusive."""
    s = datetime.datetime.combine(start_date + datetime.timedelta(days=n * length_days),
                                  datetime.time(0, 0, 0), tzinfo=UTC)
    return ts(s), ts(s + datetime.timedelta(days=length_days))


# ---------------------------------------------------------------------------
# Rules (numbers that come from env config)
# ---------------------------------------------------------------------------


@dataclass
class Rules:
    cycle_start: datetime.date
    cycle_length: int = 14
    official_window_minutes: int = 120
    min_account_age_days: int = 7
    elite_min_reach: int = 100
    elite_min_builder: int = 100
    elite_slots: int = 5
    channel_ids: dict = field(default_factory=dict)  # channel key -> channel id
    warnings: list = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg):
        warnings = []
        try:
            start = datetime.datetime.strptime(cfg.CYCLE_START_DATE, "%Y-%m-%d").date()
        except ValueError:
            start = datetime.datetime.strptime(cfg._DEFAULT_CYCLE_START, "%Y-%m-%d").date()
            warnings.append(f"CYCLE_START_DATE '{cfg.CYCLE_START_DATE}' is not YYYY-MM-DD; using {start}.")
        if getattr(cfg, "CYCLE_START_DATE_IS_DEFAULT", False):
            warnings.append(f"CYCLE_START_DATE is not set; using the default {start}. Set it to your first cycle date.")
        return cls(
            cycle_start=start,
            cycle_length=cfg.CYCLE_LENGTH_DAYS,
            official_window_minutes=cfg.OFFICIAL_POST_WINDOW_MINUTES,
            min_account_age_days=cfg.MIN_ACCOUNT_AGE_DAYS,
            elite_min_reach=cfg.ELITE_MIN_REACH,
            elite_min_builder=cfg.ELITE_MIN_BUILDER,
            elite_slots=cfg.ELITE_SLOTS_PER_CYCLE,
            channel_ids=dict(cfg.PROOF_CHANNELS),
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Intake validation (pure functions)
# ---------------------------------------------------------------------------

MSG_MULTI_DAY = "Please submit one day at a time. Submissions with several days combined earn no points."
MSG_NEEDS_PROOF = "Add a link or screenshot so a reviewer can check this."
MSG_NEEDS_DAY = "Add which day this is, for example Day 3."
MSG_DUPLICATE = "This proof has already been submitted."
MSG_RECEIVED = "Received. A reviewer will check your proof and award points."

# "day 1-2", "day 1 to 5", "days 1, 2", "day 1 and 2", "day 1 & 2", "day1-day2"
_MULTI_DAY_RE = re.compile(r"\bdays?\s*\d+\s*(?:-|\u2013|\u2014|to|and|&|,|/)\s*(?:day\s*)?\d+", re.IGNORECASE)
_DAY_RE = re.compile(r"\bday\s*(\d{1,3})\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_X_STATUS_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/[^/\s?#]+/status(?:es)?/(\d+)", re.IGNORECASE)
_DISCORD_MSG_RE = re.compile(
    r"https?://(?:\w+\.)?discord(?:app)?\.com/channels/\d+/\d+/\d+", re.IGNORECASE)


def has_multiple_days(text):
    return bool(_MULTI_DAY_RE.search(text or ""))


def parse_lesson_day(text):
    m = _DAY_RE.search(text or "")
    return int(m.group(1)) if m else None


def find_links(text):
    out = []
    for raw in _URL_RE.findall(text or ""):
        out.append(raw.rstrip(".,;:!?)]}>\"'"))
    return out


def is_x_status_link(url):
    return bool(_X_STATUS_RE.match(url or ""))


def is_help_submission(text):
    """An Academy post that links to a Discord message is a helping submission."""
    return bool(_DISCORD_MSG_RE.search(text or ""))


def normalise_url(url):
    """Stable key for a link: lower-case host, no www, no query or fragment, no trailing slash.
    X and Twitter links collapse to x.com/{user}/status/{id}."""
    m = _X_STATUS_RE.match(url)
    if m:
        user = re.match(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/\s?#]+)/", url, re.IGNORECASE).group(1)
        return f"x.com/{user.lower()}/status/{m.group(1)}"
    u = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    u = u.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    host, _, path = u.partition("/")
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + ("/" + path if path else "")


def primary_proof_url(channel_key, text):
    """The link a submission is keyed on: the X status link in Reach, else the first link."""
    links = find_links(text)
    if channel_key == "reach":
        for link in links:
            if is_x_status_link(link):
                return link
        return None
    return links[0] if links else None


@dataclass
class IntakeCheck:
    ok: bool
    code: str = ""             # multi_day | no_proof | no_day | ok
    reply: str = ""
    store_rejected: bool = False  # multi-day submissions are recorded as rejected
    lesson_day: int | None = None
    url_key: str | None = None
    subtype: str = ""          # "lesson" | "help" | ""


def check_intake(channel_key, text, attachment_count):
    """Validation in the brief's order: multi-day, needs proof, lesson day. Duplicates and the
    account-age flag are handled by the caller because they need the database."""
    text = text or ""
    if channel_key == "academy" and has_multiple_days(text):
        return IntakeCheck(False, "multi_day", MSG_MULTI_DAY, store_rejected=True)

    links = find_links(text)
    if channel_key == "reach":
        has_proof = any(is_x_status_link(link) for link in links)
    else:
        has_proof = bool(links) or attachment_count > 0
    if not has_proof:
        return IntakeCheck(False, "no_proof", MSG_NEEDS_PROOF)

    subtype = ""
    lesson_day = None
    if channel_key == "academy":
        if is_help_submission(text):
            subtype = "help"
        else:
            subtype = "lesson"
            lesson_day = parse_lesson_day(text)
            if lesson_day is None:
                return IntakeCheck(False, "no_day", MSG_NEEDS_DAY)

    url = primary_proof_url(channel_key, text)
    return IntakeCheck(True, "ok", lesson_day=lesson_day,
                       url_key=normalise_url(url) if url else None, subtype=subtype)


def menu_categories(channel_key, subtype=""):
    """Categories a reviewer may pick for a submission, and whether several may be picked."""
    if channel_key == "academy":
        if subtype == "help":
            return list(pc.HELP_CATEGORIES), False
        return list(pc.LESSON_CATEGORIES), True
    cats = [k for k, v in pc.CATEGORIES.items()
            if channel_key in v["channels"] and not v.get("staff") and not v.get("system")]
    return cats, False


def label(category):
    return pc.CATEGORIES.get(category, {}).get("label", category.replace("_", " "))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Award:
    category: str
    lane: str
    points: int
    base: int = 0
    notes: list = field(default_factory=list)
    user_id: int = 0
    inserted: bool = True


@dataclass
class ApprovalResult:
    ok: bool
    error: str = ""
    awards: list = field(default_factory=list)   # the reviewer's categories
    bonuses: list = field(default_factory=list)  # streaks, referrals, learn together, challenge


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    def __init__(self, store, rules):
        self.store = store
        self.rules = rules

    # ---- time shortcuts --------------------------------------------------
    def cycle_of(self, dt):
        return cycle_number(dt, self.rules.cycle_start, self.rules.cycle_length)

    def cycle_range(self, n):
        return cycle_bounds(n, self.rules.cycle_start, self.rules.cycle_length)

    def period_range(self, period, now):
        if period == "alltime":
            return ALL_TIME_START, ALL_TIME_END
        return self.cycle_range(self.cycle_of(now))

    # ---- intake support --------------------------------------------------
    def check_duplicate(self, user_id, channel_key, url_key, content_hash):
        """The earlier submission this one duplicates, or None. Cross-member duplicates are
        always rejected. One exception: in Reach a member may resubmit their OWN already
        approved post link, to claim a likes milestone (the engine still stops a category
        paying twice for the same link)."""
        for row in self.store.find_duplicates(url_key, content_hash):
            via_url_only = bool(url_key) and row["url_key"] == url_key and not (
                content_hash and row["content_hash"] == content_hash)
            if (channel_key == "reach" and row["user_id"] == user_id
                    and row["status"] == "approved" and via_url_only):
                continue
            return row
        return None

    def within_official_window(self, created_ts):
        return self.store.official_post_open_at(created_ts) is not None

    def young_account(self, account_created_at, at):
        age = (to_utc(at) - to_utc(account_created_at)).total_seconds() / 86400
        return age < self.rules.min_account_age_days

    def open_official_post(self, url, now):
        closes = to_utc(now) + datetime.timedelta(minutes=self.rules.official_window_minutes)
        pid = self.store.add_official_post(url, ts(now), ts(closes))
        return pid, ts(closes)

    # ---- low level award -------------------------------------------------
    def _insert(self, *, guild_id, user_id, lane, category, points, reason, submission_id,
                award_key, awarded_by, created_at):
        return self.store.insert_award(
            guild_id=guild_id, user_id=user_id, lane=lane, category=category, points=points,
            reason=reason, submission_id=submission_id, award_key=award_key,
            awarded_by=awarded_by, created_at=created_at)

    def _apply_caps(self, user_id, category, points, created_ts):
        """Daily caps by UTC day of the event. Points over the cap are not awarded."""
        day_start, day_end = day_bounds(parse_ts(created_ts))
        note = ""
        for cap in pc.DAILY_CAPS.values():
            if category not in cap["categories"]:
                continue
            used_pts, used_n = self.store.day_usage(user_id, cap["categories"], day_start, day_end)
            if cap["mode"] == "points":
                remaining = cap["limit"] - used_pts
                if remaining <= 0:
                    return 0, "daily cap reached"
                if points > remaining:
                    points, note = remaining, "trimmed to the daily cap"
            elif used_n >= cap["limit"]:
                return 0, "daily limit reached"
        return points, note

    def challenge_check(self, created_ts):
        """(on_time, note, challenge) for an entry posted at created_ts."""
        ch = self.store.challenge_started_before(created_ts)
        if ch is None:
            return False, "no challenge was running", None
        if created_ts > ch["ends_at"]:
            return False, "posted after the challenge closed", ch
        return True, "", ch

    def _grant(self, sub, category, reviewer_id):
        cat = pc.CATEGORIES[category]
        uid, created = sub["user_id"], sub["created_at"]
        base = points = cat["points"]
        notes = []
        if self.store.is_excluded(uid):
            points = 0
            notes.append("member is excluded from points")
        else:
            if category in (pc.CHALLENGE["build_category"], pc.CHALLENGE["share_category"]):
                on_time, note, _ch = self.challenge_check(created)
                if not on_time:
                    points = 0
                    notes.append(note)
            if points > 0 and sub.get("url_key") and category in pc.ONCE_PER_URL_CATEGORIES:
                have = self.store.url_awards(uid, sub["url_key"], pc.ONCE_PER_URL_CATEGORIES, sub["id"])
                if category in have:
                    points = 0
                    notes.append("already paid for this link")
                else:
                    for group in pc.ONE_OF_PER_URL_GROUPS:
                        if category in group and have & set(group):
                            points = 0
                            notes.append("this link already earned a post award")
            if points > 0:
                points, note = self._apply_caps(uid, category, points, created)
                if note:
                    notes.append(note)
        row_id = self._insert(
            guild_id=sub["guild_id"], user_id=uid, lane=cat["lane"], category=category, points=points,
            reason="; ".join(notes), submission_id=sub["id"], award_key=f"{sub['id']}:{category}",
            awarded_by=reviewer_id, created_at=created)
        if row_id is None:
            return Award(category, cat["lane"], 0, base, ["already awarded"], uid, inserted=False)
        return Award(category, cat["lane"], points, base, notes, uid)

    # ---- review actions --------------------------------------------------
    def approve(self, sid, categories, reviewer_id, now):
        sub = self.store.get_submission(sid)
        if not sub:
            return ApprovalResult(False, "not_found")
        channel_key = next((k for k, v in self.rules.channel_ids.items() if v == sub["channel_id"]), "")
        subtype = "help" if "help" in (sub.get("flags") or "").split(",") else ""
        allowed, multi = menu_categories(channel_key, subtype)
        categories = list(dict.fromkeys(categories))  # drop repeats, keep order
        if not categories:
            return ApprovalResult(False, "no_category")
        if any(c not in allowed for c in categories):
            return ApprovalResult(False, "bad_category")
        if len(categories) > 1 and not multi:
            return ApprovalResult(False, "single_only")
        if not self.store.transition(sid, "pending", "approved", reviewer_id, ts(now)):
            return ApprovalResult(False, "not_pending")
        try:
            awards = [self._grant(sub, c, reviewer_id) for c in categories]
            bonuses = self._after_awards(sub, awards, channel_key, reviewer_id, now)
        except Exception:
            self.store.revert_to_pending(sid)  # never leave an approved row with missing awards
            raise
        return ApprovalResult(True, awards=awards, bonuses=bonuses)

    def reject(self, sid, reviewer_id, reason, now):
        return self.store.transition(sid, "pending", "rejected", reviewer_id, ts(now), reason)

    def withdraw(self, sid, now):
        return self.store.transition(sid, "pending", "withdrawn", None, ts(now), "message deleted")

    def zero_points(self, sid, reviewer_id, now):
        """Spam: a 0-point ledger row, a strike, and the new strike count. None if not pending."""
        sub = self.store.get_submission(sid)
        if not sub or not self.store.transition(sid, "pending", "rejected", reviewer_id, ts(now), "zero points (spam)"):
            return None
        self._insert(
            guild_id=sub["guild_id"], user_id=sub["user_id"], lane=sub["lane"], category="zero_points",
            points=0, reason="spam", submission_id=sid, award_key=f"{sid}:zero",
            awarded_by=reviewer_id, created_at=sub["created_at"])
        return self.store.add_strike(sub["user_id"], "spam proof")

    # ---- bonuses and linked awards --------------------------------------
    def _after_awards(self, sub, awards, channel_key, reviewer_id, now):
        paid = {a.category for a in awards if a.points > 0}
        uid = sub["user_id"]
        created_dt = parse_ts(sub["created_at"])
        out = []
        if self.store.is_excluded(uid):
            return out
        for key, rule in pc.STREAK_BONUSES.items():
            if rule.get("categories") and not (paid & set(rule["categories"])):
                continue
            if rule.get("channels") and channel_key not in rule["channels"]:
                continue
            out += self._streak(key, rule, sub, created_dt)
        if "academy_lesson" in paid:
            out += self.evaluate_referral(uid, now, has_elite=False, expire=False)
            out += self._learn_together(sub, created_dt)
        if paid & {pc.CHALLENGE["build_category"], pc.CHALLENGE["share_category"]}:
            out += self._challenge_bonuses(sub)
        return out

    def _bonus(self, *, sub_or_none, guild_id, user_id, lane, category, points, reason, key, created_at, by=None):
        row_id = self._insert(
            guild_id=guild_id, user_id=user_id, lane=lane, category=category, points=points,
            reason=reason, submission_id=(sub_or_none["id"] if sub_or_none else None),
            award_key=key, awarded_by=by, created_at=created_at)
        if row_id is None:
            return None
        return Award(category, lane, points, points, [reason] if reason else [], user_id)

    def _streak(self, key, rule, sub, created_dt):
        uid = sub["user_id"]
        wk, (start, end) = week_key(created_dt), week_bounds(created_dt)
        award_key = f"streak:{rule['lane']}:{wk}:{uid}"
        if self.store.award_exists(award_key):
            return []
        if rule.get("categories"):
            days = self.store.distinct_award_days(uid, rule["categories"], start, end)
        else:
            ids = [self.rules.channel_ids[k] for k in rule["channels"] if k in self.rules.channel_ids]
            days = self.store.distinct_submission_days(uid, ids, start, end)
        if days < rule["distinct_days"]:
            return []
        a = self._bonus(sub_or_none=sub, guild_id=sub["guild_id"], user_id=uid, lane=rule["lane"],
                        category=key, points=rule["points"],
                        reason=f"{rule['label']}: {days} days in the week of {wk}",
                        key=award_key, created_at=sub["created_at"])
        return [a] if a else []

    def _learn_together(self, sub, created_dt):
        day = sub.get("lesson_day")
        if day is None:
            return []
        uid = sub["user_id"]
        wk, (start, end) = week_key(created_dt), week_bounds(created_dt)
        cat = pc.CATEGORIES[pc.LEARN_TOGETHER["category"]]
        out = []
        for partner in sorted(self.store.referral_partners(uid)):
            if partner == uid or self.store.is_excluded(partner):
                continue
            if not self.store.lesson_in_week(partner, day, start, end):
                continue
            lo, hi = sorted((uid, partner))
            base = f"learn:{wk}:{lo}:{hi}"
            if self.store.award_exists(f"{base}:{lo}") or self.store.award_exists(f"{base}:{hi}"):
                continue  # once per pair per week
            for u in (lo, hi):
                a = self._bonus(sub_or_none=sub, guild_id=sub["guild_id"], user_id=u, lane=cat["lane"],
                                category=pc.LEARN_TOGETHER["category"], points=cat["points"],
                                reason=f"both did Day {day} in the week of {wk}", key=f"{base}:{u}",
                                created_at=sub["created_at"])
                if a:
                    out.append(a)
        return out

    # ---- weekly challenge ------------------------------------------------
    def create_challenge(self, title, description, now):
        starts, ends = ts(now), ts(next_sunday_end(now))
        cid = self.store.add_challenge(title, description, starts, ends)
        return cid, starts, ends

    def current_challenge(self, now):
        """The challenge running at `now`, or None."""
        ch = self.store.challenge_started_before(ts(now))
        if ch and ts(now) <= ch["ends_at"]:
            return ch
        return None

    def _both_done(self, user_id, ch):
        return (self.store.challenge_part_done(user_id, pc.CHALLENGE["build_category"], ch["starts_at"], ch["ends_at"])
                and self.store.challenge_part_done(user_id, pc.CHALLENGE["share_category"], ch["starts_at"], ch["ends_at"]))

    def challenge_streak(self, user_id, ch):
        """Consecutive challenges, ending at ch, completed on both parts."""
        challenges = self.store.all_challenges()
        idx = next((i for i, c in enumerate(challenges) if c["id"] == ch["id"]), None)
        if idx is None:
            return 0
        n = 0
        for c in reversed(challenges[: idx + 1]):
            if not self._both_done(user_id, c):
                break
            n += 1
        return n

    def _challenge_bonuses(self, sub):
        on_time, _note, ch = self.challenge_check(sub["created_at"])
        if not on_time or ch is None:
            return []
        uid = sub["user_id"]
        if not self._both_done(uid, ch):
            return []
        cfg = pc.CHALLENGE
        out = []
        for lane, pts in ((pc.BUILDER, cfg["both_bonus_builder"]), (pc.REACH, cfg["both_bonus_reach"])):
            a = self._bonus(sub_or_none=sub, guild_id=sub["guild_id"], user_id=uid, lane=lane,
                            category="challenge_both", points=pts,
                            reason=f"both challenge parts done for '{ch['title']}'",
                            key=f"challenge_both:{ch['id']}:{uid}:{lane}", created_at=sub["created_at"])
            if a:
                out.append(a)
        streak = self.challenge_streak(uid, ch)
        if streak and streak % cfg["streak_length"] == 0:
            a = self._bonus(sub_or_none=sub, guild_id=sub["guild_id"], user_id=uid, lane=pc.BUILDER,
                            category="challenge_streak", points=cfg["streak_bonus"],
                            reason=f"{streak} challenges in a row", key=f"challenge_streak:{uid}:{ch['id']}",
                            created_at=sub["created_at"])
            if a:
                out.append(a)
        return out

    # ---- referrals -------------------------------------------------------
    def register_referral(self, guild_id, invitee_id, inviter_id, joined_at, account_created_at, now):
        """Attribute an invitee to an inviter. First attribution wins. Returns a dict with
        status: attributed | self | exists. counted is False past the per-cycle cap."""
        if inviter_id == invitee_id:
            return {"status": "self"}
        if self.store.get_referral(invitee_id):
            return {"status": "exists"}
        joined_ts = ts(joined_at)
        start, end = self.cycle_range(self.cycle_of(joined_at))
        counted = self.store.count_counted_referrals(inviter_id, start, end) < pc.REFERRAL["cap_per_cycle"]
        flagged = self.young_account(account_created_at, joined_at)
        if not self.store.add_referral(invitee_id=invitee_id, guild_id=guild_id, inviter_id=inviter_id,
                                       joined_at=joined_ts, counted=counted, flagged=flagged):
            return {"status": "exists"}
        award = None
        if counted and not self.store.is_excluded(inviter_id):
            cat = pc.CATEGORIES["referral_join"]
            award = self._bonus(sub_or_none=None, guild_id=guild_id, user_id=inviter_id, lane=cat["lane"],
                                category="referral_join", points=cat["points"],
                                reason=f"invited {invitee_id}", key=f"referral:join:{invitee_id}",
                                created_at=joined_ts)
        if counted:
            self.store.set_stage(invitee_id, "stage_join")
        return {"status": "attributed", "counted": counted, "flagged": flagged, "award": award}

    def _pay_referral(self, r, category, key, created_at, reason):
        cat = pc.CATEGORIES[category]
        if self.store.is_excluded(r["inviter_id"]):
            return None
        return self._bonus(sub_or_none=None, guild_id=r["guild_id"], user_id=r["inviter_id"], lane=cat["lane"],
                           category=category, points=cat["points"], reason=reason, key=key, created_at=created_at)

    def evaluate_referral(self, invitee_id, now, has_elite=False, expire=False):
        """Pay any referral stage the invitee has reached. Safe to call repeatedly: every
        payout has a unique award_key. Stages 2 to 4 pay nothing while the referral is flagged."""
        r = self.store.get_referral(invitee_id)
        if not r or not r["counted"] or r["status"] != "active" or r["flagged"]:
            return []
        out = []
        joined = parse_ts(r["joined_at"])
        now_dt = to_utc(now)
        cfg = pc.REFERRAL

        if not r["stage_lesson"]:
            lesson = self.store.first_lesson(invitee_id)
            if lesson and parse_ts(lesson["created_at"]) <= joined + datetime.timedelta(days=cfg["lesson_window_days"]):
                a = self._pay_referral(r, "referral_lesson", f"referral:lesson:{invitee_id}",
                                       lesson["created_at"], f"{invitee_id} finished a first lesson")
                self.store.set_stage(invitee_id, "stage_lesson")
                if a:
                    out.append(a)

        if not r["stage_active"] and (now_dt - joined).days >= cfg["active_min_age_days"]:
            days = self.store.distinct_submission_days(invitee_id, [], ts(joined), ts(now_dt + datetime.timedelta(seconds=1)))
            if days >= cfg["active_min_days"]:
                a = self._pay_referral(r, "referral_active", f"referral:active:{invitee_id}", ts(now_dt),
                                       f"{invitee_id} active on {days} days")
                self.store.set_stage(invitee_id, "stage_active")
                r = dict(r, stage_active=1)
                if a:
                    out.append(a)

        if has_elite and not r["stage_elite"]:
            a = self._pay_referral(r, "referral_elite", f"referral:elite:{invitee_id}", ts(now_dt),
                                   f"{invitee_id} became Elite")
            self.store.set_stage(invitee_id, "stage_elite")
            if a:
                out.append(a)

        if expire and not r["stage_active"] and (now_dt - joined).days >= cfg["expire_days"]:
            self.store.set_referral_status(invitee_id, "expired")
        return out

    def sweep_referrals(self, now, elite_ids=()):
        """Hourly: pay stage 3, catch stage 4, expire stale referrals. Returns all new awards."""
        out = []
        elite_ids = set(elite_ids)
        for r in self.store.open_referrals():
            out += self.evaluate_referral(r["invitee_id"], now, has_elite=r["invitee_id"] in elite_ids, expire=True)
        return out

    def clear_flag(self, invitee_id, now, has_elite=False):
        """Staff cleared a young-account flag: release anything the invitee had already earned."""
        if not self.store.set_flag(invitee_id, False):
            return None
        return self.evaluate_referral(invitee_id, now, has_elite=has_elite)

    def invitedby_allowed(self, joined_at, now):
        return (to_utc(now) - to_utc(joined_at)).total_seconds() <= pc.REFERRAL["invitedby_window_days"] * 86400

    # ---- manual awards ---------------------------------------------------
    def award_manual(self, guild_id, user_id, category, reason, awarded_by, now, key):
        """Staff award (events and manual). Bypasses caps; still skips excluded members."""
        cat = pc.CATEGORIES[category]
        if self.store.is_excluded(user_id):
            return Award(category, cat["lane"], 0, cat["points"], ["member is excluded from points"], user_id)
        row = self._insert(guild_id=guild_id, user_id=user_id, lane=cat["lane"], category=category,
                           points=cat["points"], reason=reason, submission_id=None,
                           award_key=f"award:{key}", awarded_by=awarded_by, created_at=ts(now))
        if row is None:
            return Award(category, cat["lane"], 0, cat["points"], ["already awarded"], user_id, inserted=False)
        return Award(category, cat["lane"], cat["points"], cat["points"], [reason] if reason else [], user_id)

    def adjust(self, guild_id, user_id, lane, points, reason, awarded_by, now, key):
        row = self._insert(guild_id=guild_id, user_id=user_id, lane=lane, category="adjustment",
                           points=points, reason=reason, submission_id=None,
                           award_key=f"adjust:{key}", awarded_by=awarded_by, created_at=ts(now))
        return row is not None

    # ---- leaderboards and reports ---------------------------------------
    def _visible_totals(self, period, now, hide=None, lane=None):
        start, end = self.period_range(period, now)
        excluded = self.store.excluded_ids()
        totals = {}
        for r in self.store.lane_totals(start, end, None if lane in (None, "combined") else lane):
            totals[r["user_id"]] = totals.get(r["user_id"], 0) + r["pts"]
        return {u: p for u, p in totals.items()
                if p > 0 and u not in excluded and not (hide and hide(u))}

    @staticmethod
    def _rank(totals):
        ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
        out, last_pts, rank = [], None, 0
        for i, (u, p) in enumerate(ordered, 1):
            if p != last_pts:
                rank, last_pts = i, p
            out.append((rank, u, p))
        return out

    def leaderboard(self, lane, period, now, hide=None, limit=None):
        rows = self._rank(self._visible_totals(period, now, hide, lane))
        return rows[:limit] if limit else rows

    def user_summary(self, user_id, now, hide=None):
        n = self.cycle_of(now)
        start, end = self.cycle_range(n)
        out = {"cycle": n, "start": start, "end": end,
               "min_reach": self.rules.elite_min_reach, "min_builder": self.rules.elite_min_builder}
        for lane in pc.LANES:
            cyc = self._visible_totals("cycle", now, hide, lane)
            allt = self.store.lane_totals(ALL_TIME_START, ALL_TIME_END, lane)
            mine = next((r["pts"] for r in self.store.lane_totals(start, end, lane) if r["user_id"] == user_id), 0)
            out[lane] = mine
            out[f"{lane}_alltime"] = next((r["pts"] for r in allt if r["user_id"] == user_id), 0)
            out[f"{lane}_rank"] = (1 + sum(1 for p in cyc.values() if p > mine)) if (mine > 0 and user_id in cyc) else None
        return out

    def cycle_review(self, now, hide=None, is_elite=None):
        n = self.cycle_of(now)
        start, end = self.cycle_range(n)
        excluded = self.store.excluded_ids()
        by_lane = {pc.REACH: {}, pc.BUILDER: {}}
        for r in self.store.lane_totals(start, end):
            by_lane[r["lane"]][r["user_id"]] = r["pts"]
        users = set(by_lane[pc.REACH]) | set(by_lane[pc.BUILDER])

        def ok(u):
            return u not in excluded and not (hide and hide(u)) and not (is_elite and is_elite(u))

        qualifiers, near = [], []
        for u in users:
            if not ok(u):
                continue
            rp, bp = by_lane[pc.REACH].get(u, 0), by_lane[pc.BUILDER].get(u, 0)
            met_r, met_b = rp >= self.rules.elite_min_reach, bp >= self.rules.elite_min_builder
            row = {"user_id": u, "reach": rp, "builder": bp, "total": rp + bp}
            if met_r and met_b:
                qualifiers.append(row)
            elif met_r or met_b:
                row["missing"] = "builder" if met_r else "reach"
                near.append(row)
        key = lambda r: (-r["total"], -r["reach"], r["user_id"])
        qualifiers.sort(key=key)
        near.sort(key=key)

        def top(totals):
            vis = {u: p for u, p in totals.items() if p > 0 and ok(u)}
            if not vis:
                return None
            u = min(vis, key=lambda x: (-vis[x], x))
            return {"user_id": u, "points": vis[u]}

        refs = {r["user_id"]: r["pts"] for r in self.store.category_totals(
            ["referral_join", "referral_lesson", "referral_active", "referral_elite"], start, end)}
        return {
            "cycle": n, "start": start, "end": end,
            "selected": qualifiers[: self.rules.elite_slots],
            "waiting": max(0, len(qualifiers) - self.rules.elite_slots),
            "near_misses": near,
            "top_referrer": top(refs),
            "top_builder": top(by_lane[pc.BUILDER]),
            "top_reach": top(by_lane[pc.REACH]),
        }
