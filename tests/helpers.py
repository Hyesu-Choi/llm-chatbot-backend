"""테스트 여러 곳에서 쓰는 도우미 함수.

conftest.py에 두지 않은 이유: conftest.py는 pytest가 특별하게 불러오는 파일이라
일반 모듈처럼 import해서 쓰는 걸 pytest가 권장하지 않는다. 그냥 함수는 이렇게 따로 둔다.
"""

from fastapi.testclient import TestClient


def signup(client: TestClient, email: str = "user@example.com") -> dict[str, str]:
    """가입하고 그 사용자의 쿠키를 돌려준다. 여러 사용자를 오가며 테스트할 때 쓴다."""
    client.cookies.clear()
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 201, response.text
    return dict(client.cookies)


def login_as(client: TestClient, cookies: dict[str, str]) -> None:
    """signup()이 돌려준 쿠키로 바꿔 끼워서 그 사용자로 요청하게 한다."""
    client.cookies.clear()
    client.cookies.update(cookies)
