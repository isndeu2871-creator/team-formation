-- 시험(tests/)에서만 쓰는 sqlite 판 스키마. 배포 앱은 ../schema.sql(PostgreSQL)을 쓴다.
-- 제약(유일성, check)은 schema.sql 과 같게 맞춘다.
create table tf_students (
    id integer primary key autoincrement,
    student_no text not null unique,
    name text not null,
    main_role text check (main_role in ('기획자','웹 프로그래머','프론트엔드 디자이너','테스터','보고서 작성자','발표자')),
    password_hash text not null,
    is_admin boolean not null default 0,
    check (is_admin or main_role is not null)
);
create table tf_student_sub_roles (
    student_id integer not null references tf_students(id) on delete cascade,
    role text not null check (role in ('기획자','웹 프로그래머','프론트엔드 디자이너','테스터','보고서 작성자','발표자')),
    primary key (student_id, role)
);
create table tf_teams (
    id integer primary key autoincrement,
    team_no integer not null unique check (team_no >= 1),
    name text not null unique
);
create table tf_members (
    team_id integer not null references tf_teams(id) on delete cascade,
    slot_no integer not null check (slot_no between 1 and 4),
    student_id integer not null unique references tf_students(id) on delete cascade,
    joined_at text not null default current_timestamp,
    primary key (team_id, slot_no)
);
create table tf_applications (
    id integer primary key autoincrement,
    team_id integer not null references tf_teams(id) on delete cascade,
    student_id integer not null references tf_students(id) on delete cascade,
    status text not null check (status in ('대기','승인','거절')),
    reason text,
    created_at text not null default current_timestamp,
    decided_at text
);
create unique index tf_applications_one_pending on tf_applications (student_id) where status = '대기';
create table tf_votes (
    application_id integer not null references tf_applications(id) on delete cascade,
    voter_id integer not null references tf_students(id) on delete cascade,
    vote text not null check (vote in ('찬성','거부')),
    created_at text not null default current_timestamp,
    primary key (application_id, voter_id)
);
create table tf_settings (key text primary key, value text);
create table tf_ai_logs (
    id integer primary key autoincrement,
    run_id text not null,
    attempt integer not null,
    model text not null,
    outcome text not null,
    detail text,
    elapsed_sec numeric,
    created_at text not null default current_timestamp
);
