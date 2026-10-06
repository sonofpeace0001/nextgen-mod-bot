"""SQL layer for the points system.

The same SQL runs on Postgres (production, mod_bot schema, through database._run) and on
SQLite (the unit tests use an in-memory database). To keep that true:
  - timestamps are TEXT in 'YYYY-MM-DD HH:MM:SS' UTC, so string comparison is time comparison
  - the Python side supplies every timestamp, nothing uses the database clock
  - only portable SQL is used (ON CONFLICT, RETURNING, COALESCE, substr, COUNT(DISTINCT))

The ledger (xp_ledger) is the source of truth. Leaderboards are SUM queries over it.
"""
import sqlite3

ALL_TIME_START = "0000-01-01 00:00:00"
ALL_TIME_END = "9999-12-31 23:59:59"


def make_sqlite_run(conn):
    """Adapt a sqlite3 connection to the database._run(query, params, fetch, commit) shape."""
    conn.row_factory = sqlite3.Row

    def run(query, params=(), fetch=None, commit=False):
        cur = conn.execute(query.replace("%s", "?"), tuple(params))
        result = None
        if fetch == "one":
            row = cur.fetchone()
            result = dict(row) if row else None
        elif fetch == "all":
            result = [dict(r) for r in cur.fetchall()]
        if commit:
            conn.commit()
        cur.close()
        return result

    return run


def _marks(n):
    return ",".join(["%s"] * n)


class Store:
    def __init__(self, run, dialect="pg"):
        self._run = run
        self.dialect = dialect
        self.p = "mod_bot." if dialect == "pg" else ""

    # ---- plumbing --------------------------------------------------------
    def _one(self, sql, params=(), commit=False):
        return self._run(sql, tuple(params), fetch="one", commit=commit)

    def _all(self, sql, params=()):
        return self._run(sql, tuple(params), fetch="all")

    def _exec(self, sql, params=()):
        self._run(sql, tuple(params), commit=True)

    # ---- schema ----------------------------------------------------------
    def init_schema(self):
        p = self.p
        pk = "BIGSERIAL PRIMARY KEY" if self.dialect == "pg" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        stmts = [
            f"""CREATE TABLE IF NOT EXISTS {p}submissions (
                id {pk}, guild_id BIGINT, user_id BIGINT, channel_id BIGINT, message_id BIGINT,
                lane TEXT, status TEXT DEFAULT 'pending', lesson_day INTEGER,
                content_hash TEXT, url_key TEXT, review_message_id BIGINT DEFAULT 0,
                reviewer_id BIGINT, within_window INTEGER DEFAULT 0,
                created_at TEXT, reviewed_at TEXT,
                selected TEXT DEFAULT '', reason TEXT DEFAULT '', flags TEXT DEFAULT ''
            )""",
            f"""CREATE TABLE IF NOT EXISTS {p}xp_ledger (
                id {pk}, guild_id BIGINT, user_id BIGINT, lane TEXT, category TEXT,
                points INTEGER, reason TEXT DEFAULT '', submission_id BIGINT,
                award_key TEXT UNIQUE, awarded_by BIGINT, created_at TEXT
            )""",
            f"""CREATE TABLE IF NOT EXISTS {p}referrals (
                invitee_id BIGINT PRIMARY KEY, guild_id BIGINT, inviter_id BIGINT, joined_at TEXT,
                counted INTEGER DEFAULT 1, stage_join INTEGER DEFAULT 0, stage_lesson INTEGER DEFAULT 0,
                stage_active INTEGER DEFAULT 0, stage_elite INTEGER DEFAULT 0,
                flagged INTEGER DEFAULT 0, status TEXT DEFAULT 'active'
            )""",
            f"""CREATE TABLE IF NOT EXISTS {p}challenges (
                id {pk}, title TEXT, description TEXT, starts_at TEXT, ends_at TEXT,
                message_id BIGINT DEFAULT 0
            )""",
            f"""CREATE TABLE IF NOT EXISTS {p}official_posts (
                id {pk}, url TEXT, opened_at TEXT, closes_at TEXT
            )""",
            f"CREATE TABLE IF NOT EXISTS {p}strikes (user_id BIGINT PRIMARY KEY, count INTEGER DEFAULT 0, last_reason TEXT DEFAULT '')",
            f"CREATE TABLE IF NOT EXISTS {p}excluded_users (user_id BIGINT PRIMARY KEY, reason TEXT DEFAULT '')",
            f"CREATE TABLE IF NOT EXISTS {p}invite_cache (code TEXT PRIMARY KEY, uses INTEGER DEFAULT 0)",
            f"CREATE INDEX IF NOT EXISTS idx_xp_ledger_user_lane_created ON {p}xp_ledger (user_id, lane, created_at)",
            f"CREATE INDEX IF NOT EXISTS idx_points_submissions_url ON {p}submissions (url_key)",
            f"CREATE INDEX IF NOT EXISTS idx_points_submissions_hash ON {p}submissions (content_hash)",
            f"CREATE INDEX IF NOT EXISTS idx_points_submissions_user ON {p}submissions (user_id, status, created_at)",
        ]
        for s in stmts:
            self._exec(s)

    # ---- submissions -----------------------------------------------------
    def add_submission(self, *, guild_id, user_id, channel_id, message_id, lane, lesson_day,
                       content_hash, url_key, within_window, created_at,
                       status="pending", reason="", flags=""):
        r = self._one(
            f"INSERT INTO {self.p}submissions (guild_id,user_id,channel_id,message_id,lane,status,"
            "lesson_day,content_hash,url_key,within_window,created_at,reason,flags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (guild_id, user_id, channel_id, message_id, lane, status, lesson_day, content_hash,
             url_key, 1 if within_window else 0, created_at, reason, flags),
            commit=True,
        )
        return r["id"]

    def get_submission(self, sid):
        return self._one(f"SELECT * FROM {self.p}submissions WHERE id=%s", (sid,))

    def get_pending_by_message(self, channel_id, message_id):
        return self._one(
            f"SELECT * FROM {self.p}submissions WHERE channel_id=%s AND message_id=%s AND status='pending'",
            (channel_id, message_id))

    def pending_with_cards(self):
        return self._all(
            f"SELECT * FROM {self.p}submissions WHERE status='pending' AND review_message_id>0")

    def set_review_message(self, sid, mid):
        self._exec(f"UPDATE {self.p}submissions SET review_message_id=%s WHERE id=%s", (mid, sid))

    def set_selected(self, sid, csv):
        self._exec(f"UPDATE {self.p}submissions SET selected=%s WHERE id=%s AND status='pending'", (csv, sid))

    def transition(self, sid, from_status, to_status, reviewer_id, reviewed_at, reason=""):
        """Compare-and-set the status. True only for the one caller that wins, so a double
        click or a retry can never review the same submission twice."""
        r = self._one(
            f"UPDATE {self.p}submissions SET status=%s, reviewer_id=%s, reviewed_at=%s, reason=%s "
            "WHERE id=%s AND status=%s RETURNING id",
            (to_status, reviewer_id, reviewed_at, reason, sid, from_status), commit=True)
        return r is not None

    def revert_to_pending(self, sid):
        self._exec(
            f"UPDATE {self.p}submissions SET status='pending', reviewer_id=NULL, reviewed_at=NULL "
            "WHERE id=%s", (sid,))

    def find_duplicates(self, url_key, content_hash):
        clauses, params = [], []
        if url_key:
            clauses.append("url_key=%s"); params.append(url_key)
        if content_hash:
            clauses.append("content_hash=%s"); params.append(content_hash)
        if not clauses:
            return []
        return self._all(
            f"SELECT * FROM {self.p}submissions WHERE status IN ('pending','approved') "
            f"AND ({' OR '.join(clauses)}) ORDER BY id", params)

    def has_rejected_match(self, url_key, content_hash):
        clauses, params = [], []
        if url_key:
            clauses.append("url_key=%s"); params.append(url_key)
        if content_hash:
            clauses.append("content_hash=%s"); params.append(content_hash)
        if not clauses:
            return False
        r = self._one(
            f"SELECT id FROM {self.p}submissions WHERE status IN ('rejected','withdrawn') "
            f"AND ({' OR '.join(clauses)}) LIMIT 1", params)
        return r is not None

    def user_submission_count_since(self, user_id, since):
        r = self._one(
            f"SELECT COUNT(*) AS n FROM {self.p}submissions WHERE user_id=%s AND created_at>=%s",
            (user_id, since))
        return r["n"]

    # ---- ledger ----------------------------------------------------------
    def insert_award(self, *, guild_id, user_id, lane, category, points, reason, submission_id,
                     award_key, awarded_by, created_at):
        """Insert one ledger row. Returns the new id, or None when award_key already exists
        (so a retry, a double click or a restart can never pay twice)."""
        r = self._one(
            f"INSERT INTO {self.p}xp_ledger (guild_id,user_id,lane,category,points,reason,submission_id,"
            "award_key,awarded_by,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (award_key) DO NOTHING RETURNING id",
            (guild_id, user_id, lane, category, points, reason, submission_id, award_key,
             awarded_by, created_at), commit=True)
        return r["id"] if r else None

    def award_exists(self, award_key):
        return self._one(f"SELECT id FROM {self.p}xp_ledger WHERE award_key=%s", (award_key,)) is not None

    def get_award(self, award_key):
        return self._one(f"SELECT * FROM {self.p}xp_ledger WHERE award_key=%s", (award_key,))

    def day_usage(self, user_id, categories, start, end):
        """(points, count) of positive awards for these categories in [start, end)."""
        r = self._one(
            f"SELECT COALESCE(SUM(points),0) AS pts, COUNT(*) AS n FROM {self.p}xp_ledger "
            f"WHERE user_id=%s AND points>0 AND category IN ({_marks(len(categories))}) "
            "AND created_at>=%s AND created_at<%s",
            (user_id, *categories, start, end))
        return int(r["pts"]), int(r["n"])

    def url_awards(self, user_id, url_key, categories, exclude_submission_id=0):
        """Categories this member has already been paid for on this link."""
        rows = self._all(
            f"SELECT DISTINCT l.category FROM {self.p}xp_ledger l JOIN {self.p}submissions s "
            "ON s.id=l.submission_id WHERE l.user_id=%s AND s.url_key=%s AND l.points>0 "
            f"AND l.submission_id<>%s AND l.category IN ({_marks(len(categories))})",
            (user_id, url_key, exclude_submission_id, *categories))
        return {r["category"] for r in rows}

    def distinct_award_days(self, user_id, categories, start, end):
        r = self._one(
            f"SELECT COUNT(DISTINCT substr(created_at,1,10)) AS n FROM {self.p}xp_ledger "
            f"WHERE user_id=%s AND points>0 AND category IN ({_marks(len(categories))}) "
            "AND created_at>=%s AND created_at<%s",
            (user_id, *categories, start, end))
        return int(r["n"])

    def distinct_submission_days(self, user_id, channel_ids, start, end):
        """Distinct UTC days with an approved submission, optionally limited to channels."""
        sql = (f"SELECT COUNT(DISTINCT substr(created_at,1,10)) AS n FROM {self.p}submissions "
               "WHERE user_id=%s AND status='approved' AND created_at>=%s AND created_at<%s")
        params = [user_id, start, end]
        if channel_ids:
            sql += f" AND channel_id IN ({_marks(len(channel_ids))})"
            params += list(channel_ids)
        return int(self._one(sql, params)["n"])

    def legacy_xp_rows(self):
        """The old single-balance XP table (read only, never modified)."""
        return self._all(f"SELECT user_id, guild_id, xp FROM {self.p}member_xp WHERE xp>0 ORDER BY xp DESC, user_id")

    def first_lesson(self, user_id):
        """The member's earliest approved Academy lesson (a paid academy_lesson row)."""
        return self._one(
            f"SELECT s.* FROM {self.p}submissions s JOIN {self.p}xp_ledger l ON l.submission_id=s.id "
            "WHERE s.user_id=%s AND l.category='academy_lesson' AND l.points>0 "
            "ORDER BY s.created_at, s.id LIMIT 1", (user_id,))

    def lesson_in_week(self, user_id, lesson_day, start, end):
        return self._one(
            f"SELECT s.id FROM {self.p}submissions s JOIN {self.p}xp_ledger l ON l.submission_id=s.id "
            "WHERE s.user_id=%s AND s.lesson_day=%s AND l.category='academy_lesson' AND l.points>0 "
            "AND s.created_at>=%s AND s.created_at<%s LIMIT 1",
            (user_id, lesson_day, start, end)) is not None

    def lane_totals(self, start, end, lane=None):
        sql = (f"SELECT user_id, lane, COALESCE(SUM(points),0) AS pts FROM {self.p}xp_ledger "
               "WHERE created_at>=%s AND created_at<%s")
        params = [start, end]
        if lane:
            sql += " AND lane=%s"; params.append(lane)
        sql += " GROUP BY user_id, lane"
        return [dict(user_id=r["user_id"], lane=r["lane"], pts=int(r["pts"])) for r in self._all(sql, params)]

    def category_totals(self, categories, start, end):
        rows = self._all(
            f"SELECT user_id, COALESCE(SUM(points),0) AS pts FROM {self.p}xp_ledger "
            f"WHERE category IN ({_marks(len(categories))}) AND created_at>=%s AND created_at<%s "
            "GROUP BY user_id", (*categories, start, end))
        return [dict(user_id=r["user_id"], pts=int(r["pts"])) for r in rows]

    def ledger_for_user(self, user_id, limit=20):
        return self._all(
            f"SELECT * FROM {self.p}xp_ledger WHERE user_id=%s ORDER BY id DESC LIMIT %s", (user_id, limit))

    # ---- strikes and exclusions -----------------------------------------
    def add_strike(self, user_id, reason):
        r = self._one(
            f"INSERT INTO {self.p}strikes (user_id,count,last_reason) VALUES (%s,1,%s) "
            f"ON CONFLICT (user_id) DO UPDATE SET count={self.p}strikes.count+1, last_reason=%s "
            "RETURNING count", (user_id, reason, reason), commit=True)
        return int(r["count"])

    def get_strikes(self, user_id):
        r = self._one(f"SELECT count FROM {self.p}strikes WHERE user_id=%s", (user_id,))
        return int(r["count"]) if r else 0

    def exclude(self, user_id, reason):
        self._exec(
            f"INSERT INTO {self.p}excluded_users (user_id,reason) VALUES (%s,%s) "
            "ON CONFLICT (user_id) DO UPDATE SET reason=EXCLUDED.reason", (user_id, reason))

    def include(self, user_id):
        r = self._one(f"DELETE FROM {self.p}excluded_users WHERE user_id=%s RETURNING user_id",
                      (user_id,), commit=True)
        return r is not None

    def is_excluded(self, user_id):
        return self._one(f"SELECT user_id FROM {self.p}excluded_users WHERE user_id=%s", (user_id,)) is not None

    def excluded_ids(self):
        return {r["user_id"] for r in self._all(f"SELECT user_id FROM {self.p}excluded_users")}

    # ---- referrals -------------------------------------------------------
    def get_referral(self, invitee_id):
        return self._one(f"SELECT * FROM {self.p}referrals WHERE invitee_id=%s", (invitee_id,))

    def add_referral(self, *, invitee_id, guild_id, inviter_id, joined_at, counted, flagged):
        r = self._one(
            f"INSERT INTO {self.p}referrals (invitee_id,guild_id,inviter_id,joined_at,counted,flagged) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (invitee_id) DO NOTHING RETURNING invitee_id",
            (invitee_id, guild_id, inviter_id, joined_at, 1 if counted else 0, 1 if flagged else 0),
            commit=True)
        return r is not None

    def count_counted_referrals(self, inviter_id, start, end):
        r = self._one(
            f"SELECT COUNT(*) AS n FROM {self.p}referrals WHERE inviter_id=%s AND counted=1 "
            "AND joined_at>=%s AND joined_at<%s", (inviter_id, start, end))
        return int(r["n"])

    def referrals_by_inviter(self, inviter_id):
        return self._all(
            f"SELECT * FROM {self.p}referrals WHERE inviter_id=%s ORDER BY joined_at", (inviter_id,))

    def referral_partners(self, user_id):
        """Everyone linked to this member by a referral, in either direction."""
        out = set()
        r = self.get_referral(user_id)
        if r:
            out.add(r["inviter_id"])
        for row in self.referrals_by_inviter(user_id):
            out.add(row["invitee_id"])
        return out

    def open_referrals(self):
        return self._all(
            f"SELECT * FROM {self.p}referrals WHERE counted=1 AND status='active'")

    _STAGES = {"stage_join", "stage_lesson", "stage_active", "stage_elite"}

    def set_stage(self, invitee_id, stage):
        if stage not in self._STAGES:
            raise ValueError(stage)
        self._exec(f"UPDATE {self.p}referrals SET {stage}=1 WHERE invitee_id=%s", (invitee_id,))

    def set_referral_status(self, invitee_id, status):
        self._exec(f"UPDATE {self.p}referrals SET status=%s WHERE invitee_id=%s", (status, invitee_id))

    def set_flag(self, invitee_id, flagged):
        r = self._one(f"UPDATE {self.p}referrals SET flagged=%s WHERE invitee_id=%s RETURNING invitee_id",
                      (1 if flagged else 0, invitee_id), commit=True)
        return r is not None

    # ---- challenges ------------------------------------------------------
    def add_challenge(self, title, description, starts_at, ends_at):
        r = self._one(
            f"INSERT INTO {self.p}challenges (title,description,starts_at,ends_at) VALUES (%s,%s,%s,%s) "
            "RETURNING id", (title, description, starts_at, ends_at), commit=True)
        return r["id"]

    def set_challenge_message(self, cid, mid):
        self._exec(f"UPDATE {self.p}challenges SET message_id=%s WHERE id=%s", (mid, cid))

    def get_challenge(self, cid):
        return self._one(f"SELECT * FROM {self.p}challenges WHERE id=%s", (cid,))

    def challenge_started_before(self, ts):
        """The latest challenge that had started at ts (it may already have ended)."""
        return self._one(
            f"SELECT * FROM {self.p}challenges WHERE starts_at<=%s ORDER BY starts_at DESC, id DESC LIMIT 1",
            (ts,))

    def all_challenges(self):
        return self._all(f"SELECT * FROM {self.p}challenges ORDER BY id")

    def challenge_part_done(self, user_id, category, start, end):
        """Paid, on-time entry for this part: the ledger row's submission was posted inside the window."""
        r = self._one(
            f"SELECT l.id FROM {self.p}xp_ledger l JOIN {self.p}submissions s ON s.id=l.submission_id "
            "WHERE l.user_id=%s AND l.category=%s AND l.points>0 AND s.created_at>=%s AND s.created_at<=%s "
            "LIMIT 1", (user_id, category, start, end))
        return r is not None

    # ---- official posts --------------------------------------------------
    def add_official_post(self, url, opened_at, closes_at):
        r = self._one(
            f"INSERT INTO {self.p}official_posts (url,opened_at,closes_at) VALUES (%s,%s,%s) RETURNING id",
            (url, opened_at, closes_at), commit=True)
        return r["id"]

    def official_post_open_at(self, ts):
        return self._one(
            f"SELECT * FROM {self.p}official_posts WHERE opened_at<=%s AND closes_at>=%s "
            "ORDER BY id DESC LIMIT 1", (ts, ts))

    def latest_official_post(self):
        return self._one(f"SELECT * FROM {self.p}official_posts ORDER BY id DESC LIMIT 1")

    # ---- invite cache ----------------------------------------------------
    def invite_cache(self):
        return {r["code"]: int(r["uses"]) for r in self._all(f"SELECT code, uses FROM {self.p}invite_cache")}

    def invite_cache_set(self, code, uses):
        self._exec(
            f"INSERT INTO {self.p}invite_cache (code,uses) VALUES (%s,%s) "
            "ON CONFLICT (code) DO UPDATE SET uses=EXCLUDED.uses", (code, uses))

    def invite_cache_delete(self, code):
        self._exec(f"DELETE FROM {self.p}invite_cache WHERE code=%s", (code,))
