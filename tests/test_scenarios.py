"""연습용 시나리오(공고 부록 1~3)를 업무 로직으로 그대로 따라 해 보는 시험.

실행: python tests/test_scenarios.py   (DB 대신 sqlite 메모리 DB를 씀)
화면 시험은 배포한 앱에서 따로 한다. 이 파일은 규칙과 업무 로직이 공고의 기대 결과와 같은지만 본다.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rules  # noqa: E402
import security  # noqa: E402
import services as sv  # noqa: E402
from db import SqliteDatabase  # noqa: E402

# 시험에서는 bcrypt 대신 빠른 가짜 해시를 씀(앱은 bcrypt)
security.hash_password = lambda p: "test$" + hashlib.sha256(p.encode()).hexdigest()
security.check_password = lambda p, h: h == "test$" + hashlib.sha256(p.encode()).hexdigest()

DATA = ROOT / "tests" / "data"


def fresh(csv_name: str, teams: int) -> SqliteDatabase:
    db = SqliteDatabase()
    db.init_schema()
    sv.ensure_admin(db, "admin-test-pw")
    rows = sv.parse_roster((DATA / csv_name).read_bytes())
    word, _ = sv.reset_roster(db, rows, teams)
    assert word == "승인"
    return db


def no_of(db, n: int, prefix: str) -> str:
    return f"{prefix}{n:02d}"


class Runner:
    def __init__(self, db, prefix):
        self.db, self.prefix, self.log = db, prefix, []

    def no(self, n):
        return f"{self.prefix}{n:02d}"

    def apply(self, n, team, expect):
        w, m = sv.apply(self.db, self.no(n), team)
        self.check(f"학생{n} → 팀{team} 지원", w, m, expect)

    def vote(self, voter, applicant, agree, expect):
        w, m = sv.vote(self.db, self.no(voter), self.no(applicant), agree)
        self.check(f"학생{voter} → 학생{applicant} {'찬성' if agree else '거부'}", w, m, expect)

    def close(self, expect="승인"):
        w, m = sv.close_recruiting(self.db)
        self.check("관리자 마감", w, m, expect)

    def assign_and_confirm(self):
        st = sv.load_state(self.db)
        teams, un = st.members_by_team(), st.unassigned()
        sol = rules.solve(teams, un)
        assert sol is not None, rules.infeasible_reasons(teams, un)
        assert rules.validate_proposal(teams, un, sol) == []
        sv.save_proposal(self.db, sol, "앱 자체 배정(시험)")
        w, m = sv.confirm(self.db)
        self.check("관리자 확정", w, m, "승인")
        st = sv.load_state(self.db)
        assert not st.unassigned(), "남은 학생이 있음"
        assert rules.validate_teams(st.members_by_team()) == [], rules.validate_teams(st.members_by_team())
        for t in st.teams:
            self.log.append(f"   {t['name']}: " + ", ".join(m["name"] for m in t["members"]))

    def check(self, what, w, m, expect):
        ok = w == expect
        self.log.append(f"{'OK ' if ok else 'XX '} {what}: {w}: {m}  (기대: {expect})")
        assert ok, "\n".join(self.log)


def test_easy():
    r = Runner(fresh("쉬움.csv", 3), "20261")
    r.apply(1, 1, "승인"); r.apply(2, 2, "승인"); r.apply(3, 3, "승인")           # 1
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")                               # 2
    r.apply(5, 2, "대기"); r.vote(2, 5, True, "승인")                               # 3
    r.apply(7, 1, "대기"); r.vote(1, 7, True, "대기"); r.vote(4, 7, False, "거절")  # 4
    r.apply(6, 3, "대기"); r.vote(3, 6, True, "승인")                               # 5
    r.close(); r.assign_and_confirm()                                               # 6
    return r.log


def test_medium():
    r = Runner(fresh("중간.csv", 4), "20262")
    r.apply(1, 1, "승인")                                                          # 1
    r.apply(5, 1, "대기"); r.vote(1, 5, True, "승인")                               # 2
    r.apply(2, 1, "막힘")                                                          # 3 (주 역할 중복, 뒤 투표 건너뜀)
    r.apply(6, 2, "승인")                                                          # 4
    r.apply(9, 2, "대기"); r.vote(6, 9, True, "승인")                               # 5
    r.apply(10, 2, "대기"); r.vote(6, 10, True, "대기"); r.vote(9, 10, False, "거절")  # 6
    r.apply(3, 3, "승인"); r.apply(11, 4, "승인")                                   # 7
    r.apply(12, 4, "대기"); r.apply(12, 3, "막힘")                                  # 8
    r.close()                                                                      # 9
    st = sv.load_state(r.db)
    assert st.latest_app(st.by_no[r.no(12)]["id"])["status"] == "거절"
    un = {s["name"] for s in st.unassigned()}
    assert {"학생3(PL;RW)", "학생11(RW)"} <= un, un
    r.log.append(f"   마감 뒤 팀이 없는 학생: {sorted(un)}")
    r.apply(2, 3, "막힘")                                                          # 10 (마감)
    r.assign_and_confirm()                                                         # 11
    return r.log


def test_hard():
    r = Runner(fresh("어려움.csv", 4), "20263")
    r.apply(1, 1, "승인")                                                          # 1
    r.apply(4, 1, "대기"); r.vote(1, 4, True, "승인")                               # 2
    r.apply(7, 1, "대기"); r.vote(1, 7, True, "대기"); r.vote(4, 7, True, "승인")   # 3
    r.apply(2, 2, "승인")                                                          # 4
    r.apply(8, 2, "대기"); r.vote(2, 8, True, "승인")                               # 5
    r.apply(5, 2, "대기"); r.apply(6, 2, "대기")                                    # 6
    r.vote(2, 5, True, "대기"); r.vote(8, 5, True, "승인")                          # 7
    r.vote(2, 6, True, "대기"); r.vote(8, 6, True, "대기"); r.vote(5, 6, True, "거절")  # 8 (승인 직전 검사)
    r.apply(11, 3, "승인")                                                         # 9
    r.apply(15, 3, "대기"); r.vote(11, 15, True, "승인")                            # 10
    r.apply(14, 3, "막힘")                                                         # 11 (필수 포함 역할 자리)
    r.apply(10, 4, "승인")                                                         # 12
    r.apply(13, 1, "대기"); r.vote(1, 13, True, "대기")                             # 13
    r.close()                                                                      # 14
    st = sv.load_state(r.db)
    assert st.latest_app(st.by_no[r.no(13)]["id"])["status"] == "거절"
    assert "학생10(TT;RW)" in {s["name"] for s in st.unassigned()}
    r.assign_and_confirm()                                                         # 15
    return r.log


def test_rules_examples():
    """공고 3장의 '필수 포함 역할 자리 계산' 예."""
    def s(main, *subs):
        return {"student_no": main, "name": main, "main_role": main, "sub_roles": list(subs)}
    # 기획자(보조 웹 프로그래머) 혼자인 팀은 필수 역할 2개 중 1개만 채운 것
    assert rules.missing_required([s("기획자", "웹 프로그래머")]) == 1
    # 테스터+발표자 팀에 보고서 작성자가 지원하면 막힘
    assert rules.join_block_reason([s("테스터"), s("발표자")], s("보고서 작성자")) is not None
    # 테스터+발표자 팀에 웹 프로그래머는 들어갈 수 있음(남는 자리 1개 = 남은 필수 역할 1개)
    assert rules.join_block_reason([s("테스터"), s("발표자")], s("웹 프로그래머")) is None


def test_no_valid_assignment():
    """규칙을 지키는 배정이 없을 때 이유를 찾는지."""
    def s(no, main, *subs):
        return {"student_no": no, "name": no, "main_role": main, "sub_roles": list(subs)}
    teams = {
        1: [s("a", "기획자"), s("b", "웹 프로그래머")],
        2: [s("c", "테스터"), s("d", "발표자"), s("e", "보고서 작성자"), s("f", "프론트엔드 디자이너")],
    }
    assert rules.solve(teams, []) is None
    reasons = rules.infeasible_reasons(teams, [])
    assert any("팀2" in x and "다 찼" in x for x in reasons), reasons


def test_db_constraints():
    """DB 유일성 제약: 한 학생 두 팀 소속 불가, 같은 자리 두 명 불가, 대기 지원 2개 불가."""
    import sqlite3
    db = fresh("쉬움.csv", 3)
    sv.apply(db, "2026101", 1)
    for sql in [
        "insert into tf_members (team_id, slot_no, student_id) values (2, 1, (select id from tf_students where student_no='2026101'))",
        "insert into tf_members (team_id, slot_no, student_id) values (1, 1, (select id from tf_students where student_no='2026102'))",
        "insert into tf_members (team_id, slot_no, student_id) values (1, 5, (select id from tf_students where student_no='2026102'))",
    ]:
        try:
            db.conn.execute(sql)
            raise AssertionError("제약이 막지 못함: " + sql)
        except sqlite3.IntegrityError:
            pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            out = fn()
            print(f"== {name}: 통과")
            for line in out or []:
                print("  ", line)
