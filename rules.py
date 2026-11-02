"""팀 편성 규칙 (공고 2.3~2.5절, 3장).

DB나 화면과 상관없는 순수 파이썬 함수만 둔다. 그래서 따로 시험하기 쉽다.

학생(student)은 다음 꼴의 dict 로 다룬다.
    {"student_no": "2026101", "name": "...", "main_role": "기획자", "sub_roles": ["보고서 작성자"]}
팀 구성(teams)은 {팀 번호: [학생, ...]} 꼴의 dict 이다.
"""

from __future__ import annotations

ROLES = ["기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"]
PL = "기획자"
WP = "웹 프로그래머"
REQUIRED_ROLES = (PL, WP)
MIN_SIZE = 2
MAX_SIZE = 4

ROLE_ABBR = {
    "기획자": "PL",
    "웹 프로그래머": "WP",
    "프론트엔드 디자이너": "FD",
    "테스터": "TT",
    "보고서 작성자": "RW",
    "발표자": "PT",
}


# ---------------------------------------------------------------------------
# 역할 계산
# ---------------------------------------------------------------------------
def can_do(student: dict, role: str) -> bool:
    """주 역할이나 보조 역할로 그 역할을 맡을 수 있는지."""
    return student["main_role"] == role or role in (student.get("sub_roles") or [])


def required_filled(members: list[dict]) -> int:
    """필수 포함 역할(기획자, 웹 프로그래머)을 서로 다른 학생으로 몇 개까지 채울 수 있는지(0~2).

    한 학생이 둘 다 할 수 있어도 하나로만 센다(공고 3장 '필수 포함 역할 자리 계산').
    """
    pl = {i for i, s in enumerate(members) if can_do(s, PL)}
    wp = {i for i, s in enumerate(members) if can_do(s, WP)}
    if not pl and not wp:
        return 0
    if not pl or not wp:
        return 1
    # 둘 다 후보가 있음: 서로 다른 두 사람을 고를 수 있으면 2
    return 2 if len(pl | wp) >= 2 else 1


def missing_required(members: list[dict]) -> int:
    """아직 채우지 못한 필수 포함 역할 수."""
    return len(REQUIRED_ROLES) - required_filled(members)


def missing_required_desc(members: list[dict]) -> str:
    """채우지 못한 필수 포함 역할을 사람이 읽을 수 있게 설명."""
    has_pl = any(can_do(s, PL) for s in members)
    has_wp = any(can_do(s, WP) for s in members)
    if not has_pl and not has_wp:
        return "기획자와 웹 프로그래머"
    if not has_pl:
        return "기획자"
    if not has_wp:
        return "웹 프로그래머"
    if required_filled(members) < 2:
        return "기획자와 웹 프로그래머를 서로 다른 학생이(한 학생만 둘 다 가능)"
    return ""


def has_main_dup(members: list[dict]) -> bool:
    mains = [s["main_role"] for s in members]
    return len(mains) != len(set(mains))


# ---------------------------------------------------------------------------
# 지원·승인 때의 검사 (공고 2.4절, 3장)
# ---------------------------------------------------------------------------
def join_block_reason(members: list[dict], candidate: dict) -> str | None:
    """candidate 가 이 팀에 들어가면 규칙을 어기는지 검사. 어기면 이유, 아니면 None.

    지원할 때와 승인 직전 검사에 똑같이 쓴다.
    - 정원: 4명인 팀에는 들어갈 수 없음
    - 주 역할 중복: 같은 주 역할의 팀원이 있으면 안 됨
    - 필수 포함 역할 자리: 들어간 뒤 남는 자리 수가 아직 채우지 못한 필수 포함 역할 수보다 적으면 안 됨
    """
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m["main_role"] == candidate["main_role"] for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = members + [candidate]
    left = MAX_SIZE - len(after)
    need = missing_required(after)
    if left < need:
        return (
            f"필수 포함 역할을 맡을 학생의 자리가 모자람"
            f"(들어간 뒤 남는 자리 {left}개, 채워야 할 {missing_required_desc(after)} {need}명)"
        )
    return None


# ---------------------------------------------------------------------------
# 확정 팀 구성 검사 (공고 2.3절, 2.5절 4항)
# ---------------------------------------------------------------------------
def team_errors(team_no: int, members: list[dict]) -> list[str]:
    errs = []
    n = len(members)
    if n < MIN_SIZE or n > MAX_SIZE:
        errs.append(f"팀{team_no}의 인원이 {n}명임(2~4명이어야 함)")
    if has_main_dup(members):
        seen, dups = set(), set()
        for s in members:
            if s["main_role"] in seen:
                dups.add(s["main_role"])
            seen.add(s["main_role"])
        errs.append(f"팀{team_no}에 주 역할 {', '.join(sorted(dups))}이(가) 겹침")
    if n and missing_required(members) > 0:
        errs.append(f"팀{team_no}에 {missing_required_desc(members)}을(를) 맡을 학생이 없음")
    elif n == 0:
        errs.append(f"팀{team_no}에 기획자와 웹 프로그래머를 맡을 학생이 없음")
    return errs


def validate_teams(teams: dict[int, list[dict]]) -> list[str]:
    errs = []
    for no in sorted(teams):
        errs.extend(team_errors(no, teams[no]))
    return errs


def validate_proposal(
    teams: dict[int, list[dict]],
    unassigned: list[dict],
    assignments: list[tuple[str, int]],
) -> list[str]:
    """배정안 검사 다섯 가지(공고 2.5절 4항). 통과하면 빈 목록.

    assignments: [(학번, 팀 번호), ...]  — 팀이 없는 학생만 들어 있어야 한다.
    1. 팀이 없는 학생이 빠짐없이 배정됨
    2. 한 학생은 한 팀에만 배정됨 (이미 팀이 있는 학생을 옮기거나, 모르는 학생을 넣지 않음)
    3. 모든 팀이 2~4명
    4. 팀 안에서 주 역할 중복 없음
    5. 필수 포함 역할 충족
    """
    errs: list[str] = []
    by_no = {s["student_no"]: s for s in unassigned}
    placed_nos = {s["student_no"] for ms in teams.values() for s in ms}

    count: dict[str, int] = {}
    for no, t in assignments:
        count[no] = count.get(no, 0) + 1
    # 2. 한 학생은 한 팀에만
    for no, c in count.items():
        if c > 1:
            errs.append(f"학번 {no} 학생이 {c}개 팀에 배정됨")
        if no in placed_nos:
            errs.append(f"학번 {no} 학생은 이미 팀이 있는데 다시 배정됨")
        elif no not in by_no:
            errs.append(f"명단에 없는 학번 {no}이(가) 배정안에 있음")
    for no, t in assignments:
        if t not in teams:
            errs.append(f"없는 팀 번호 {t}에 학번 {no} 학생을 배정함")
    # 1. 빠짐없이
    missing = [s for s in unassigned if s["student_no"] not in count]
    for s in missing:
        errs.append(f"{s['name']}({s['student_no']})이(가) 배정되지 않음")
    if errs:
        return errs

    # 3~5. 배정 뒤 팀 구성 검사
    after = {no: list(ms) for no, ms in teams.items()}
    for no, t in assignments:
        after[t].append(by_no[no])
    return validate_teams(after)


# ---------------------------------------------------------------------------
# 앱이 스스로 하는 배정: 모든 경우를 따져 끝까지 찾음 (공고 3장 '3차 모델까지 실패'의 어려운 해결)
# ---------------------------------------------------------------------------
def _team_sig(members: list[dict]) -> tuple:
    """남은 학생을 넣을 수 있는지에만 영향을 주는 팀 상태 요약(실패 기억용)."""
    mains = frozenset(s["main_role"] for s in members)
    pl_only = sum(1 for s in members if can_do(s, PL) and not can_do(s, WP))
    wp_only = sum(1 for s in members if can_do(s, WP) and not can_do(s, PL))
    both = sum(1 for s in members if can_do(s, PL) and can_do(s, WP))
    return (len(members), mains, min(pl_only, 2), min(wp_only, 2), min(both, 2))


def solve(teams: dict[int, list[dict]], unassigned: list[dict]) -> list[tuple[str, int]] | None:
    """팀이 없는 학생을 규칙을 모두 지키도록 배정하는 방법을 모든 경우를 따져 찾는다.

    이미 팀에 있는 학생은 옮기지 않는다. 방법이 없으면 None.
    같은 상태에서 실패한 경우를 기억해(메모) 같은 탐색을 되풀이하지 않는다.
    """
    team_nos = sorted(teams)
    work = {no: list(teams[no]) for no in team_nos}

    # 이미 4명 넘거나 주 역할이 겹친 팀이 있으면 학생을 더 넣어도 고칠 수 없음
    for no in team_nos:
        if len(work[no]) > MAX_SIZE or has_main_dup(work[no]):
            return None

    # 넣을 수 있는 팀이 적은 학생부터 (빨리 막다른 길을 찾기 위함)
    def options(s):
        return sum(1 for no in team_nos if join_block_reason_basic(work[no], s) is None)

    order = sorted(unassigned, key=lambda s: (options(s), s["student_no"]))
    failed: set = set()
    result: list[tuple[str, int]] = []

    def feasible_now(idx: int) -> bool:
        remaining = len(order) - idx
        deficit = sum(max(0, MIN_SIZE - len(work[no])) for no in team_nos)
        if deficit > remaining:
            return False
        capacity = sum(MAX_SIZE - len(work[no]) for no in team_nos)
        if capacity < remaining:
            return False
        for no in team_nos:
            if MAX_SIZE - len(work[no]) < missing_required(work[no]):
                return False
        return True

    def rec(idx: int) -> bool:
        if not feasible_now(idx):
            return False
        if idx == len(order):
            return not validate_teams(work)
        key = (idx, tuple(sorted(_team_sig(work[no]) for no in team_nos)))
        if key in failed:
            return False
        s = order[idx]
        tried_sigs = set()
        for no in team_nos:
            if join_block_reason_basic(work[no], s) is not None:
                continue
            sig = _team_sig(work[no])
            if sig in tried_sigs:  # 상태가 똑같은 팀에 넣는 것은 한 번만 따짐
                continue
            tried_sigs.add(sig)
            work[no].append(s)
            result.append((s["student_no"], no))
            if rec(idx + 1):
                return True
            result.pop()
            work[no].pop()
        failed.add(key)
        return False

    if rec(0):
        return list(result)
    return None


def join_block_reason_basic(members: list[dict], candidate: dict) -> str | None:
    """배정 탐색용: 정원과 주 역할 중복만 본다(필수 역할은 feasible_now 에서 따로 가지치기)."""
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m["main_role"] == candidate["main_role"] for m in members):
        return "같은 주 역할의 팀원이 있음"
    return None


def infeasible_reasons(teams: dict[int, list[dict]], unassigned: list[dict]) -> list[str]:
    """규칙을 지키는 배정이 없을 때 그 이유를 찾아 설명한다(공고 2.5절 8항)."""
    reasons: list[str] = []
    team_nos = sorted(teams)
    n_un = len(unassigned)
    capacity = sum(max(0, MAX_SIZE - len(teams[no])) for no in team_nos)
    deficit = sum(max(0, MIN_SIZE - len(teams[no])) for no in team_nos)
    if n_un > capacity:
        reasons.append(f"팀이 없는 학생({n_un}명)이 팀에 남은 자리({capacity}개)보다 많음")
    if deficit > n_un:
        reasons.append(f"팀이 없는 학생이 {n_un}명뿐이라 모든 팀을 2명 이상으로 채울 수 없음(모자란 인원 {deficit}명)")
    for no in team_nos:
        ms = teams[no]
        if has_main_dup(ms):
            reasons.append(f"팀{no}에 이미 주 역할이 겹치는 팀원이 있음")
        need = missing_required(ms)
        left = MAX_SIZE - len(ms)
        if need and left < need:
            if left == 0:
                reasons.append(f"팀{no}이(가) 4명으로 다 찼는데 {missing_required_desc(ms)}을(를) 맡을 학생이 없음")
            else:
                reasons.append(f"팀{no}에 남은 자리 {left}개로 {missing_required_desc(ms)}을(를) 채울 수 없음")
            continue
        mains = {s["main_role"] for s in ms}
        for role in REQUIRED_ROLES:
            if any(can_do(s, role) for s in ms):
                continue
            cands = [s for s in unassigned if can_do(s, role) and s["main_role"] not in mains]
            if not cands:
                reasons.append(f"팀{no}에 {role}을(를) 맡을 학생이 없음(팀이 없는 학생 가운데 넣을 수 있는 {role} 후보가 없음)")
    if not reasons:
        reasons.append("모든 경우를 따져 보았지만 인원, 주 역할 중복, 필수 포함 역할을 한꺼번에 지키는 조합이 없음")
    return reasons


def role_label(student: dict) -> str:
    subs = student.get("sub_roles") or []
    return student["main_role"] + (f" (보조: {', '.join(subs)})" if subs else "")
