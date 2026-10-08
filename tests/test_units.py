"""DB · HTTP 없이 함수 하나만 따로 검사하는 단위 테스트. 가장 빠르고 원인을 찾기 쉽다."""

import pytest

from app.llm import trim_history
from app.routers.conversations import _clean_title, _fallback_title
from app.schemas import ChatMessage


def _m(role: str, length: int) -> ChatMessage:
    return ChatMessage(role=role, content="x" * length)


def _shape(messages: list[ChatMessage]) -> list[tuple[str, int]]:
    return [(m.role, len(m.content)) for m in messages]


class TestTrimHistory:
    # 클래스로 묶으면 관련 테스트를 한눈에 보고, `pytest -k TrimHistory`로 이것만 돌릴 수 있다

    def test_short_history_is_unchanged(self):
        history = [_m("user", 10), _m("assistant", 10), _m("user", 10)]
        assert trim_history(history, 100) == history

    def test_keeps_most_recent_messages_within_limit(self):
        history = [
            _m("user", 3000),
            _m("assistant", 3000),
            _m("user", 100),
            _m("assistant", 2000),
            _m("user", 50),
        ]
        assert _shape(trim_history(history, 6000)) == [
            ("user", 100),
            ("assistant", 2000),
            ("user", 50),
        ]

    def test_drops_leading_assistant_message(self):
        # 한도 안에 [답변, 질문]이 들어오지만, 질문 없는 답변으로 시작하면 안 되므로 답변을 버린다
        history = [_m("user", 500), _m("assistant", 40), _m("user", 50)]
        assert _shape(trim_history(history, 100)) == [("user", 50)]

    def test_always_keeps_latest_message_even_if_too_long(self):
        assert _shape(trim_history([_m("user", 9000)], 100)) == [("user", 9000)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("리스트 튜플 차이", "리스트 튜플 차이"),
        ('"리스트 튜플 차이"', "리스트 튜플 차이"),
        ("제목: 저녁 메뉴 추천.", "저녁 메뉴 추천"),
        ("**스트레스 해소법**\n설명입니다", "스트레스 해소법"),
        ("", ""),
    ],
)
def test_clean_title(raw, expected):
    assert _clean_title(raw) == expected


def test_fallback_title_collapses_spaces_and_truncates():
    assert _fallback_title("짧은   질문\n입니다") == "짧은 질문 입니다"
    assert _fallback_title("가" * 40) == "가" * 30 + "…"
