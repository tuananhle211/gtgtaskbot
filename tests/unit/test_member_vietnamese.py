"""The Vietnamese a Member actually types, and what it costs them.

Three properties, and the third is the one with money attached:

1. **Routing.** Accented and unaccented, upper and lower case, abbreviated and
   spelled out - all reach the same intent.
2. **Safety of the normalizer.** It expands ``cmt`` but must never rewrite a
   URL, an ``@mention`` or an email address.
3. **The quota boundary.** Operational Vietnamese is answered from the database
   and costs nothing. Only generative requests spend a chat slot, and the
   decision is made *before* the provider is called.
"""

from __future__ import annotations

import pytest

from meobot.application.access_gate import IncomingUpdate
from meobot.domain.member.intents import (
    GENERATIVE_MARKERS,
    MemberIntent,
    classify,
    is_operational,
    split_mixed,
)
from meobot.domain.member.normalization import normalize, strip_accents


def intent_of(text: str) -> MemberIntent:
    return classify(text).intent


# --- 1. Routing -------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # View work
        ("Hôm nay tôi có việc gì?", MemberIntent.VIEW_WORK),
        ("Cho tôi xem việc hôm nay.", MemberIntent.VIEW_WORK),
        ("Việc của tôi.", MemberIntent.VIEW_WORK),
        ("Xem việc quá hạn.", MemberIntent.VIEW_WORK),
        ("viec hnay cua toi", MemberIntent.VIEW_WORK),
        # Accept and start
        ("Tôi nhận việc này.", MemberIntent.ACCEPT_WORK),
        ("Việc này để tôi làm.", MemberIntent.ACCEPT_WORK),
        ("toi nhan viec", MemberIntent.ACCEPT_WORK),
        # Progress
        ("Tôi làm được 3 trên 5 bình luận rồi.", MemberIntent.UPDATE_PROGRESS),
        ("Còn thiếu 2 comment.", MemberIntent.UPDATE_PROGRESS),
        ("da lam 3 cmt", MemberIntent.UPDATE_PROGRESS),
        ("Tôi đang làm.", MemberIntent.UPDATE_PROGRESS),
        # Completion
        ("Tôi làm xong rồi.", MemberIntent.COMPLETE_WORK),
        ("toi lam xong roi", MemberIntent.COMPLETE_WORK),
        ("Đã hoàn thành.", MemberIntent.COMPLETE_WORK),
        # Proof
        ("Tôi gửi ảnh bằng chứng.", MemberIntent.SUBMIT_PROOF),
        ("gui anh bang chung", MemberIntent.SUBMIT_PROOF),
        ("Tôi nộp kết quả việc này.", MemberIntent.SUBMIT_PROOF),
        # Issues
        ("Bài đang chờ admin duyệt.", MemberIntent.REPORT_ISSUE),
        ("bai dang cho ad duyet", MemberIntent.REPORT_ISSUE),
        ("Bài bị từ chối.", MemberIntent.REPORT_ISSUE),
        ("bai bi reject", MemberIntent.REPORT_ISSUE),
        ("group khong cho dang", MemberIntent.REPORT_ISSUE),
        # Channels and schedule
        ("page cua toi", MemberIntent.VIEW_CHANNELS),
        ("kenh toi dang lam", MemberIntent.VIEW_CHANNELS),
        ("toi thieu bai nao", MemberIntent.VIEW_SCHEDULE_GAPS),
        ("kenh nao chua dang", MemberIntent.VIEW_SCHEDULE_GAPS),
        # Personal results
        ("bao cao ca nhan", MemberIntent.VIEW_PERSONAL_REPORT),
        ("Cho tôi xem kết quả của tôi.", MemberIntent.VIEW_PERSONAL_REPORT),
        # AI allowance
        ("Tôi còn bao nhiêu lượt?", MemberIntent.VIEW_AI_ALLOWANCE),
        ("con bn luot", MemberIntent.VIEW_AI_ALLOWANCE),
        ("Xem lượt trò chuyện.", MemberIntent.VIEW_AI_ALLOWANCE),
        ("quota cua toi", MemberIntent.VIEW_AI_ALLOWANCE),
        # Home and help
        ("Bắt đầu", MemberIntent.HOME),
        ("TasksBot ơi", MemberIntent.HOME),
        ("Tôi cần làm gì?", MemberIntent.HOME),
        ("Mở menu", MemberIntent.HOME),
        ("Giúp tôi", MemberIntent.HELP),
    ],
)
def test_every_specified_utterance_routes(text: str, expected: MemberIntent) -> None:
    assert intent_of(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ngày mai tôi xin nghỉ.", MemberIntent.REQUEST_LEAVE),
        ("Cho tôi xin nghỉ sáng mai.", MemberIntent.REQUEST_LEAVE),
        ("Chiều thứ Sáu tôi xin nghỉ.", MemberIntent.REQUEST_LEAVE),
        ("toi xin nghi sang mai", MemberIntent.REQUEST_LEAVE),
        ("cho toi nghi chieu thu 6", MemberIntent.REQUEST_LEAVE),
        ("Sáng mai tôi đến muộn khoảng 30 phút.", MemberIntent.REQUEST_LATE),
        ("mai toi di muon 30p", MemberIntent.REQUEST_LATE),
        ("Hôm nay tôi đến muộn 20 phút.", MemberIntent.REQUEST_LATE),
        ("huy don nghi vua gui", MemberIntent.CANCEL_HR_REQUEST),
        ("Rút yêu cầu vừa gửi.", MemberIntent.CANCEL_HR_REQUEST),
        ("thang nay toi nghi bao nhieu", MemberIntent.VIEW_MY_HR_STATS),
        ("toi di muon may lan", MemberIntent.VIEW_MY_HR_STATS),
        ("Đơn nghỉ của tôi.", MemberIntent.VIEW_MY_HR_REQUESTS),
        ("Hôm nay ai nghỉ?", MemberIntent.VIEW_WHO_IS_OFF),
        ("Cho tôi xem đơn đang chờ duyệt.", MemberIntent.VIEW_PENDING_HR),
        ("Cho tôi báo cáo nhân sự tháng 7.", MemberIntent.VIEW_HR_REPORT),
    ],
)
def test_every_hr_utterance_routes(text: str, expected: MemberIntent) -> None:
    assert intent_of(text) is expected


@pytest.mark.parametrize(
    "text",
    ["Hôm nay tôi có việc gì?", "toi lam xong roi", "mai toi di muon 30p", "Bắt đầu"],
)
def test_case_does_not_change_the_answer(text: str) -> None:
    assert intent_of(text.upper()) is intent_of(text.lower()) is intent_of(text)


@pytest.mark.parametrize(
    ("accented", "plain"),
    [
        ("Hôm nay tôi có việc gì?", "hom nay toi co viec gi"),
        ("Tôi làm xong rồi.", "toi lam xong roi"),
        ("Ngày mai tôi xin nghỉ.", "ngay mai toi xin nghi"),
        ("Tôi còn bao nhiêu lượt?", "toi con bao nhieu luot"),
    ],
)
def test_accents_and_no_accents_agree(accented: str, plain: str) -> None:
    assert intent_of(accented) is intent_of(plain)


# --- 2. The normalizer is conservative --------------------------------------
@pytest.mark.parametrize(
    "protected",
    [
        "https://facebook.com/post/123",
        "http://fb.com/x?a=1&b=2",
        "fb.com/abc",
        "@linh_pr",
        "nguoi.dung@congty.vn",
    ],
)
def test_urls_mentions_and_emails_survive_untouched(protected: str) -> None:
    """Expanding an abbreviation inside a link would break the link."""
    result = normalize(f"gui cho toi {protected} nhe")
    assert protected in result.matchable


def test_an_abbreviation_only_expands_as_a_whole_word() -> None:
    """``rep`` is "trả lời"; ``report`` is not "trả lờiort"."""
    assert "tra loi" in normalize("rep giup toi").matchable
    assert "tra loiort" not in normalize("report nay dai qua").matchable


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("kenh tt cua toi", "tiktok"),
        ("clip tt hom nay", "tiktok"),
    ],
)
def test_an_ambiguous_abbreviation_expands_only_with_context(text: str, expected: str) -> None:
    assert expected in normalize(text).matchable


@pytest.mark.parametrize("text", ["tt nhe ban", "ad hoc"])
def test_an_ambiguous_abbreviation_is_left_alone_without_context(text: str) -> None:
    """A wrong guess about which platform somebody meant is worse than none."""
    result = normalize(text).matchable
    assert "tiktok" not in result
    assert "admin group" not in result


def test_the_original_message_is_never_discarded() -> None:
    """Audit and storage use what the person wrote, not the folded form."""
    original = "Tôi làm được 3/5 rồi nhé"
    assert normalize(original).original == original


def test_normalization_can_be_switched_off() -> None:
    """Case and accents still fold; expansion stops."""
    disabled = normalize("da lam 3 cmt", enabled=False).matchable
    assert "cmt" in disabled
    assert "binh luan" not in disabled


def test_strip_accents_handles_the_letter_d() -> None:
    """``đ`` is a distinct letter, not a ``d`` with a combining mark."""
    assert strip_accents("đi muộn") == "di muon"


# --- 3. The quota boundary --------------------------------------------------
def update_for(text: str) -> IncomingUpdate:
    return IncomingUpdate(
        bot_id=1,
        chat_id=2,
        chat_type="private",
        telegram_user_id=3,
        is_bot=False,
        message_id=1,
        text=text,
        is_command=text.startswith("/"),
        addressed_to_bot=True,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Bắt đầu",
        "Hôm nay tôi có việc gì?",
        "viec hnay cua toi",
        "Tôi nhận việc này.",
        "Tôi làm được 3 trên 5 bình luận rồi.",
        "toi lam xong roi",
        "Tôi gửi ảnh bằng chứng.",
        "Bài đang chờ admin duyệt.",
        "bao cao ca nhan",
        "page cua toi",
        "toi thieu bai nao",
        "Tôi còn bao nhiêu lượt?",
        "Ngày mai tôi xin nghỉ.",
        "mai toi di muon 30p",
        "Đơn nghỉ của tôi.",
        "thang nay toi nghi bao nhieu",
        "huy don nghi vua gui",
        "Giúp tôi",
    ],
)
def test_operational_vietnamese_costs_nothing(text: str) -> None:
    """The whole point: everyday Member work is free and always available."""
    assert is_operational(text)
    assert update_for(text).is_generative() is False


@pytest.mark.parametrize(
    "text",
    [
        "Viết giúp tôi 5 bình luận tự nhiên.",
        "Viết lại caption này.",
        "Gợi ý 10 ý tưởng seeding.",
        "Sửa nội dung để dễ được group duyệt.",
        "Phân tích vì sao video này ít view.",
        "Soạn giúp tôi tin nhắn báo đi muộn.",
        "Viết lại lý do xin nghỉ cho lịch sự.",
    ],
)
def test_generative_requests_are_metered(text: str) -> None:
    assert not is_operational(text)
    assert update_for(text).is_generative() is True


def test_a_slash_command_is_never_metered() -> None:
    assert update_for("/help").is_generative() is False


def test_an_unaddressed_message_is_never_metered() -> None:
    update = IncomingUpdate(
        bot_id=1,
        chat_id=-100,
        chat_type="supergroup",
        telegram_user_id=3,
        is_bot=False,
        message_id=1,
        text="Viết giúp tôi 5 bình luận",
        is_command=False,
        addressed_to_bot=False,
    )
    assert update.is_generative() is False


def test_an_unrecognised_message_falls_through_to_conversation() -> None:
    """A missing pattern costs one slot; it never refuses to help."""
    assert intent_of("Hôm nay trời đẹp nhỉ") is MemberIntent.GENERATIVE
    assert update_for("Hôm nay trời đẹp nhỉ").is_generative() is True


def test_a_generative_marker_wins_over_work_vocabulary() -> None:
    """ "Viết giúp tôi 5 bình luận" mentions work but is a writing request."""
    for marker in GENERATIVE_MARKERS[:5]:
        assert classify(f"{marker} 5 binh luan cho viec nay").intent is MemberIntent.GENERATIVE


def test_a_mixed_request_is_split_in_two() -> None:
    """ "Tôi đã đăng bài rồi, viết giúp tôi 5 comment."

    The operational half is handled and confirmed on its own; the generative
    half is offered separately so the Member chooses whether to spend a slot.
    """
    split = split_mixed("Tôi đã làm 5 bình luận, viết giúp tôi 5 comment nữa")
    assert split is not None
    operational, generative = split
    assert is_operational(operational)
    assert not is_operational(generative)


def test_a_purely_generative_message_is_not_split() -> None:
    assert split_mixed("Viết giúp tôi 5 bình luận tự nhiên") is None
