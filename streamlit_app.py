"""팀 프로젝트 팀 편성 웹 앱 (Streamlit).

화면 요소의 key 는 공고 2.7절 표와 똑같이 지정한다(자동 채점 프로그램이 key 로 찾음).
- 다른 사람의 변경 보기: st.fragment(run_every=3초)로 바뀐 부분만 자동으로 다시 그림
- 비밀 값(OPENROUTER_API_KEY, DATABASE_URL, ADMIN_PASSWORD)은 Streamlit Secrets 또는 .env 에서만 읽고,
  화면에는 "있음/없음"만 보여 준다.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

import ai
import rules
import services as sv
from db import PgDatabase

load_dotenv(Path(__file__).with_name(".env"))

AUTO_REFRESH_SEC = 3  # 5초 이하(공고 2.7절)
KST = ZoneInfo("Asia/Seoul")

st.set_page_config(page_title="팀 프로젝트 팀 편성", page_icon="🧩", layout="wide")


# ---------------------------------------------------------------------------
# 설정과 DB 연결
# ---------------------------------------------------------------------------
def secret(name: str) -> str | None:
    """Streamlit Secrets → 환경 변수(.env) 순으로 읽는다. 값은 화면에 절대 출력하지 않는다."""
    value = None
    try:
        if name in st.secrets:
            value = st.secrets[name]
    except Exception:
        value = None
    if not value:
        value = os.environ.get(name)
    return str(value) if value else None


def safe_error(e: Exception) -> str:
    """오류 안내에 DB 주소나 키가 섞여 나오지 않게 지운다."""
    msg = str(e)
    msg = re.sub(r"postgres(ql)?://\S+", "[DB 주소 숨김]", msg)
    msg = re.sub(r"password\s*=\s*\S+", "password=[숨김]", msg, flags=re.I)
    msg = re.sub(r"sk-or-[A-Za-z0-9\-_]+", "[키 숨김]", msg)
    return f"{type(e).__name__}: {msg[:200]}"


@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_db() -> PgDatabase:
    """연결 풀을 앱 전체에서 한 번만 만들고 계속 다시 쓴다. 시작할 때 테이블과 관리자 계정을 준비한다."""
    database = PgDatabase(secret("DATABASE_URL"))
    try:
        database.init_schema()  # schema.sql 실행(create table if not exists)
        sv.ensure_admin(database, secret("ADMIN_PASSWORD"))  # 관리자 계정을 만들거나 비밀번호를 갱신
    except Exception:
        database.pool.close()  # 실패한 연결 풀을 닫고 다음 실행에서 다시 시도
        raise
    return database


def db_or_stop() -> PgDatabase:
    missing = [n for n in ("DATABASE_URL", "ADMIN_PASSWORD") if not secret(n)]
    if missing:
        st.error("설정이 없음: " + ", ".join(missing) + " (Streamlit Secrets 또는 .env 에 넣어야 함)")
        st.stop()
    try:
        return get_db()
    except Exception as e:  # noqa: BLE001
        st.error("DB에 연결하지 못함. Supabase 프로젝트가 일시정지되었는지, Secrets의 DATABASE_URL 이 맞는지 확인해야 함.")
        st.caption(safe_error(e))
        st.stop()


def to_kst(value) -> str:
    if not value:
        return ""
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def md_escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]$~#|<>])", r"\\\1", str(text))


def roles_text(s: dict) -> str:
    subs = ", ".join(s.get("sub_roles") or [])
    return f"주 역할 **{s['main_role']}**" + (f" · 보조 역할 {subs}" if subs else " · 보조 역할 없음")


# ---------------------------------------------------------------------------
# 결과 안내 (key = result). "승인/대기/거절/막힘: 안내" 꼴
# ---------------------------------------------------------------------------
WORD_COLOR = {"승인": "green", "대기": "blue", "거절": "orange", "막힘": "red"}


def set_result(word: str, message: str) -> None:
    st.session_state["result"] = (word, message)


def render_result() -> None:
    with st.container(key="result"):
        res = st.session_state.get("result")
        if res:
            word, message = res
            st.markdown(f":{WORD_COLOR.get(word, 'gray')}[**{word}**]: {md_escape(message)}")


def run_action(fn, *args) -> None:
    """단추 콜백 공통: 업무 로직을 부르고 결과를 result 에 남긴다."""
    try:
        word, message = fn(*args)
    except Exception as e:  # noqa: BLE001
        word, message = "막힘", "처리하는 중에 오류가 남(다시 눌러 보아야 함): " + safe_error(e)
    set_result(word, message)


# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------
def login_view(database) -> None:
    st.title("🧩 팀 프로젝트 팀 편성")
    st.caption("학번과 비밀번호로 로그인합니다. 관리자는 학번 admin 으로 로그인합니다.")
    with st.form("login_form"):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit", type="primary")
    if submitted:
        user = sv.authenticate(database, st.session_state.get("login_student_no", ""), st.session_state.get("login_password", ""))
        if user:
            st.session_state["user_no"] = user["student_no"]
            st.session_state.pop("result", None)
            st.rerun()
        set_result("막힘", "학번이나 비밀번호가 틀림")
    render_result()


def logout() -> None:
    for k in ("user_no", "result", "assign_note", "login_student_no", "login_password"):
        st.session_state.pop(k, None)


def whoami(user: dict) -> None:
    with st.container(key="whoami", border=True):
        c1, c2 = st.columns([5, 1])
        if user["is_admin"]:
            c1.markdown(f"👤 **{user['name']}** (admin) 로그인 중")
        else:
            c1.markdown(f"👤 **{md_escape(user['name'])}** ({user['student_no']}) 로그인 중 · {roles_text(user)}")
        c2.button("로그아웃", key="logout", on_click=logout)


# ---------------------------------------------------------------------------
# 학생 화면 (공고 2.8절). 3초마다 바뀐 부분만 다시 그림
# ---------------------------------------------------------------------------
def refresh_notice() -> None:
    """다른 사람의 변경 보기(어려운 해결)를 쓴다는 안내. 시범 앱처럼 화면 위쪽에 보여 준다."""
    st.caption(f"🔄 화면은 {AUTO_REFRESH_SEC}초마다 저절로 바뀜(다른 사람의 지원과 투표가 자동으로 보임) · 마지막 갱신 {datetime.now(KST):%H:%M:%S}")


def app_rows(state, apps: list[dict]) -> list[dict]:
    """지원 기록 표(최근 순). 시각은 한국 시각으로 바꿔 보여 준다."""
    rows = []
    for a in reversed(apps):
        s = state.students.get(a["student_id"], {})
        agree = sum(1 for v in a["votes"].values() if v == "찬성")
        rows.append({"지원자": s.get("name", ""), "팀": f"팀{a['team_no']}", "상태": a["status"],
                     "이유": a.get("reason") or "", "찬성": agree,
                     "지원 시각": to_kst(a.get("created_at")), "결과 시각": to_kst(a.get("decided_at"))})
    return rows


def member_line(m: dict) -> str:
    return f"- {md_escape(m['name'])} — {roles_text(m)}"


@st.fragment(run_every=AUTO_REFRESH_SEC)
def student_panel(user_no: str) -> None:
    database = get_db()
    state = sv.load_state(database)
    me = state.by_no.get(user_no)
    if me is None:
        st.warning("명단이 바뀌어 이 계정이 없어짐. 로그아웃한 뒤 다시 로그인해야 함.")
        return
    render_result()
    refresh_notice()

    my_team_no = state.member_team.get(me["id"])
    my_pending = state.pending_of(me["id"])
    if state.closed:
        st.info("모집이 마감되었습니다. 지원과 투표를 할 수 없습니다." + (" (배정 확정됨)" if state.confirmed else ""))
    if my_team_no:
        st.success(f"내 팀: 팀{my_team_no}")
    elif my_pending:
        st.info(f"팀{my_pending['team_no']}에 지원하고 결과를 기다리는 중입니다.")
    else:
        st.write("아직 팀이 없음")
    mine_apps = [a for a in state.applications if a["student_id"] == me["id"]]

    can_apply_now = not state.closed and not my_team_no and not my_pending  # 2.8절: 이 경우 지원 단추 비활성화
    st.subheader("팀 목록")
    cols = st.columns(min(4, max(1, len(state.teams))))
    for i, team in enumerate(state.teams):
        n = team["team_no"]
        with cols[i % len(cols)]:
            with st.container(key=f"team_{n}", border=True):
                mine = " · ⭐ 내 팀" if n == my_team_no else ""
                st.markdown(f"#### {team['name']}{mine}  \n{len(team['members'])}/4명")
                if team["members"]:
                    st.markdown("\n".join(member_line(m) for m in team["members"]))
                else:
                    st.caption("아직 팀원이 없음")
            st.button(
                f"{team['name']}에 지원",
                key=f"apply_{n}",
                disabled=not can_apply_now,
                on_click=run_action,
                args=(sv.apply, database, user_no, n),
            )

    if my_team_no:
        st.subheader("내 팀에 들어온 지원")
        apps = [a for a in state.pending() if a["team_no"] == my_team_no]
        if not apps:
            st.caption("대기 중인 지원이 없음")
        team = state.team_by_no[my_team_no]
        for a in apps:
            applicant = state.students[a["student_id"]]
            with st.container(border=True):
                st.markdown(f"**{md_escape(applicant['name'])}** ({applicant['student_no']}) — {roles_text(applicant)}")
                status = []
                for m in team["members"]:
                    v = a["votes"].get(m["id"])
                    status.append(f"{md_escape(m['name'])}: {v or '아직 안 함'}")
                st.caption("팀원 투표: " + " · ".join(status))
                my_vote = a["votes"].get(me["id"])
                if my_vote:
                    st.write(f"내 투표: {my_vote}")
                elif not state.closed:
                    c1, c2 = st.columns(2)
                    c1.button(
                        "찬성",
                        key=f"vote_yes_{applicant['student_no']}",
                        on_click=run_action,
                        args=(sv.vote, database, user_no, applicant["student_no"], True),
                        type="primary",
                    )
                    c2.button(
                        "거부",
                        key=f"vote_no_{applicant['student_no']}",
                        on_click=run_action,
                        args=(sv.vote, database, user_no, applicant["student_no"], False),
                    )
    if mine_apps:
        st.subheader("내 지원 기록")
        st.dataframe(app_rows(state, mine_apps), hide_index=True)


# ---------------------------------------------------------------------------
# 관리자 화면
# ---------------------------------------------------------------------------
def admin_guard(database) -> bool:
    """관리자 단추를 누를 때마다 DB에서 관리자인지 다시 확인한다(학생이 관리자 기능을 쓰지 못하게)."""
    user_no = st.session_state.get("user_no")
    state = sv.load_state(database)
    me = state.by_no.get(user_no or "")
    if not me or not me["is_admin"]:
        set_result("막힘", "관리자만 할 수 있음")
        return False
    return True


def do_start() -> None:
    database = get_db()
    if not admin_guard(database):
        return
    up = st.session_state.get("admin_roster_file")
    if up is None:
        set_result("막힘", "명단 CSV 파일을 먼저 올려야 함")
        return
    try:
        rows = sv.parse_roster(up.getvalue())
    except ValueError as e:
        set_result("막힘", f"CSV를 읽을 수 없음: {e}")
        return
    count = int(st.session_state.get("admin_team_count") or 0)
    st.session_state.pop("assign_note", None)
    run_action(sv.reset_roster, database, rows, count)


def do_close() -> None:
    database = get_db()
    if admin_guard(database):
        run_action(sv.close_recruiting, database)


def do_confirm() -> None:
    database = get_db()
    if admin_guard(database):
        run_action(sv.confirm, database)
        st.session_state.pop("assign_note", None)


def run_assignment(database, status_box) -> None:
    """AI 배정: 1차 → 2차 → 3차 모델 → 앱 자체 배정. 진행 상황을 assign_status 안에 보여 준다."""
    if not admin_guard(database):
        return
    state = sv.load_state(database)
    if not state.teams:
        set_result("막힘", "명단을 먼저 올려야 함")
        return
    if not state.closed:
        set_result("막힘", "모집을 마감한 뒤에 배정할 수 있음")
        return
    if state.confirmed:
        set_result("막힘", "이미 배정을 확정함")
        return
    teams, unassigned = state.members_by_team(), state.unassigned()
    sv.clear_proposal(database)

    # 규칙을 지키는 배정이 아예 없으면 AI를 부르지 않고 이유를 보여 줌(2.5절 8항)
    if rules.solve(teams, unassigned) is None:
        reasons = rules.infeasible_reasons(teams, unassigned)
        st.session_state["assign_note"] = ("none", reasons)
        set_result("막힘", "규칙을 지키는 배정이 없음: " + "; ".join(reasons))
        return
    if not unassigned:
        sv.save_proposal(database, [], "배정할 학생 없음")
        st.session_state["assign_note"] = ("ok", ["팀이 없는 학생이 없고, 지금 팀 구성이 규칙을 모두 지킴"])
        set_result("승인", "배정할 학생이 없음. 확정을 누르면 지금 팀 구성으로 확정함")
        return

    run_id = uuid.uuid4().hex[:8]
    if not st.session_state.get("use_ai", True):  # 관리자가 'AI를 부름'을 끈 경우(시험할 때 무료 호출 횟수를 아끼기 위함)
        sol = rules.solve(teams, unassigned)
        sv.log_ai(database, run_id, 0, "앱 자체 배정", "성공", "관리자가 AI를 끄고 배정함", 0.0)
        sv.save_proposal(database, sol, "앱 자체 배정(AI를 끔)")
        st.session_state["assign_note"] = ("ok", ["- AI를 부르지 않고 앱이 모든 경우를 따져 배정함"])
        set_result("승인", "배정안을 만듦(출처: 앱 자체 배정, AI를 끔). 확인한 뒤 '배정안 확정'을 누름")
        return
    models = ai.resolve_models(secret("OPENROUTER_MODELS"))
    lines: list[str] = []
    with status_box:
        progress = st.empty()
        history = st.empty()

    def on_progress(attempt: int, model: str, elapsed: float) -> None:
        if attempt == 0:
            progress.warning("⏳ 3차 모델까지 실패해 앱이 모든 경우를 따져 스스로 배정하는 중…")
        else:
            progress.info(f"⏳ {attempt}차 모델({model})의 응답을 기다리는 중… {int(elapsed)}초 / {ai.TIMEOUT_SEC}초")

    def log(attempt: int, model: str, outcome: str, detail: str, elapsed: float) -> None:
        sv.log_ai(database, run_id, attempt, model, outcome, detail, elapsed)
        label = f"{attempt}차 모델 {model}" if attempt else "앱 자체 배정"
        lines.append(f"- {label}: **{outcome}** ({elapsed:.1f}초) — {md_escape(detail)}")
        history.markdown("\n".join(lines))

    proposal, source = ai.assign(teams, unassigned, models, secret("OPENROUTER_API_KEY") or "", on_progress, log)
    progress.empty()
    if proposal is None:  # solve 가 위에서 성공했으므로 일어나지 않아야 함
        st.session_state["assign_note"] = ("none", ["규칙을 지키는 배정을 찾지 못함"])
        set_result("막힘", "규칙을 지키는 배정이 없음")
        return
    sv.save_proposal(database, proposal, source)
    st.session_state["assign_note"] = ("ok", lines)
    set_result("승인", f"배정안을 만듦(출처: {source}). 확인한 뒤 '배정안 확정'을 누름")


def render_proposal(state) -> None:
    prop = state.proposal
    if not prop:
        return
    st.markdown(f"**배정안** (출처: {md_escape(prop.get('source', ''))}) — 검사 다섯 가지를 모두 통과한 배정안입니다.")
    rows = []
    for no, t in prop["assignments"]:
        s = state.by_no.get(str(no))
        if s:
            rows.append(
                {"이름": s["name"], "학번": s["student_no"], "주 역할": s["main_role"],
                 "보조 역할": ", ".join(s["sub_roles"]) or "없음", "배정된 팀": f"팀{t}"}
            )
    if rows:
        st.dataframe(rows, hide_index=True)
    preview = state.members_by_team()
    for no, t in prop["assignments"]:
        if str(no) in state.by_no and int(t) in preview:
            preview[int(t)].append(state.by_no[str(no)])
    st.caption("확정하면 이렇게 됩니다: " + " / ".join(
        f"팀{n}: " + ", ".join(m["name"] for m in ms) for n, ms in sorted(preview.items())
    ))


@st.fragment(run_every=AUTO_REFRESH_SEC)
def admin_live_panel() -> None:
    state = sv.load_state(get_db())
    flags = ["모집 마감됨" if state.closed else "모집 중", "배정 확정됨" if state.confirmed else "확정 전"]
    st.subheader("현황 · " + " · ".join(flags))
    refresh_notice()
    cols = st.columns(min(4, max(1, len(state.teams))))
    for i, team in enumerate(state.teams):
        with cols[i % len(cols)]:
            with st.container(key=f"team_{team['team_no']}", border=True):
                st.markdown(f"#### {team['name']}  \n{len(team['members'])}/4명")
                if team["members"]:
                    st.markdown("\n".join(member_line(m) for m in team["members"]))
                else:
                    st.caption("아직 팀원이 없음")
                errs = rules.team_errors(team["team_no"], team["members"]) if state.confirmed else []
                for e in errs:
                    st.error(e)
    with st.container(key="unassigned", border=True):
        un = state.unassigned()
        st.markdown(f"**팀이 없는 학생 ({len(un)}명)**")
        if un:
            st.markdown("\n".join(f"- {md_escape(s['name'])} — {roles_text(s)}" for s in un))
        else:
            st.caption("없음")
    if state.applications:
        st.markdown("**지원 기록** (최근 순, 한국 시각)")
        st.dataframe(app_rows(state, state.applications), hide_index=True)


def admin_view(database) -> None:
    render_result()
    state = sv.load_state(database)

    st.subheader("1. 명단으로 처음 상태 만들기")
    c1, c2, c3 = st.columns([3, 1, 2])
    with c1:
        st.file_uploader("명단 CSV (UTF-8, BOM 있어도 됨)", type=["csv"], key="admin_roster_file")
    with c2:
        st.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    with c3:
        st.write("")
        st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=do_start, type="primary")
        st.caption("지원, 투표, 팀원, 마감 표시를 모두 지우고 새로 시작합니다.")

    st.subheader("2. 모집 마감")
    st.button("모집 마감", key="admin_close", on_click=do_close)

    st.subheader("3. AI 배정과 확정")
    st.checkbox("AI를 부름 (끄면 AI 없이 앱이 바로 배정함 · 무료 호출 횟수를 아낄 때만 끔)", value=True, key="use_ai")
    assign_clicked = st.button("AI 배정", key="admin_assign", type="primary")
    status_box = st.container(key="assign_status", border=True)
    if assign_clicked:
        run_assignment(database, status_box)
        st.rerun()
    with status_box:
        note = st.session_state.get("assign_note")
        if note and note[0] == "none":
            st.error("규칙을 지키는 배정이 없음")
            st.markdown("\n".join(f"- {md_escape(r)}" for r in note[1]))
        elif note and note[0] == "ok":
            st.markdown("\n".join(note[1]))
        if state.proposal and not state.confirmed:
            render_proposal(state)
        elif state.confirmed:
            st.success("배정이 확정되었습니다.")
        elif not note:
            st.caption("모집을 마감한 뒤 'AI 배정'을 누르면 1차 → 2차 → 3차 모델 순으로 배정안을 받고, "
                       "모두 실패하면 앱이 스스로 배정합니다.")
    if state.proposal and not state.confirmed:
        st.button("배정안 확정", key="admin_confirm", on_click=do_confirm, type="primary")

    st.divider()
    admin_live_panel()

    st.divider()
    st.subheader("AI 호출 기록 (최근 30건, 한국 시각)")
    try:
        logs = sv.recent_ai_logs(database)
        if logs:
            st.dataframe(
                [{"시각": to_kst(r["created_at"]), "배정 번호": r["run_id"],
                  "차수": f"{r['attempt']}차" if r["attempt"] else "앱", "모델": r["model"],
                  "결과": r["outcome"], "걸린 초": float(r["elapsed_sec"] or 0), "내용": r["detail"]} for r in logs],
                hide_index=True,
            )
        else:
            st.caption("아직 기록이 없음")
    except Exception as e:  # noqa: BLE001
        st.caption("기록을 읽지 못함: " + safe_error(e))

    st.subheader("설정 확인")
    models = ai.resolve_models(secret("OPENROUTER_MODELS"))
    st.markdown(
        "\n".join(
            [f"- {n}: {'있음' if secret(n) else '없음'}" for n in ("OPENROUTER_API_KEY", "DATABASE_URL", "ADMIN_PASSWORD")]
            + [f"- {i}차 모델: `{m}`" for i, m in enumerate(models, start=1)]
            + [f"- 외부 예약 서비스(GitHub Actions)의 마지막 DB 질의: {to_kst(state.settings.get('keepalive_at')) or '아직 없음'}"]
        )
    )
    if st.button("모델이 오픈라우터에 지금 있는지 확인(무료 호출 횟수를 쓰지 않음)", key="check_models"):
        res = ai.check_models(models)
        for m, ok in res.items():
            st.write(f"{m}: " + ("있음" if ok else ("없음 — 다른 모델로 바꿔야 함" if ok is False else "확인 못 함")))


# ---------------------------------------------------------------------------
# 시작
# ---------------------------------------------------------------------------
def main() -> None:
    database = db_or_stop()
    user_no = st.session_state.get("user_no")
    if not user_no:
        login_view(database)
        return
    state = sv.load_state(database)
    user = state.by_no.get(user_no)
    if user is None:
        logout()
        st.warning("명단이 바뀌어 이 계정이 없어짐. 다시 로그인해야 함.")
        login_view(database)
        return
    st.title("🧩 팀 프로젝트 팀 편성" + (" · 관리자" if user["is_admin"] else ""))
    whoami(user)
    if user["is_admin"]:
        admin_view(database)
    else:
        student_panel(user_no)


main()
