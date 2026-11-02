# 팀 프로젝트 팀 편성 웹 앱

학생들이 팀 프로젝트 팀을 스스로 꾸리고, 마감 뒤 남은 학생은 AI가 배정을 돕는 Streamlit 앱입니다.
AI의 배정안은 앱이 규칙으로 검사하며, AI가 실패하면 2차·3차 모델로, 끝까지 실패하면 앱이 스스로 배정합니다.

- 작성자: 임유주
- 만든 방법: 클로드(Claude)와 함께 바이브 코딩

---

## 1. 배포 주소
- https://team-formation-jukl2ja9rn2sxgc7fqvzsx.streamlit.app/

## 2. PRD와 구현 계획의 위치
- PRD: [docs/PRD.md](docs/PRD.md)
- 구현 계획: [docs/구현 계획.md](docs/구현%20계획.md)
- DB 스키마: [schema.sql](schema.sql)

## 3. 고른 해결 방법(공고 3장)
| 기능 | 고른 해결 | 구현 |
|---|---|---|
| 다른 사람의 변경 보기 | **어려운 해결** | `st.fragment(run_every=3)`로 학생 화면 전체와 관리자 화면의 팀·팀이 없는 학생 영역만 3초마다 다시 그림(새로 고침 단추 없음) |
| 필수 포함 역할을 지킬 수 없는 팀 | **어려운 해결** | 지원할 때 "들어간 뒤 남는 자리 < 아직 못 채운 필수 역할 수"이면 막음(`rules.join_block_reason`). 한 학생이 기획자·웹 프로그래머를 둘 다 할 수 있어도 하나로만 셈 |
| 같은 팀에 대기 중인 지원이 여럿 | **어려운 해결** | 마지막 찬성이 들어온 순간(투표가 끝난 순서) 정원·주 역할 중복·필수 역할 자리를 다시 검사해 어기면 거절(`services.vote`) |
| 마지막 자리에 두 명이 동시에 들어감 | **어려운 해결** | `tf_members`의 `(team_id, slot_no)` 기본 키와 `student_id` 유일 제약, `slot_no between 1 and 4`(schema.sql). 바꾸는 작업은 advisory lock 트랜잭션으로 차례로 처리 |
| AI 응답이 늦을 때 | **어려운 해결** | 요청을 별도 스레드로 보내고 모델마다 **전체 대기 시간**을 직접 재서 120초가 넘으면 끊고 다음 모델로(`ai.run_with_deadline`) |
| 3차 모델까지 실패 | **어려운 해결** | 앱이 모든 경우를 따져(완전 탐색, 실패 상태 기억) 검사 5가지를 모두 통과하는 배정을 찾음(`rules.solve`). 배정이 없으면 이유를 보여 줌(`rules.infeasible_reasons`) |
| (덧붙임) AI 끄기 | - | 관리자 화면의 "AI를 부름" 체크(기본 켜짐)를 끄면 AI 없이 앱이 바로 배정함. 시험할 때 무료 호출 횟수(하루 50회)를 아끼기 위함 |
| AI 대기 중 화면 | **어려운 해결** | `assign_status`에 "2차 모델(모델 이름)의 응답을 기다리는 중… 37초 / 120초"처럼 몇 차 모델인지와 경과 시간을 1초마다 표시, 끝난 시도는 성공/실패와 이유를 목록으로 표시 |
| DB(Supabase) 일시정지 막기 | **어려운 해결** | 외부 예약 서비스 GitHub Actions(`.github/workflows/keepalive.yml`)가 매일 두 번(09:17, 21:17 한국 시각) DB에 질의. 마지막 질의 시각은 관리자 화면 "설정 확인"에 표시 |

## 4. 쓴 모델 3개
- 1차 `google/gemma-4-31b-it:free`, 2차 `nvidia/nemotron-3-ultra-550b-a55b:free`, 3차 `nvidia/nemotron-3.5-lightning:free`
- 처음에는 공고 예시와 시연 동영상의 모델(`nvidia/nemotron-3-super-120b-a12b:free` 등)을 썼으나, 11/2 배포한 앱의 "모델 확인"에서 3개 모두 오픈라우터에 없다고 나와(화면 `img/model-check-missing.png`) 그날 있는 무료 모델 가운데서 다시 고름(화면 `img/model-check-free-list.png`)
- Streamlit Secrets의 `OPENROUTER_MODELS`(쉼표로 구분)로 코드를 고치지 않고 바꿀 수 있음
- 무료 모델로 자동 연결하는 라우터(`openrouter/free`)는 쓰지 않음(코드에서 걸러 냄)

## 5. 오픈라우터 AI 모델 호출 실패 사례
- (배포한 앱에서 연습 시나리오를 실행하며 관리자 화면의 "AI 호출 기록" 표를 캡처해 `img/`에 넣고 기입)

## 6. 클로드가 잘못된 추론이나 답변을 한 사례와 알아챈 방법
1. **AI 실패 처리 시험에서 "규칙을 어긴 배정안"이 사실은 규칙을 지키는 배정안이었음**
   - 클로드가 `tests/test_ai.py`에서 "2차 모델이 규칙을 어긴 배정안을 낸다"는 가짜 응답을 만들면서,
     팀3에 `테스터(보조 기획자)`와 `프론트엔드 디자이너(보조 웹 프로그래머)`를 넣고 "팀3에 웹 프로그래머가 없다"고 판단했음.
     실제로는 보조 역할로 기획자와 웹 프로그래머를 서로 다른 학생이 맡으므로 규칙을 지키는 배정안이었음(보조 역할도 필수 역할로 인정하는 2.3절 규칙을 시험 데이터를 만들 때 놓침).
   - 알아챈 방법: 시험을 실행하자 2차 모델 시도가 "실패"가 아니라 "성공"으로 기록되어 기대 결과와 달랐음.
     앱의 검사 코드(`rules.validate_proposal`)가 맞고 시험 데이터가 틀렸다는 것을 확인한 뒤, 팀2에는 웹 프로그래머가, 팀3에는 기획자가 없도록 가짜 응답을 고침(`tests/test_ai.py`의 `m2` 응답과 주석).
2. (배포한 앱에서 시험하며 찾은 사례를 화면·커밋과 함께 덧붙임)

## 7. 앱에서 발견한 문제점이나 개선 필요 사항
- (배포한 앱에서 연습 시나리오를 실행하며 찾은 문제를 시나리오 단계·화면과 함께 기입)

---

## 실행 방법(내 컴퓨터)
1. `pip install -r requirements.txt`
2. `.env.example`을 복사해 이름을 정확히 `.env`로 바꾸고 값을 채움(깃허브에 올라가지 않음)
3. `streamlit run streamlit_app.py`
- 앱이 시작할 때 `schema.sql`을 실행해 `tf_` 테이블을 만들고, `ADMIN_PASSWORD`로 관리자 계정(`admin`)을 만들거나 비밀번호를 갱신함

## 시험
```
python tests/test_scenarios.py   # 연습 시나리오 3개(부록 1~3)를 업무 로직으로 실행
python tests/test_ai.py          # 1~3차 모델 실패 처리, 120초 제한, 앱 자체 배정
python tests/smoke_ui.py         # Streamlit 흉내 모듈로 화면 key·콜백 확인
```

## 파일 구조
| 파일 | 하는 일 |
|---|---|
| `streamlit_app.py` | 화면(공고 2.7절 key) |
| `services.py` | 명단, 로그인, 지원, 투표, 마감, 확정 |
| `rules.py` | 규칙 검사, 배정안 검사 5가지, 완전 탐색 배정 |
| `ai.py` | 오픈라우터 1~3차 모델 호출과 실패 처리 |
| `db.py` | DB 연결 풀(한 번 만들어 계속 씀), 상태를 질의 1번으로 읽기 |
| `security.py` | bcrypt 비밀번호 해시 |
| `schema.sql` | `tf_` 테이블과 제약 |
| `.github/workflows/keepalive.yml` | 매일 DB 질의(일시정지 막기) |

## 키 관리
- `OPENROUTER_API_KEY`, `DATABASE_URL`, `ADMIN_PASSWORD`는 `.env`(내 컴퓨터)와 Streamlit Secrets(배포)에만 둠. `.gitignore`가 `.env`를 걸러 냄.
- 화면에는 키가 "있음/없음"만 보여 주고, 오류 안내에서도 DB 주소와 키를 지워서 보여 줌(`safe_error`). `.streamlit/config.toml`에서 오류 자세한 내용 표시를 끔.
- 비밀번호는 bcrypt 해시로만 저장함.
