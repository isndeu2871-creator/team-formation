"""업무 로직: 명단 올리기, 로그인, 지원, 투표, 마감, 배정안 저장, 확정.

함수는 모두 (단어, 안내) 를 돌려준다. 단어는 '승인', '대기', '거절', '막힘' 가운데 하나이고,
화면의 result 에는 "단어: 안내" 꼴로 보여 준다(공고 2.7절).
바꾸는 작업은 모두 한 트랜잭션 안에서 lock 을 잡고 한다.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import rules
import security

APPROVED, PENDING, REJECTED, BLOCKED = "승인", "대기", "거절", "막힘"
ADMIN_NO = "admin"
STUDENT_PASSWORD = "1234"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 상태 읽기
# ---------------------------------------------------------------------------
@dataclass
class State:
    students: dict[int, dict] = field(default_factory=dict)
    by_no: dict[str, dict] = field(default_factory=dict)
    teams: list[dict] = field(default_factory=list)
    team_by_no: dict[int, dict] = field(default_factory=dict)
    team_by_id: dict[int, dict] = field(default_factory=dict)
    member_team: dict[int, int] = field(default_factory=dict)  # 학생 id → 팀 번호
    applications: list[dict] = field(default_factory=list)
    settings: dict[str, str] = field(default_factory=dict)

    @property
    def closed(self) -> bool:
        return self.settings.get("closed") == "1"

    @property
    def confirmed(self) -> bool:
        return self.settings.get("confirmed") == "1"

    @property
    def proposal(self) -> dict | None:
        raw = self.settings.get("proposal")
        return json.loads(raw) if raw else None

    def members_by_team(self) -> dict[int, list[dict]]:
        return {t["team_no"]: list(t["members"]) for t in self.teams}

    def unassigned(self) -> list[dict]:
        return sorted(
            (s for s in self.students.values() if not s["is_admin"] and s["id"] not in self.member_team),
            key=lambda s: s["student_no"],
        )

    def pending(self) -> list[dict]:
        return [a for a in self.applications if a["status"] == PENDING]

    def pending_of(self, student_id: int) -> dict | None:
        for a in self.applications:
            if a["student_id"] == student_id and a["status"] == PENDING:
                return a
        return None

    def latest_app(self, student_id: int) -> dict | None:
        apps = [a for a in self.applications if a["student_id"] == student_id]
        return apps[-1] if apps else None


def build_state(raw: dict) -> State:
    st = State()
    subs: dict[int, list[str]] = {}
    for r in raw["sub_roles"]:
        subs.setdefault(r["student_id"], []).append(r["role"])
    for r in raw["students"]:
        s = {
            "id": r["id"],
            "student_no": str(r["student_no"]),
            "name": r["name"],
            "main_role": r["main_role"],
            "sub_roles": sorted(subs.get(r["id"], []), key=rules.ROLES.index),
            "is_admin": bool(r["is_admin"]),
        }
        st.students[s["id"]] = s
        st.by_no[s["student_no"]] = s
    for r in raw["teams"]:
        t = {"id": r["id"], "team_no": r["team_no"], "name": r["name"], "members": [], "slots": set()}
        st.teams.append(t)
        st.team_by_no[t["team_no"]] = t
        st.team_by_id[t["id"]] = t
    st.teams.sort(key=lambda t: t["team_no"])
    for r in sorted(raw["members"], key=lambda r: (r["team_id"], r["slot_no"])):
        t = st.team_by_id.get(r["team_id"])
        s = st.students.get(r["student_id"])
        if t is None or s is None:
            continue
        t["members"].append(s)
        t["slots"].add(r["slot_no"])
        st.member_team[s["id"]] = t["team_no"]
    votes: dict[int, dict[int, str]] = {}
    for v in raw["votes"]:
        votes.setdefault(v["application_id"], {})[v["voter_id"]] = v["vote"]
    for r in sorted(raw["applications"], key=lambda r: r["id"]):
        a = dict(r)
        t = st.team_by_id.get(a["team_id"])
        a["team_no"] = t["team_no"] if t else None
        a["votes"] = votes.get(a["id"], {})
        st.applications.append(a)
    st.settings = {r["key"]: r["value"] for r in raw["settings"]}
    return st


def load_state(database) -> State:
    with database.tx() as s:
        return build_state(s.raw_state())


def _set(s, key: str, value: str | None) -> None:
    if value is None:
        s.x("delete from tf_settings where key = %s", (key,))
    else:
        s.x(
            "insert into tf_settings (key, value) values (%s, %s) "
            "on conflict (key) do update set value = excluded.value",
            (key, value),
        )


def _free_slot(team: dict) -> int:
    for n in range(1, rules.MAX_SIZE + 1):
        if n not in team["slots"]:
            return n
    raise ValueError("빈 자리가 없음")


def _add_member(s, team: dict, student: dict) -> None:
    slot = _free_slot(team)
    s.x(
        "insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
        (team["id"], slot, student["id"]),
    )
    team["slots"].add(slot)
    team["members"].append(student)


# ---------------------------------------------------------------------------
# 관리자 계정, 로그인
# ---------------------------------------------------------------------------
def ensure_admin(database, admin_password: str) -> None:
    """앱이 시작할 때 관리자 계정을 만들거나 비밀번호를 갱신한다. 명단을 다시 올려도 남는다."""
    with database.tx() as s:
        s.lock()
        row = s.one("select id, password_hash from tf_students where student_no = %s", (ADMIN_NO,))
        if row is None:
            s.x(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                "values (%s, %s, null, %s, %s)",
                (ADMIN_NO, "관리자", security.hash_password(admin_password), True),
            )
        elif not security.check_password(admin_password, row["password_hash"]):
            s.x(
                "update tf_students set password_hash = %s, is_admin = %s where id = %s",
                (security.hash_password(admin_password), True, row["id"]),
            )


def authenticate(database, student_no: str, password: str) -> dict | None:
    student_no = (student_no or "").strip()
    if not student_no or not password:
        return None
    with database.tx() as s:
        row = s.one(
            "select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
            (student_no,),
        )
    if row is None or not security.check_password(password, row["password_hash"]):
        return None
    return {"student_no": row["student_no"], "name": row["name"], "is_admin": bool(row["is_admin"])}


# ---------------------------------------------------------------------------
# 명단 올리기 (공고 2.6절)
# ---------------------------------------------------------------------------
def parse_roster(data: bytes) -> list[dict]:
    """명단 CSV를 읽는다. BOM 이 붙은 UTF-8 도 읽도록 utf-8-sig 로 연다. 틀리면 ValueError."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV를 UTF-8로 읽을 수 없음")
    reader = csv.DictReader(io.StringIO(text))
    need = ["student_no", "name", "main_role", "sub_roles"]
    header = [h.strip() for h in (reader.fieldnames or [])]
    if header[:4] != need:
        raise ValueError(f"CSV 첫 줄이 {','.join(need)} 이어야 함(지금: {','.join(header)})")
    reader.fieldnames = header
    rows, seen = [], set()
    for i, r in enumerate(reader, start=2):
        no = (r.get("student_no") or "").strip()
        if not no and not any((v or "").strip() for v in r.values() if isinstance(v, str)):
            continue  # 빈 줄
        name = (r.get("name") or "").strip()
        main = (r.get("main_role") or "").strip()
        subs_raw = (r.get("sub_roles") or "").strip()
        if not no or not name:
            raise ValueError(f"{i}번째 줄: 학번과 이름이 있어야 함")
        if no.lower() == ADMIN_NO:
            raise ValueError(f"{i}번째 줄: 학번 admin 은 관리자용이라 쓸 수 없음")
        if no in seen:
            raise ValueError(f"{i}번째 줄: 학번 {no}이(가) 두 번 나옴")
        if main not in rules.ROLES:
            raise ValueError(f"{i}번째 줄: 주 역할 '{main}'은(는) 6개 역할에 없음")
        subs = []
        for part in subs_raw.split(";"):
            part = part.strip()
            if not part:
                continue
            if part not in rules.ROLES:
                raise ValueError(f"{i}번째 줄: 보조 역할 '{part}'은(는) 6개 역할에 없음")
            if part != main and part not in subs:
                subs.append(part)
        if len(subs) > 2:
            raise ValueError(f"{i}번째 줄: 보조 역할은 2개까지임")
        seen.add(no)
        rows.append({"student_no": no, "name": name, "main_role": main, "sub_roles": subs})
    if not rows:
        raise ValueError("CSV에 학생이 없음")
    return rows


def reset_roster(database, rows: list[dict], team_count: int) -> tuple[str, str]:
    """앱 상태를 초기화한다: 지원, 투표, 팀원, 마감 표시를 지우고 명단 학생과 빈 팀을 만든다."""
    if not (1 <= team_count <= 20):
        return BLOCKED, "팀 수는 1~20 사이여야 함"
    hashes = [security.hash_password(STUDENT_PASSWORD) for _ in rows]  # 학생 비밀번호 1234 (해시로 저장)
    with database.tx() as s:
        s.lock()
        s.x("delete from tf_votes")
        s.x("delete from tf_applications")
        s.x("delete from tf_members")
        s.x("delete from tf_teams")
        s.x("delete from tf_student_sub_roles where student_id in (select id from tf_students where not is_admin)")
        s.x("delete from tf_students where not is_admin")
        for key in ("closed", "confirmed", "proposal"):
            _set(s, key, None)
        for r, h in zip(rows, hashes):
            new = s.one(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                "values (%s, %s, %s, %s, %s) returning id",
                (r["student_no"], r["name"], r["main_role"], h, False),
            )
            for role in r["sub_roles"]:
                s.x("insert into tf_student_sub_roles (student_id, role) values (%s, %s)", (new["id"], role))
        for n in range(1, team_count + 1):
            s.x("insert into tf_teams (team_no, name) values (%s, %s)", (n, f"팀{n}"))
    return APPROVED, f"명단 {len(rows)}명과 빈 팀 {team_count}개로 처음 상태를 만듦"


# ---------------------------------------------------------------------------
# 지원 (공고 2.4절)
# ---------------------------------------------------------------------------
def apply_block_reason(st: State, me: dict, team_no: int) -> str | None:
    """지원 단추를 누른 순간의 검사. 막히면 이유."""
    if me is None or me["is_admin"]:
        return "학생 계정만 지원할 수 있음"
    if st.closed:
        return "모집이 마감됨"
    if me["id"] in st.member_team:
        return "이미 팀이 있음"
    if st.pending_of(me["id"]):
        return "결과를 기다리는 지원이 있음"
    team = st.team_by_no.get(team_no)
    if team is None:
        return f"팀{team_no}이(가) 없음"
    return rules.join_block_reason(team["members"], me)


def apply(database, student_no: str, team_no: int) -> tuple[str, str]:
    with database.tx() as s:
        s.lock()
        st = build_state(s.raw_state())
        me = st.by_no.get(student_no)
        reason = apply_block_reason(st, me, team_no)
        if reason:
            return BLOCKED, reason  # 막힌 지원은 DB에 기록하지 않음
        team = st.team_by_no[team_no]
        now = _now()
        if not team["members"]:
            _add_member(s, team, me)
            s.x(
                "insert into tf_applications (team_id, student_id, status, reason, decided_at) "
                "values (%s, %s, %s, %s, %s)",
                (team["id"], me["id"], APPROVED, "빈 팀이라 바로 승인", now),
            )
            return APPROVED, f"{team['name']}에 들어감(빈 팀이라 바로 승인)"
        s.x(
            "insert into tf_applications (team_id, student_id, status) values (%s, %s, %s)",
            (team["id"], me["id"], PENDING),
        )
        names = ", ".join(m["name"] for m in team["members"])
        return PENDING, f"{team['name']}에 지원함. 팀원 {len(team['members'])}명({names})의 투표를 기다림"


# ---------------------------------------------------------------------------
# 투표 (공고 2.4절). 승인 직전에 규칙을 다시 검사함(3장 어려운 해결)
# ---------------------------------------------------------------------------
def vote(database, voter_no: str, applicant_no: str, agree: bool) -> tuple[str, str]:
    with database.tx() as s:
        s.lock()
        st = build_state(s.raw_state())
        voter = st.by_no.get(voter_no)
        applicant = st.by_no.get(applicant_no)
        if voter is None or applicant is None or voter["is_admin"]:
            return BLOCKED, "투표할 수 없는 계정이거나 없는 지원자임"
        if st.closed:
            return BLOCKED, "모집이 마감됨"
        my_team_no = st.member_team.get(voter["id"])
        app = st.pending_of(applicant["id"])
        if app is None or app["team_no"] != my_team_no:
            last = st.latest_app(applicant["id"])
            if last and last["team_no"] == my_team_no and last["status"] != PENDING:
                return last["status"], f"{applicant['name']}의 지원은 이미 끝남({last['reason'] or ''})"
            return BLOCKED, "내 팀에 대기 중인 이 학생의 지원이 없음"
        if voter["id"] in app["votes"]:
            return PENDING, f"이미 투표함(내 투표: {app['votes'][voter['id']]})"

        team = st.team_by_no[my_team_no]
        now = _now()
        s.x(
            "insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
            (app["id"], voter["id"], "찬성" if agree else "거부"),
        )
        app["votes"][voter["id"]] = "찬성" if agree else "거부"

        if not agree:  # 한 명이라도 거부하면 바로 거절
            s.x(
                "update tf_applications set status = %s, reason = %s, decided_at = %s where id = %s",
                (REJECTED, f"팀원 {voter['name']}이(가) 거부함", now, app["id"]),
            )
            return REJECTED, f"{applicant['name']}의 지원이 거절됨(팀원 {voter['name']}이(가) 거부함)"

        # 투표할 사람은 '지금'의 팀원 모두
        waiting = [m for m in team["members"] if app["votes"].get(m["id"]) != "찬성"]
        if waiting:
            agreed = len(team["members"]) - len(waiting)
            names = ", ".join(m["name"] for m in waiting)
            return PENDING, f"{applicant['name']}의 지원: 찬성 {agreed}/{len(team['members'])}, 아직 투표하지 않은 팀원: {names}"

        # 모든 팀원이 찬성 → 승인 직전 검사(정원, 주 역할 중복, 필수 포함 역할 자리)
        reason = rules.join_block_reason(team["members"], applicant)
        if reason:
            s.x(
                "update tf_applications set status = %s, reason = %s, decided_at = %s where id = %s",
                (REJECTED, f"승인 직전 검사: {reason}", now, app["id"]),
            )
            return REJECTED, f"{applicant['name']}의 지원이 승인 직전 검사에서 거절됨({reason})"
        _add_member(s, team, applicant)
        s.x(
            "update tf_applications set status = %s, reason = %s, decided_at = %s where id = %s",
            (APPROVED, "팀원 모두 찬성", now, app["id"]),
        )
        return APPROVED, f"{applicant['name']}이(가) {team['name']}에 들어감(팀원 모두 찬성)"


# ---------------------------------------------------------------------------
# 마감 (공고 2.5절 1~2항)
# ---------------------------------------------------------------------------
def close_recruiting(database) -> tuple[str, str]:
    with database.tx() as s:
        s.lock()
        st = build_state(s.raw_state())
        if not st.teams:
            return BLOCKED, "명단을 먼저 올려야 함"
        if st.closed:
            return BLOCKED, "이미 모집을 마감함"
        now = _now()
        _set(s, "closed", "1")
        pend = st.pending()
        for a in pend:
            s.x(
                "update tf_applications set status = %s, reason = %s, decided_at = %s where id = %s",
                (REJECTED, "모집 마감", now, a["id"]),
            )
        removed = []
        for t in st.teams:
            if len(t["members"]) == 1:
                s.x("delete from tf_members where team_id = %s", (t["id"],))
                removed.append(f"{t['members'][0]['name']}({t['name']})")
        msg = f"모집을 마감함. 대기 중이던 지원 {len(pend)}건 거절"
        msg += f", 1명뿐인 팀에서 빠진 학생: {', '.join(removed)}" if removed else ", 1명뿐인 팀 없음"
        return APPROVED, msg


# ---------------------------------------------------------------------------
# 배정안 저장과 확정 (공고 2.5절 7항)
# ---------------------------------------------------------------------------
def save_proposal(database, assignments: list[tuple[str, int]], source: str) -> None:
    payload = json.dumps(
        {"assignments": [[no, t] for no, t in assignments], "source": source, "created_at": _now()},
        ensure_ascii=False,
    )
    with database.tx() as s:
        s.lock()
        _set(s, "proposal", payload)


def clear_proposal(database) -> None:
    with database.tx() as s:
        s.lock()
        _set(s, "proposal", None)


def confirm(database) -> tuple[str, str]:
    with database.tx() as s:
        s.lock()
        st = build_state(s.raw_state())
        if not st.closed:
            return BLOCKED, "모집을 마감한 뒤에 확정할 수 있음"
        if st.confirmed:
            return BLOCKED, "이미 배정을 확정함"
        prop = st.proposal
        if not prop:
            return BLOCKED, "확정할 배정안이 없음(AI 배정을 먼저 눌러야 함)"
        assignments = [(str(no), int(t)) for no, t in prop["assignments"]]
        errs = rules.validate_proposal(st.members_by_team(), st.unassigned(), assignments)
        if errs:
            _set(s, "proposal", None)
            return BLOCKED, "배정안이 지금 팀 구성과 맞지 않아 확정하지 않음: " + "; ".join(errs)
        for no, t in assignments:
            _add_member(s, st.team_by_no[t], st.by_no[no])
        final_errs = rules.validate_teams(st.members_by_team())
        if final_errs:  # 일어나면 안 됨. 트랜잭션을 되돌림
            raise RuntimeError("확정 뒤 규칙 검사 실패: " + "; ".join(final_errs))
        _set(s, "confirmed", "1")
        _set(s, "proposal", None)
        return APPROVED, f"배정을 확정함({len(assignments)}명 배정, 출처: {prop.get('source', '')})"


# ---------------------------------------------------------------------------
# AI 호출 기록
# ---------------------------------------------------------------------------
def log_ai(database, run_id: str, attempt: int, model: str, outcome: str, detail: str, elapsed: float) -> None:
    try:
        with database.tx() as s:
            s.x(
                "insert into tf_ai_logs (run_id, attempt, model, outcome, detail, elapsed_sec) "
                "values (%s, %s, %s, %s, %s, %s)",
                (run_id, attempt, model, outcome, (detail or "")[:1000], round(elapsed, 1)),
            )
    except Exception:
        pass  # 기록 실패가 배정을 막지 않게 함


def recent_ai_logs(database, limit: int = 30) -> list[dict]:
    with database.tx() as s:
        return s.q(
            "select run_id, attempt, model, outcome, detail, elapsed_sec, created_at "
            "from tf_ai_logs order by id desc limit %s",
            (limit,),
        )


def touch_keepalive(database, who: str) -> None:
    with database.tx() as s:
        _set(s, f"last_used_{who}", _now())
