"""AI 배정 (공고 2.5절 3~6항, 4.3절).

- 오픈라우터의 텍스트 생성 모델 3개를 1차, 2차, 3차로 차례로 부른다.
- 모델 하나를 부른 때부터 응답을 다 받을 때까지 '전체 대기 시간'을 직접 재서 120초가 넘으면 끊고
  다음 모델로 넘어간다(요청 함수의 timeout 값은 응답이 시작될 때까지만 재는 경우가 많아서, 따로 잰다).
- 응답이 없음, 빈 응답, 오류(HTTP 429 등), JSON 아님, 다섯 가지 검사 실패는 모두 실패로 보고 다음 모델로 넘어간다.
- 3차 모델까지 실패하면 앱이 모든 경우를 따져 규칙을 지키는 배정을 스스로 찾는다(rules.solve).
- 무료 모델로 자동 연결하는 라우터(openrouter/free)는 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable

import requests

import rules

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
# 1차, 2차, 3차 모델. Streamlit Secrets 의 OPENROUTER_MODELS 로 바꿀 수 있다.
DEFAULT_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",  # 공고 예시이자 시연 동영상에서 검사를 통과한 모델
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder:free",
]
TIMEOUT_SEC = 120  # 모델 하나의 전체 대기 시간 한도


class AIError(Exception):
    pass


def resolve_models(configured: str | None) -> list[str]:
    models = [m.strip() for m in (configured or "").split(",") if m.strip()] or list(DEFAULT_MODELS)
    models = [m for m in models if m != "openrouter/free"]  # 자동 라우터 금지(공고 4.3절)
    return models[:3]


# ---------------------------------------------------------------------------
# 요청 내용 만들기와 응답 읽기
# ---------------------------------------------------------------------------
def _student_line(s: dict) -> str:
    subs = ", ".join(s.get("sub_roles") or []) or "없음"
    return f'- 학번 {s["student_no"]}, 이름 {s["name"]}, 주 역할 {s["main_role"]}, 보조 역할 {subs}'


def build_prompt(teams: dict[int, list[dict]], unassigned: list[dict]) -> str:
    lines = [
        "당신은 대학 팀 프로젝트의 팀 배정 도우미입니다. 아래 '팀이 없는 학생'을 기존 팀에 배정하세요.",
        "",
        "[규칙]",
        "1. 팀이 없는 학생은 빠짐없이, 한 학생은 정확히 한 팀에만 배정합니다.",
        "2. 이미 팀에 있는 학생은 옮기지 않습니다. 새 팀을 만들지 않고, 아래에 있는 팀 번호만 씁니다.",
        "3. 배정이 끝난 뒤 모든 팀은 2~4명이어야 합니다(빈 팀 없음).",
        "4. 한 팀 안에 주 역할이 같은 학생이 2명 이상이면 안 됩니다.",
        "5. 모든 팀에는 '기획자'를 맡을 학생과 '웹 프로그래머'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 합니다.",
        "   주 역할이나 보조 역할로 맡을 수 있습니다. 한 학생이 둘 다 할 수 있어도 한 역할만 맡습니다.",
        "   보조 역할은 이 5번 규칙에만 쓰고, 4번(주 역할 중복)은 주 역할만 봅니다.",
        "",
        "[지금의 팀 구성]",
    ]
    for no in sorted(teams):
        ms = teams[no]
        lines.append(f"팀 {no} ({len(ms)}명)" + (":" if ms else ": 비어 있음"))
        lines.extend(_student_line(s) for s in ms)
    lines += ["", "[팀이 없는 학생]"]
    lines.extend(_student_line(s) for s in unassigned)
    example = ", ".join(f'{{"student_no": "{s["student_no"]}", "team": 1}}' for s in unassigned[:2])
    lines += [
        "",
        "[답하는 형식]",
        "설명 없이 JSON 하나만 출력하세요. team 은 팀 번호(정수)입니다.",
        '{"assignments": [' + example + ", ...]}",
    ]
    return "\n".join(lines)


def parse_answer(text: str) -> list[tuple[str, int]]:
    """모델 응답에서 배정안을 꺼낸다. 형식이 틀리면 AIError."""
    if not text or not text.strip():
        raise AIError("빈 응답")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise AIError("응답에 JSON이 없음: " + text.strip()[:120])
    try:
        data = json.loads(t[start : end + 1])
    except json.JSONDecodeError as e:
        raise AIError(f"JSON을 읽을 수 없음({e.msg})")
    items = data.get("assignments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AIError("JSON에 assignments 목록이 없음")
    out = []
    for it in items:
        if not isinstance(it, dict) or "student_no" not in it or "team" not in it:
            raise AIError("assignments 항목 꼴이 틀림: " + str(it)[:80])
        team = it["team"]
        if isinstance(team, str):
            m = re.search(r"\d+", team)
            if not m:
                raise AIError(f"팀 번호를 읽을 수 없음: {team}")
            team = m.group()
        try:
            out.append((str(it["student_no"]).strip(), int(team)))
        except (TypeError, ValueError):
            raise AIError(f"팀 번호를 읽을 수 없음: {team}")
    return out


# ---------------------------------------------------------------------------
# 오픈라우터 호출
# ---------------------------------------------------------------------------
def call_openrouter(model: str, prompt: str, api_key: str) -> str:
    if not api_key:
        raise AIError("OPENROUTER_API_KEY 가 설정되지 않음")
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 6000,  # 추론 모델이 생각하는 데 다 쓰지 않도록 넉넉히
                "reasoning": {"effort": "low"},  # 빈 응답을 줄이기 위함(공고 4.3절)
            },
            timeout=(15, TIMEOUT_SEC + 10),
        )
    except requests.Timeout:
        raise AIError("요청 시간 초과")
    except requests.RequestException as e:
        raise AIError(f"연결 오류({type(e).__name__})")
    if resp.status_code == 429:
        raise AIError("HTTP 429(사용량이 많다며 거절함)")
    if resp.status_code != 200:
        msg = ""
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except Exception:
            pass
        raise AIError(f"HTTP {resp.status_code} {msg}"[:200])
    try:
        data = resp.json()
    except ValueError:
        raise AIError("응답이 JSON이 아님")
    if data.get("error"):
        raise AIError("오류 응답: " + str(data["error"].get("message", data["error"]))[:200])
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError("응답에 choices 가 없음")
    if not content.strip():
        raise AIError("빈 응답(content 없음)")
    return content


def run_with_deadline(fn: Callable[[], str], deadline_sec: float, on_tick: Callable[[float], None]) -> str:
    """fn 을 따로 실행하고 전체 대기 시간을 잰다. deadline_sec 이 넘으면 기다리지 않고 TimeoutError."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=target, daemon=True)
    t0 = time.monotonic()
    th.start()
    while True:
        th.join(timeout=1.0)
        elapsed = time.monotonic() - t0
        if not th.is_alive():
            break
        if elapsed >= deadline_sec:
            raise TimeoutError(f"{int(deadline_sec)}초 안에 응답을 다 받지 못함")
        on_tick(elapsed)
    if "error" in box:
        raise box["error"]
    return box["value"]


def assign(
    teams: dict[int, list[dict]],
    unassigned: list[dict],
    models: list[str],
    api_key: str,
    on_progress: Callable[[int, str, float], None],
    log: Callable[[int, str, str, str, float], None],
) -> tuple[list[tuple[str, int]] | None, str]:
    """1차 → 2차 → 3차 모델 → 앱 자체 배정 순으로 배정안을 만든다. 검사를 통과한 배정안만 돌려준다."""
    prompt = build_prompt(teams, unassigned)
    for i, model in enumerate(models, start=1):
        on_progress(i, model, 0.0)
        t0 = time.monotonic()
        try:
            text = run_with_deadline(
                lambda: call_openrouter(model, prompt, api_key),
                TIMEOUT_SEC,
                lambda el, i=i, model=model: on_progress(i, model, el),
            )
            proposal = parse_answer(text)
            errs = rules.validate_proposal(teams, unassigned, proposal)
            if errs:
                raise AIError("배정안 검사 실패: " + "; ".join(errs[:4]))
            log(i, model, "성공", "배정안이 검사 다섯 가지를 모두 통과함", time.monotonic() - t0)
            return proposal, f"{i}차 모델 {model}"
        except TimeoutError as e:
            log(i, model, "실패", f"시간 초과: {e}", time.monotonic() - t0)
        except AIError as e:
            log(i, model, "실패", str(e), time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001
            log(i, model, "실패", f"예상하지 못한 오류({type(e).__name__})", time.monotonic() - t0)

    on_progress(0, "앱 자체 배정", 0.0)
    t0 = time.monotonic()
    sol = rules.solve(teams, unassigned)
    if sol is None:
        log(0, "앱 자체 배정", "실패", "규칙을 지키는 배정이 없음", time.monotonic() - t0)
        return None, "앱 자체 배정"
    log(0, "앱 자체 배정", "성공", "3차 모델까지 실패해 앱이 모든 경우를 따져 배정함", time.monotonic() - t0)
    return sol, "앱 자체 배정(3차 모델까지 실패)"


def check_models(models: list[str]) -> dict[str, bool | None]:
    """오픈라우터 모델 목록에 그 모델이 지금 있는지(무료 호출 횟수를 쓰지 않음). 확인 못 하면 None."""
    try:
        resp = requests.get(MODELS_URL, timeout=15)
        ids = {m["id"] for m in resp.json().get("data", [])}
        return {m: (m in ids) for m in models}
    except Exception:
        return {m: None for m in models}
