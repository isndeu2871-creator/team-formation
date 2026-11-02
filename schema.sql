-- 팀 편성 앱의 DB 스키마 (Supabase / PostgreSQL)
-- 모든 테이블 이름은 tf_ 로 시작한다(6장 실습 테이블과 같은 DB에 함께 들어가므로 구분).
-- 앱(db.py)은 시작할 때 이 파일을 그대로 실행한다. 그래서 이 파일이 곧 앱이 실제로 쓰는 테이블·열·제약이다.
-- 여러 번 실행해도 되도록 모두 "if not exists" 로 만든다.

-- 학생과 관리자
create table if not exists tf_students (
    id            serial primary key,
    student_no    text not null unique,                 -- 학번(유일). 관리자는 'admin'
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
                                                         -- 관리자는 주 역할이 없으므로 null 허용
    password_hash text not null,                         -- bcrypt 해시(비밀번호를 적힌 그대로 저장하지 않음)
    is_admin      boolean not null default false,
    check (is_admin or main_role is not null)            -- 학생은 주 역할이 꼭 있어야 함
);

-- 보조 역할: 학생 1명에 0~2행
create table if not exists tf_student_sub_roles (
    student_id integer not null references tf_students(id) on delete cascade,
    role       text not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

-- 팀: 팀1, 팀2, ...
create table if not exists tf_teams (
    id      serial primary key,
    team_no integer not null unique check (team_no >= 1), -- 팀 번호(화면 key team_1 의 1)
    name    text not null unique                          -- '팀1'
);

-- 팀원
--  (team_id, slot_no) 기본 키: 한 자리에는 한 학생만 → 마지막 자리에 두 명이 동시에 들어가면 먼저 도착한 학생만 기록됨
--  student_id 유일: 한 학생은 한 팀에만 소속
--  slot_no 1~4: 다섯 번째 자리를 DB가 막음
create table if not exists tf_members (
    team_id    integer not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),
    student_id integer not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

-- 지원
create table if not exists tf_applications (
    id         serial primary key,
    team_id    integer not null references tf_teams(id) on delete cascade,
    student_id integer not null references tf_students(id) on delete cascade,
    status     text not null check (status in ('대기', '승인', '거절')),
    reason     text,                                      -- 승인·거절 이유(팀원 거부, 승인 직전 검사, 모집 마감 등)
    created_at timestamptz not null default now(),
    decided_at timestamptz
);
-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있음(DB에서도 막음)
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

-- 투표: 한 지원에 한 팀원은 한 번만 투표(바꿀 수 없음)
create table if not exists tf_votes (
    application_id integer not null references tf_applications(id) on delete cascade,
    voter_id       integer not null references tf_students(id) on delete cascade,
    vote           text not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- 설정: 마감 여부(closed), 확정 여부(confirmed), 배정안(proposal, JSON), 마지막 DB 깨우기 시각(keepalive_at) 등
create table if not exists tf_settings (
    key   text primary key,
    value text
);

-- AI 호출 기록: 몇 차 모델을 불렀고, 성공했는지, 왜 실패했는지(시간 초과, HTTP 429, 빈 응답, 검사 실패 등)
create table if not exists tf_ai_logs (
    id          serial primary key,
    run_id      text not null,                            -- 'AI 배정' 단추를 한 번 누른 것 하나
    attempt     integer not null,                         -- 1차, 2차, 3차 (앱 자체 배정은 0)
    model       text not null,
    outcome     text not null,                            -- 성공 / 실패
    detail      text,
    elapsed_sec numeric(7, 1),
    created_at  timestamptz not null default now()
);
