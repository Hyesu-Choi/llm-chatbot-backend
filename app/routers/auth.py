"""회원가입 · 로그인 · 로그아웃 · 내 정보 API (/api/auth/...)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    CurrentUser,
    clear_auth_cookie,
    hash_password,
    set_auth_cookie,
    verify_password,
)
from app.db import get_db
from app.models import User
from app.schemas import LoginRequest, SignupRequest, UserResponse

router = APIRouter(prefix="/auth")

# 엔드포인트마다 DB 세션을 받는 타입. `db: Db` 로 쓴다.
Db = Annotated[AsyncSession, Depends(get_db)]


def _normalize_email(email: str) -> str:
    # "Test@Example.com " 과 "test@example.com" 을 같은 계정으로 취급하기 위해 저장 · 조회 전에 맞춘다
    return email.strip().lower()


# status_code=201 Created: 새 자원(사용자)을 만들었다는 뜻
# response_model: 반환한 User 객체를 UserResponse 모양으로 걸러서 내보낸다 (password_hash 제외)
@router.post(
    "/signup", status_code=status.HTTP_201_CREATED, response_model=UserResponse
)
async def signup(body: SignupRequest, response: Response, db: Db) -> User:
    user = User(
        email=_normalize_email(body.email), password_hash=hash_password(body.password)
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # users.email의 unique 제약에 걸림 = 이미 가입된 이메일.
        # "먼저 조회해 보고 없으면 넣기"는 동시에 두 요청이 오면 둘 다 통과할 수 있어서,
        # 일단 넣고 DB가 거절하면 처리하는 쪽이 확실하다.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "이미 가입된 이메일입니다."
        ) from None

    # 가입하면 바로 로그인된 상태로 만든다
    set_auth_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response, db: Db) -> User:
    user = await db.scalar(
        select(User).where(User.email == _normalize_email(body.email))
    )
    # 사용자가 없을 때도 verify_password를 거친다 (응답 시간으로 가입 여부를 들키지 않게, app/auth.py 참고)
    if not verify_password(body.password, user.password_hash if user else None):
        # "이메일이 없습니다" / "비밀번호가 틀렸습니다"로 나누면 가입된 이메일 목록을 캐낼 수 있어서 같은 메시지로 답한다
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다."
        )

    set_auth_cookie(response, user.id)
    return user


# 204 No Content: 성공했고 돌려줄 본문은 없음
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    # JWT는 서버에 저장하지 않으므로, 로그아웃 = 브라우저의 쿠키를 지우라고 알려주는 것
    clear_auth_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> User:
    # CurrentUser 의존성이 로그인 확인을 끝낸 뒤라, 여기 도착했다면 이미 로그인된 사용자다.
    # 프론트는 앱을 열 때 이걸 호출해서 200이면 로그인 상태, 401이면 로그인 화면으로 보낸다.
    return user
