"""DB 연결.

- 연결은 앱이 켜질 때 한 번 만든 연결 풀(pool)을 계속 다시 쓴다(공고 4.2절).
  화면을 그릴 때마다 연결을 새로 만들지 않는다.
- 상태 읽기는 질의 1번으로 모든 테이블을 JSON으로 받아 온다(화면이 느려지지 않게).
- 바꾸는 작업(지원, 투표, 마감, 명단, 확정)은 한 트랜잭션 안에서 advisory lock 을 잡고 한다.
  그래서 두 사람이 동시에 눌러도 차례로 처리된다.
- 시험(tests/)에서는 같은 인터페이스로 sqlite 를 쓴다.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

LOCK_ID = 73_2026_11  # advisory lock 번호(이 앱 전용)
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# 상태 읽기: 질의 1번으로 모든 테이블을 가져옴(PostgreSQL)
STATE_SQL_PG = """
select
  (select coalesce(json_agg(t order by t.id), '[]'::json) from
     (select id, student_no, name, main_role, is_admin from tf_students) t) as students,
  (select coalesce(json_agg(t), '[]'::json) from
     (select student_id, role from tf_student_sub_roles) t) as sub_roles,
  (select coalesce(json_agg(t order by t.team_no), '[]'::json) from
     (select id, team_no, name from tf_teams) t) as teams,
  (select coalesce(json_agg(t order by t.team_id, t.slot_no), '[]'::json) from
     (select team_id, slot_no, student_id from tf_members) t) as members,
  (select coalesce(json_agg(t order by t.id), '[]'::json) from
     (select id, team_id, student_id, status, reason, created_at, decided_at from tf_applications) t) as applications,
  (select coalesce(json_agg(t), '[]'::json) from
     (select application_id, voter_id, vote, created_at from tf_votes) t) as votes,
  (select coalesce(json_agg(t), '[]'::json) from
     (select key, value from tf_settings) t) as settings
"""

STATE_TABLES = {
    "students": "select id, student_no, name, main_role, is_admin from tf_students order by id",
    "sub_roles": "select student_id, role from tf_student_sub_roles",
    "teams": "select id, team_no, name from tf_teams order by team_no",
    "members": "select team_id, slot_no, student_id from tf_members order by team_id, slot_no",
    "applications": "select id, team_id, student_id, status, reason, created_at, decided_at from tf_applications order by id",
    "votes": "select application_id, voter_id, vote, created_at from tf_votes",
    "settings": "select key, value from tf_settings",
}


class Session:
    """한 트랜잭션 안에서 질의를 하는 얇은 감싸개(PostgreSQL / sqlite 공통)."""

    def __init__(self, cur, kind: str):
        self.cur = cur
        self.kind = kind

    def _sql(self, sql: str) -> str:
        return sql.replace("%s", "?") if self.kind == "sqlite" else sql

    def q(self, sql: str, params=()) -> list[dict]:
        self.cur.execute(self._sql(sql), params)
        rows = self.cur.fetchall()
        if self.kind == "sqlite":
            return [dict(r) for r in rows]
        return list(rows)

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def x(self, sql: str, params=()) -> int:
        self.cur.execute(self._sql(sql), params)
        return self.cur.rowcount

    def lock(self) -> None:
        """바꾸는 작업을 한 번에 하나씩만 하게 함(트랜잭션이 끝나면 자동으로 풀림)."""
        if self.kind == "pg":
            self.cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_ID,))

    def raw_state(self) -> dict:
        if self.kind == "pg":
            self.cur.execute(STATE_SQL_PG)
            row = self.cur.fetchone()
            return {k: (row[k] or []) for k in STATE_TABLES}
        return {k: self.q(sql) for k, sql in STATE_TABLES.items()}


class PgDatabase:
    """Supabase(PostgreSQL) 연결 풀."""

    kind = "pg"

    def __init__(self, url: str):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self._dict_row = dict_row
        self.pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=5,
            kwargs={"prepare_threshold": None, "connect_timeout": 10},  # Supabase pooler 와 함께 쓰기 위함
            check=ConnectionPool.check_connection,  # 끊긴 연결을 꺼내 주지 않게 확인
            max_idle=240,
            timeout=20,
            open=True,
        )

    @contextlib.contextmanager
    def tx(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor(row_factory=self._dict_row) as cur:
                    yield Session(cur, "pg")

    def init_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.pool.connection() as conn:
            conn.execute(sql)
            conn.commit()


class SqliteDatabase:
    """시험용(tests/). 앱 배포에는 쓰지 않는다."""

    kind = "sqlite"

    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma foreign_keys = on")

    @contextlib.contextmanager
    def tx(self):
        cur = self.conn.cursor()
        cur.execute("begin immediate")
        try:
            yield Session(cur, "sqlite")
            cur.execute("commit")
        except BaseException:
            cur.execute("rollback")
            raise

    def init_schema(self) -> None:
        self.conn.executescript(Path(__file__).parent.joinpath("tests", "sqlite_schema.sql").read_text(encoding="utf-8"))
