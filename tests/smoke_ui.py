"""화면 코드 연기 시험(smoke test): Streamlit 흉내 모듈로 streamlit_app.py 를 실행해
- 오류 없이 도는지
- 공고 2.7절 표의 key 가 화면마다 빠짐없이 만들어지는지
- 단추 콜백(지원, 투표, 명단, 마감, 확정)이 result 를 "승인/대기/거절/막힘: …" 꼴로 남기는지
를 확인한다. 실제 화면 시험은 배포한 앱에서 한다.

실행: python tests/smoke_ui.py
"""

from __future__ import annotations

import hashlib
import os
import runpy
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import security  # noqa: E402

security.hash_password = lambda p: "test$" + hashlib.sha256(p.encode()).hexdigest()
security.check_password = lambda p, h: h == "test$" + hashlib.sha256(p.encode()).hexdigest()


class Stop(Exception):
    pass


class Rerun(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class Recorder:
    """화면 요소 흉내. key 와 보이는 글자를 기록한다."""

    def __init__(self, app, key=None):
        self.app, self.key = app, key

    def __enter__(self):
        self.app.stack.append(self.key)
        return self

    def __exit__(self, *a):
        self.app.stack.pop()

    def __getattr__(self, name):
        return getattr(self.app.mod, name)


class FakeApp:
    def __init__(self):
        self.session = SessionState()
        self.reset()
        self.mod = self.build()

    def reset(self):
        self.keys = {}  # key -> dict(kind, disabled, texts)
        self.stack = []
        self.texts = []

    def text(self, t):
        self.texts.append(str(t))
        for k in self.stack:
            if k:
                self.keys[k]["texts"].append(str(t))

    def reg(self, kind, key, **kw):
        if key is None:
            return
        assert key not in self.keys, f"key 가 겹침: {key}"
        self.keys[key] = {"kind": kind, "texts": [], **kw}

    def build(self):
        app = self
        m = types.ModuleType("streamlit")

        class Secrets:
            def __contains__(self, k):
                raise FileNotFoundError("no secrets")

        m.secrets = Secrets()
        m.session_state = app.session
        m.set_page_config = lambda **k: None

        def cache_resource(*a, **k):
            def deco(fn):
                box = {}

                def wrapper():
                    if "v" not in box:
                        box["v"] = fn()
                    return box["v"]
                return wrapper
            return deco if not (a and callable(a[0])) else deco(a[0])
        m.cache_resource = cache_resource

        def fragment(*a, run_every=None, **k):
            assert run_every is None or run_every <= 5, "자동 갱신 간격이 5초를 넘음"
            def deco(fn):
                return fn
            return deco
        m.fragment = fragment

        for name in ("error", "warning", "info", "success", "caption", "markdown", "write", "title", "subheader"):
            setattr(m, name, lambda t="", *a, **k: app.text(t))
        m.divider = lambda *a, **k: None
        m.dataframe = lambda data, *a, **k: [app.text(str(r)) for r in data]

        def container(key=None, border=None, **k):
            app.reg("container", key)
            return Recorder(app, key)
        m.container = container

        def columns(spec, **k):
            n = spec if isinstance(spec, int) else len(spec)
            return [Recorder(app) for _ in range(n)]
        m.columns = columns
        m.form = lambda name, **k: Recorder(app)

        class Empty:
            def __getattr__(self, name):
                return lambda *a, **k: app.text(a[0] if a else "")
        m.empty = lambda: Empty()

        def text_input(label, key=None, type=None, **k):
            app.reg("text_input", key)
            return app.session.get(key, "")
        m.text_input = text_input

        def form_submit_button(label, key=None, **k):
            app.reg("button", key)
            return app.session.get("_click") == key and app.session.pop("_click") == key
        m.form_submit_button = form_submit_button

        def button(label, key=None, disabled=False, on_click=None, args=(), kwargs=None, **k):
            app.reg("button", key, disabled=disabled, on_click=on_click, args=args)
            return app.session.get("_click") == key and app.session.pop("_click") == key
        m.button = button

        def checkbox(label, value=False, key=None, **k):
            app.reg("checkbox", key)
            app.session.setdefault(key, value)
            return app.session[key]
        m.checkbox = checkbox

        def file_uploader(label, key=None, **k):
            app.reg("file_uploader", key)
            return app.session.get(key)
        m.file_uploader = file_uploader

        def number_input(label, key=None, value=None, **k):
            app.reg("number_input", key)
            app.session.setdefault(key, value)
            return app.session[key]
        m.number_input = number_input

        def stop():
            raise Stop()
        m.stop = stop

        def rerun(*a, **k):
            raise Rerun()
        m.rerun = rerun
        return m

    def run(self):
        """스크립트를 한 번 실행(st.rerun 이 나오면 다시 실행)."""
        for _ in range(3):
            self.reset()
            sys.modules["streamlit"] = self.mod
            try:
                self.g = runpy.run_path(str(ROOT / "streamlit_app.py"), run_name="__main__")
                return self
            except Stop:
                return self
            except Rerun:
                continue
        return self

    def click(self, key):
        """콜백 단추를 누름: 콜백을 먼저 부르고 다시 실행(Streamlit 과 같은 순서)."""
        info = self.keys.get(key)
        assert info is not None, f"단추가 없음: {key}"
        assert not info.get("disabled"), f"단추가 비활성: {key}"
        if info.get("on_click"):
            info["on_click"](*info.get("args", ()))
        else:
            self.session["_click"] = key
        return self.run()

    def result(self):
        r = self.session.get("result")
        return f"{r[0]}: {r[1]}" if r else ""


class Upload:
    def __init__(self, path):
        self.data = Path(path).read_bytes()

    def getvalue(self):
        return self.data


def main():
    # DB: sqlite 하나를 모든 '브라우저'가 같이 씀
    import db as dbmod
    shared = dbmod.SqliteDatabase()
    shared.init_schema()

    class FakePg:
        def __init__(self, url):
            self.pool = types.SimpleNamespace(close=lambda: None)
            self.tx = shared.tx

        def init_schema(self):
            pass
    dbmod.PgDatabase = FakePg
    os.environ.update({"DATABASE_URL": "postgresql://x", "ADMIN_PASSWORD": "admin-pw", "OPENROUTER_API_KEY": ""})

    import ai
    ai.TIMEOUT_SEC = 1

    def login(no, pw):
        app = FakeApp().run()
        for k in ("login_student_no", "login_password", "login_submit", "result"):
            assert k in app.keys, f"로그인 화면에 key 없음: {k}"
        app.session["login_student_no"], app.session["login_password"] = no, pw
        app.session["_click"] = "login_submit"
        return app.run()

    bad = login("admin", "틀린비번")
    assert bad.result().startswith("막힘"), bad.result()
    print("OK 틀린 비밀번호 → 막힘")

    admin = login("admin", "admin-pw")
    need = ["whoami", "result", "admin_roster_file", "admin_team_count", "admin_start", "admin_close",
            "admin_assign", "assign_status", "unassigned"]
    for k in need:
        assert k in admin.keys, f"관리자 화면에 key 없음: {k}"
    admin.session["admin_roster_file"] = Upload(ROOT / "tests/data/어려움.csv")
    admin.session["admin_team_count"] = 4
    admin.click("admin_start")
    assert admin.result().startswith("승인"), admin.result()
    assert all(f"team_{n}" in admin.keys for n in range(1, 5)), admin.keys.keys()
    print("OK 관리자 명단 올리기:", admin.result())

    def student(n):
        return login(f"20263{n:02d}", "1234")

    s1 = student(1)
    assert "whoami" in s1.keys and any("학생1" in t for t in s1.keys["whoami"]["texts"])
    assert all(f"apply_{n}" in s1.keys for n in range(1, 5))
    s1.click("apply_1"); assert s1.result().startswith("승인"), s1.result()
    assert s1.keys["apply_2"]["disabled"], "팀이 있는데 지원 단추가 비활성화되지 않음"
    s4 = student(4)
    s4.click("apply_1"); assert s4.result().startswith("대기"), s4.result()
    assert s4.keys["apply_2"]["disabled"], "결과를 기다리는데 지원 단추가 비활성화되지 않음"
    s1.run()
    assert "vote_yes_2026304" in s1.keys and "vote_no_2026304" in s1.keys
    s1.click("vote_yes_2026304"); assert s1.result().startswith("승인"), s1.result()
    assert "vote_yes_2026304" not in s1.keys
    print("OK 학생 지원·투표:", s1.result())
    # 막힘: 주 역할 중복
    s2 = student(2); s2.click("apply_1"); assert s2.result().startswith("막힘"), s2.result()
    print("OK 주 역할 중복 막힘:", s2.result())
    # 학생 화면에 관리자 단추가 없어야 함
    assert not any(k.startswith("admin_") for k in s2.keys), "학생 화면에 관리자 단추가 있음"
    # 학생이 관리자 콜백을 직접 불러도 막혀야 함
    # (흉내 모듈에서는 스크립트를 실행한 세션마다 함수가 따로 있으므로, 학생 세션의 do_close 를 부름)
    s2.g["do_close"]()
    assert s2.result().startswith("막힘"), "학생이 마감 기능을 씀"
    s2.g["run_assignment"](s2.g["get_db"](), s2.mod.container())
    assert s2.result().startswith("막힘"), "학생이 배정 기능을 씀"
    print("OK 학생 계정으로 관리자 기능 막힘")

    admin.run(); admin.click("admin_close"); assert admin.result().startswith("승인"), admin.result()
    print("OK 마감:", admin.result())
    assert any("학생" in t for t in admin.keys["unassigned"]["texts"])
    admin.session["_click"] = "admin_assign"
    admin.run()
    assert admin.result().startswith("승인"), admin.result()
    assert "admin_confirm" in admin.keys, "배정안이 있는데 확정 단추가 없음"
    print("OK AI 배정(키가 없어 1~3차 실패 → 앱 자체 배정):", admin.result())
    admin.click("admin_confirm"); assert admin.result().startswith("승인"), admin.result()
    print("OK 확정:", admin.result())
    # 새 '브라우저'로 확정 결과 확인
    check = login("2026305", "1234")
    for n in range(1, 5):
        print("  ", n, [t for t in check.keys[f"team_{n}"]["texts"] if t.startswith("- ")])
    print("모든 연기 시험 통과")


if __name__ == "__main__":
    main()
