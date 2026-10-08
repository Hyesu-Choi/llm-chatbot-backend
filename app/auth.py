"""인증의 핵심 부품: 비밀번호 해시, 로그인 토큰(JWT), 쿠키, 현재 사용자 확인.

전체 흐름:
    1. 가입    비밀번호 → argon2 해시 → DB에 해시만 저장
    2. 로그인  입력 비밀번호를 저장된 해시와 비교 → 맞으면 JWT 발급 → httpOnly 쿠키로 내려줌
    3. 이후    브라우저가 요청마다 쿠키를 자동으로 붙여 보냄 → get_current_user가 JWT 검증 → 사용자 조회

JWT(JSON Web Token)란?
    "header.payload.signature" 세 덩어리로 된 문자열. payload에 {"sub": "사용자 id", "exp": 만료시각}이 들어 있다.
    payload는 암호화가 아니라 그냥 인코딩이라 누구나 읽을 수 있다 → 비밀 정보를 넣으면 안 된다.
    대신 signature가 jwt_secret으로 서명돼 있어서, 누가 payload를 고치면 서명이 안 맞아 검증에서 걸린다.
    서버는 토큰을 따로 저장하지 않고 서명만 확인하면 된다 (그래서 로그아웃해도 토큰 자체는 만료까지 유효,
    로그아웃은 브라우저 쿠키를 지우는 것).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User

ACCESS_TOKEN_COOKIE = "access_token"
JWT_ALGORITHM = "HS256"

# ── 비밀번호 ──────────────────────────────────────────

# recommended(): 현재 권장 알고리즘인 argon2로 설정된 해셔.
# 해시 결과에 알고리즘 · 설정 · 무작위 salt가 모두 들어 있어서, 같은 비밀번호도 매번 다른 해시가 나온다.
# (salt 덕분에 "123456의 해시값" 같은 미리 계산된 표로 역추적할 수 없다)
password_hash = PasswordHash.recommended()

# 없는 이메일로 로그인할 때도 해시 비교를 한 번 하기 위한 가짜 해시. 아래 verify_password 참고.
_DUMMY_HASH = password_hash.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    """비밀번호가 해시와 맞는지 확인한다. 사용자가 없으면 hashed=None을 넘긴다.

    사용자가 없을 때 바로 False를 돌려주면 응답이 눈에 띄게 빨라진다 (argon2는 일부러 느린 알고리즘).
    공격자는 응답 시간만 보고 "이 이메일은 가입돼 있다"를 알아낼 수 있으므로,
    없을 때도 가짜 해시로 똑같이 계산해서 걸리는 시간을 맞춘다.
    """
    if hashed is None:
        password_hash.verify(password, _DUMMY_HASH)
        return False
    return password_hash.verify(password, hashed)


# ── 토큰과 쿠키 ───────────────────────────────────────


def create_access_token(user_id: int) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    # sub(subject): 이 토큰이 누구 것인지. JWT 규격상 문자열이어야 한다.
    # exp(expiration): 만료 시각. 지나면 jwt.decode가 알아서 거부한다.
    payload = {"sub": str(user_id), "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=create_access_token(user_id),
        max_age=settings.jwt_expire_minutes * 60,  # 초 단위. 토큰 만료와 같이 맞춘다
        # httponly: 자바스크립트(document.cookie)로 읽을 수 없다.
        #   XSS로 악성 스크립트가 실행돼도 토큰을 훔쳐 갈 수 없다. localStorage에 토큰을 두지 않는 이유.
        httponly=True,
        # secure: HTTPS에서만 전송. 배포에서는 True (로컬 http에서는 False여야 쿠키가 동작)
        secure=settings.cookie_secure,
        # samesite="lax": 다른 사이트에서 우리 서버로 보내는 POST 요청에는 쿠키를 안 붙인다.
        #   악성 사이트가 몰래 "로그인된 내 계정으로" 요청을 보내는 CSRF 공격을 막는다.
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    # 설정할 때와 같은 옵션으로 지워야 브라우저가 같은 쿠키로 인식한다
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


# ── 현재 사용자 (FastAPI 의존성) ──────────────────────


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    # Cookie(): 요청 쿠키에서 같은 이름(access_token)의 값을 꺼내 준다. 없으면 None.
    access_token: Annotated[str | None, Cookie()] = None,
) -> User:
    """로그인한 사용자를 돌려준다. 로그인 안 했거나 토큰이 이상하면 401.

    엔드포인트에서 `user: Annotated[User, Depends(get_current_user)]` 로 받으면
    그 엔드포인트는 자동으로 "로그인 필요"가 된다.
    """
    if access_token is None:
        raise _unauthorized()
    try:
        # 서명 확인 + 만료 확인을 한 번에. algorithms를 꼭 지정해야 "none" 알고리즘 위조 공격을 막는다.
        payload = jwt.decode(
            access_token, settings.jwt_secret, algorithms=[JWT_ALGORITHM]
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        # InvalidTokenError: 서명 불일치, 만료, 형식 오류를 모두 포함
        raise _unauthorized() from None

    # 토큰은 유효해도 그 사이 탈퇴한 사용자일 수 있으니 DB에서 다시 확인한다
    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized()
    return user


# 엔드포인트 인자에 매번 긴 타입을 쓰지 않도록 줄임말을 만들어 둔다: `user: CurrentUser`
CurrentUser = Annotated[User, Depends(get_current_user)]
