"""AI 실패 처리 시험(오픈라우터를 실제로 부르지 않고 가짜 응답으로 시험).

실행: python tests/test_ai.py
- 1차: HTTP 429, 2차: 규칙을 어긴 배정안, 3차: 시간 초과 → 앱 자체 배정으로 넘어가는지
- 2차가 올바른 배정안을 내면 거기서 멈추는지
- 응답 읽기(코드 블록, 팀 번호 문자열)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ai  # noqa: E402
import rules  # noqa: E402


def s(no, main, *subs):
    return {"student_no": no, "name": f"학생{no}", "main_role": main, "sub_roles": list(subs)}


TEAMS = {1: [s("1", "기획자"), s("2", "웹 프로그래머")], 2: [s("3", "기획자")], 3: []}
UN = [s("4", "웹 프로그래머"), s("5", "테스터", "기획자"), s("6", "프론트엔드 디자이너", "웹 프로그래머")]


def run(fake, timeout=2):
    ai.TIMEOUT_SEC = timeout
    ai.call_openrouter = fake
    logs, ticks = [], []
    prop, source = ai.assign(
        TEAMS, UN, ["m1", "m2", "m3"], "key",
        on_progress=lambda i, m, el: ticks.append((i, m, round(el))),
        log=lambda i, m, o, d, el: logs.append((i, m, o, d, round(el, 1))),
    )
    return prop, source, logs, ticks


def test_all_fail_then_self():
    def fake(model, prompt, key):
        if model == "m1":
            raise ai.AIError("HTTP 429(사용량이 많다며 거절함)")
        if model == "m2":  # 팀2에 웹 프로그래머 없음, 팀3에 기획자 없음
            return '```json\n{"assignments":[{"student_no":"5","team":2},{"student_no":"4","team":3},{"student_no":"6","team":3}]}\n```'
        time.sleep(5)  # m3: 시간 초과
        return "{}"
    t0 = time.monotonic()
    prop, source, logs, ticks = run(fake)
    assert time.monotonic() - t0 < 6, "3차 모델의 시간 제한이 지켜지지 않음"
    assert [l[2] for l in logs] == ["실패", "실패", "실패", "성공"], logs
    assert "429" in logs[0][3] and "검사 실패" in logs[1][3] and "시간 초과" in logs[2][3]
    assert source.startswith("앱 자체 배정")
    assert rules.validate_proposal(TEAMS, UN, prop) == []
    assert any(t[0] == 3 for t in ticks), "몇 차 모델을 기다리는지 진행 표시가 없음"
    return logs


def test_second_ok():
    good = '{"assignments":[{"student_no":"4","team":2},{"student_no":"5","team":3},{"student_no":"6","team":3}]}'
    # 팀2: 기획자+웹프로그래머, 팀3: 테스터(보조 기획자)+FD(보조 웹 프로그래머) → 규칙 지킴
    def fake(model, prompt, key):
        if model == "m1":
            raise ai.AIError("빈 응답(content 없음)")
        return good
    prop, source, logs, _ = run(fake)
    assert source == "2차 모델 m2", source
    assert len(logs) == 2
    return logs


def test_parse():
    assert ai.parse_answer('설명\n{"assignments":[{"student_no":2026101,"team":"팀2"}]}') == [("2026101", 2)]
    for bad in ["", "   ", "배정할 수 없습니다", '{"x":1}']:
        try:
            ai.parse_answer(bad)
            raise AssertionError("틀린 응답을 받아들임: " + bad)
        except ai.AIError:
            pass


def test_no_free_router():
    assert ai.resolve_models("openrouter/free,a:free,b:free,c:free,d:free") == ["a:free", "b:free", "c:free"]
    assert len(ai.resolve_models(None)) == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            out = fn()
            print(f"== {name}: 통과")
            for line in out or []:
                print("  ", line)
