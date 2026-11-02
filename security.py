"""비밀번호 해시(bcrypt). 비밀번호를 적힌 그대로 DB에 저장하지 않는다."""

from __future__ import annotations


def hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def check_password(password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:  # 해시 꼴이 아닌 값
        return False
