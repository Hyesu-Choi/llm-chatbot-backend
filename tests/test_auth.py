"""회원가입 · 로그인 · 로그아웃 · 내 정보 (/api/auth/...)

테스트 이름은 "무엇을 하면 → 어떻게 되어야 한다"가 드러나게 짓는다.
실패했을 때 이름만 보고도 어떤 약속이 깨졌는지 알 수 있다.
"""

from datetime import UTC, datetime, timedelta

import jwt

from app.config import settings
from tests.helpers import signup

CREDENTIALS = {"email": "user@example.com", "password": "password123"}


def test_signup_returns_user_and_logs_in(client):
    response = client.post("/api/auth/signup", json=CREDENTIALS)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "user@example.com"
    # 비밀번호 해시가 응답에 절대 섞여 나오면 안 된다
    assert "password_hash" not in body
    assert "access_token" in client.cookies
    # 가입 직후 바로 로그인 상태
    assert client.get("/api/auth/me").json()["email"] == "user@example.com"


def test_signup_sets_httponly_samesite_cookie(client):
    response = client.post("/api/auth/signup", json=CREDENTIALS)

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_signup_normalizes_email(client):
    client.post(
        "/api/auth/signup", json={**CREDENTIALS, "email": "  Hyesu@Example.COM "}
    )
    client.cookies.clear()

    # 소문자 · 공백 제거로 저장됐으니 다른 대소문자로도 로그인된다
    response = client.post(
        "/api/auth/login", json={**CREDENTIALS, "email": "hyesu@example.com"}
    )
    assert response.status_code == 200


def test_signup_with_existing_email_returns_409(client):
    client.post("/api/auth/signup", json=CREDENTIALS)

    response = client.post("/api/auth/signup", json=CREDENTIALS)

    assert response.status_code == 409
    assert response.json() == {"error": "이미 가입된 이메일입니다."}


def test_signup_with_short_password_returns_korean_422(client):
    response = client.post(
        "/api/auth/signup", json={**CREDENTIALS, "password": "short"}
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "비밀번호는 8자 이상 128자 이하로 입력해 주세요."
    }


def test_signup_with_invalid_email_returns_422(client):
    response = client.post(
        "/api/auth/signup", json={**CREDENTIALS, "email": "not-an-email"}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "이메일 형식이 올바르지 않습니다."}


def test_login_wrong_password_and_unknown_email_get_same_message(client):
    signup(client)
    client.cookies.clear()

    wrong_password = client.post(
        "/api/auth/login", json={**CREDENTIALS, "password": "wrong-pass"}
    )
    unknown_email = client.post(
        "/api/auth/login", json={**CREDENTIALS, "email": "nobody@example.com"}
    )

    # 둘이 다르면 "이 이메일은 가입돼 있다"는 정보가 새어 나간다
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_logout_clears_cookie(client):
    signup(client)

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert "access_token" not in client.cookies
    assert client.get("/api/auth/me").status_code == 401


def test_me_without_cookie_returns_401(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json() == {"error": "로그인이 필요합니다."}


def _token(secret: str, expires_in: timedelta) -> str:
    payload = {"sub": "1", "exp": datetime.now(UTC) + expires_in}
    return jwt.encode(payload, secret, algorithm="HS256")


def test_me_with_expired_token_returns_401(client):
    signup(client)
    client.cookies.set(
        "access_token", _token(settings.jwt_secret, timedelta(minutes=-1))
    )

    assert client.get("/api/auth/me").status_code == 401


def test_me_with_token_signed_by_other_key_returns_401(client):
    signup(client)
    # 내용(sub=1)은 진짜 사용자지만 서명 키가 다르다 = 위조 토큰
    client.cookies.set("access_token", _token("y" * 64, timedelta(minutes=5)))

    assert client.get("/api/auth/me").status_code == 401
