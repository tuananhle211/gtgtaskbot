"""What a reply to an open draft means, and how a long announcement is cut up.

Both are pure functions, so they are tested here without a Dispatcher, a
database or a Telegram transport. That matters: the reported defects were
*routing* problems, but the reason they could not be fixed by routing alone is
that "Tất cả" and "Xác nhận" had nowhere to be understood. These are the
understanding.

The splitter tests are dominated by one property - **nothing is lost** - and it
is asserted directly rather than by eyeballing part boundaries, because the
0.6.0a2 behaviour it replaces (truncate at 3000 characters) would pass every
test about boundaries and still drop half of somebody's announcement.
"""

from __future__ import annotations

import pytest

from meobot.domain.dispatch.phrases import (
    AudienceScope,
    ContinuationKind,
    read_audience_scope,
    read_continuation,
    read_exclusions,
    read_names,
)
from meobot.domain.dispatch.privacy import classify_announcement
from meobot.domain.dispatch.splitting import (
    DEFAULT_PART_LIMIT,
    marker_for,
    rendered_length,
    split_announcement,
)
from meobot.domain.notifications.models import PrivacyClassification


class TestConfirmation:
    """ "Xác nhận" is a confirmation. It was answered as conversation."""

    @pytest.mark.parametrize(
        "text",
        [
            "Xác nhận",
            "xac nhan",
            "Đúng",
            "Đúng rồi",
            "Gửi",
            "Gửi đi",
            "Ok",
            "Ok gửi nhé",
            "Chốt",
            "Thực hiện",
            "Đồng ý",
            "OK gửi luôn đi MeoBot",
            "Xác nhận nhé em",
        ],
    )
    def test_every_documented_confirmation_confirms(self, text: str) -> None:
        assert read_continuation(text, candidate_count=3).kind is ContinuationKind.CONFIRM

    @pytest.mark.parametrize(
        "text",
        [
            "Gửi cho group Test",
            "Gửi vào các group Nội dung",
            "Gửi tất cả",
            "Huỷ",
            "Bỏ Test",
        ],
    )
    def test_a_sentence_that_names_recipients_is_not_a_confirmation(self, text: str) -> None:
        """ "Gửi" alone confirms; "Gửi cho group Test" chooses. The difference is
        the words that follow, and reading the second as the first would send an
        announcement to a list nobody checked."""
        assert read_continuation(text, candidate_count=3).kind is not ContinuationKind.CONFIRM


class TestSelectAll:
    """ "Tất cả" expands. It was answered with "thao tác gì với các group này?"."""

    @pytest.mark.parametrize(
        "text",
        [
            "Tất cả",
            "tat ca",
            "Gửi tất cả",
            "Toàn bộ",
            "Tất cả group trên",
            "Gửi cho tất cả",
            "Mọi group",
        ],
    )
    def test_every_documented_phrase_selects_all(self, text: str) -> None:
        assert read_continuation(text, candidate_count=3).kind is ContinuationKind.SELECT_ALL

    def test_ca_ba_selects_all_when_three_are_on_the_card(self) -> None:
        assert read_continuation("Cả ba", candidate_count=3).kind is ContinuationKind.SELECT_ALL

    def test_ca_hai_selects_all_when_two_are_on_the_card(self) -> None:
        assert read_continuation("Cả hai", candidate_count=2).kind is ContinuationKind.SELECT_ALL

    def test_ca_ba_is_not_all_when_four_are_on_the_card(self) -> None:
        """A count that does not match the card names a *subset*, not the lot.

        Reading it as "all" would send to a fourth group nobody mentioned.
        """
        reading = read_continuation("Cả ba", candidate_count=4)
        assert reading.kind is not ContinuationKind.SELECT_ALL

    def test_all_except_keeps_both_halves(self) -> None:
        reading = read_continuation("Tất cả trừ group Test", candidate_count=3)
        assert reading.kind is ContinuationKind.SELECT_ALL
        assert reading.excluded == ("test",)


class TestCountingOffTheCard:
    """ "Hai group đầu", "ba group trên", "group 1 và 3"."""

    def test_the_first_two(self) -> None:
        reading = read_continuation("Hai group đầu", candidate_count=3)
        assert reading.kind is ContinuationKind.SELECT_INDICES
        assert reading.indices == (1, 2)

    def test_the_three_just_shown(self) -> None:
        reading = read_continuation("Ba group trên", candidate_count=3)
        assert reading.indices == (1, 2, 3)

    def test_the_three_just_shown_by_another_name(self) -> None:
        reading = read_continuation("Gửi cho ba group vừa hiện", candidate_count=3)
        assert reading.indices == (1, 2, 3)

    def test_the_last_one(self) -> None:
        reading = read_continuation("Group cuối", candidate_count=3)
        assert reading.indices == (3,)

    def test_explicit_positions(self) -> None:
        reading = read_continuation("Group 1 và 3", candidate_count=3)
        assert reading.kind is ContinuationKind.SELECT_INDICES
        assert reading.indices == (1, 3)

    def test_a_position_past_the_end_is_ignored(self) -> None:
        """A card with three groups has no fifth row to count off."""
        reading = read_continuation("Group 1 và 5", candidate_count=3)
        assert reading.indices == (1,)


class TestSubsets:
    """Adding, removing and narrowing, by name."""

    def test_removing_one(self) -> None:
        reading = read_continuation("Bỏ Test", candidate_count=3)
        assert reading.kind is ContinuationKind.EXCLUDE_NAMES
        assert reading.excluded == ("test",)

    def test_removing_one_by_saying_do_not_send_it(self) -> None:
        reading = read_continuation("Không gửi Kết bạn", candidate_count=3)
        assert reading.kind is ContinuationKind.EXCLUDE_NAMES
        assert reading.excluded == ("ket ban",)

    def test_narrowing_to_one(self) -> None:
        reading = read_continuation("Chỉ Saykeng", candidate_count=3)
        assert reading.kind is ContinuationKind.ONLY_NAMES
        assert reading.names == ("saykeng",)

    def test_adding_one(self) -> None:
        reading = read_continuation("Thêm group Test", candidate_count=2)
        assert reading.kind is ContinuationKind.ADD_NAMES
        assert reading.names == ("test",)

    def test_two_names_joined_by_voi(self) -> None:
        reading = read_continuation("Saykeng với Test", candidate_count=3)
        assert reading.kind is ContinuationKind.SELECT_NAMES
        assert reading.names == ("saykeng", "test")


class TestCancelling:
    @pytest.mark.parametrize(
        "text",
        ["Huỷ", "huy", "Thôi", "Không gửi nữa", "Bỏ thông báo này", "thoi khong gui nua"],
    )
    def test_every_documented_phrase_cancels(self, text: str) -> None:
        assert read_continuation(text, candidate_count=3).kind is ContinuationKind.CANCEL

    def test_cancelling_wins_over_a_name(self) -> None:
        """Somebody backing out must never have their words read as a choice."""
        assert (
            read_continuation("Thôi không gửi nữa, bỏ group Test", candidate_count=3).kind
            is ContinuationKind.CANCEL
        )


class TestNameReading:
    """Names come back folded, with the verb and the word "group" removed."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Gửi cho group Test và group Saykeng", ("test", "saykeng")),
            ("Gửi thông báo này vào các group content", ("content",)),
            ("Gửi cho các group báo cáo và chấm công", ("bao cao", "cham cong")),
            ("Gửi cho các nhóm seeding", ("seeding",)),
            ("Gửi cho các group Bác Tiến", ("bac tien",)),
        ],
    )
    def test_names_are_read_without_the_addressing(
        self, text: str, expected: tuple[str, ...]
    ) -> None:
        assert read_names(text) == expected


class TestWholeRegistryPhrases:
    @pytest.mark.parametrize(
        ("text", "scope"),
        [
            ("Gửi cho tất cả group.", AudienceScope.ALL_REGISTERED),
            ("Gửi vào toàn bộ group đã đăng ký.", AudienceScope.ALL_REGISTERED),
            ("Gửi cho các group đang hoạt động.", AudienceScope.ACTIVE_ONLY),
            ("Đăng nội dung này vào mọi group tôi quản lý.", AudienceScope.MANAGED_BY_ME),
            ("Gửi cho group vừa đăng ký.", AudienceScope.MOST_RECENT),
        ],
    )
    def test_each_documented_phrase_names_a_scope(self, text: str, scope: AudienceScope) -> None:
        assert read_audience_scope(text) is scope

    def test_naming_a_group_is_not_a_scope(self) -> None:
        assert read_audience_scope("Gửi cho group Test") is None

    def test_an_exclusion_is_read_out_of_the_same_sentence(self) -> None:
        assert read_exclusions("Gửi cho tất cả group trừ group Test.") == ("test",)


class TestPrivacy:
    """A general announcement goes. A payslip does not."""

    @pytest.mark.parametrize(
        "content",
        [
            "Chào mọi người, mình là MeoBot - trợ lý của Phòng PR Truyền thông.",
            "Sáng mai họp lúc 9 giờ ở phòng lớn nhé cả nhà.",
            # Deliberately close to a marker without being one: a schedule
            # announcement about payday is exactly what a department says.
            "Lịch trả lương tháng này là ngày 5, mọi người lưu ý nhé.",
        ],
    )
    def test_an_ordinary_announcement_may_reach_a_group(self, content: str) -> None:
        assert classify_announcement(content).may_reach_a_group

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("Bảng lương của Linh tháng này là 15 triệu.", PrivacyClassification.PERSONAL_PRIVATE),
            ("Số tài khoản của anh là 0123456789.", PrivacyClassification.PERSONAL_PRIVATE),
            ("Mật khẩu wifi mới là abc123.", PrivacyClassification.SECRET),
        ],
    )
    def test_private_content_is_classified_and_kept_out_of_groups(
        self, content: str, expected: PrivacyClassification
    ) -> None:
        verdict = classify_announcement(content)
        assert verdict.classification is expected
        assert not verdict.may_reach_a_group


# --- Long messages ----------------------------------------------------------
PARAGRAPH = (
    "Chào mọi người, mình là MeoBot - trợ lý hỗ trợ điều hành và sáng tạo nội "
    "dung cho Phòng PR Truyền thông Apexmed. Mình có thể hỗ trợ brainstorm, "
    "review kịch bản, tổng hợp báo cáo, quản lý nội dung và nhắc việc."
)


def _letters(text: str) -> str:
    """Everything that is not whitespace, so a boundary cannot hide a loss."""
    return "".join(text.split())


class TestSplitting:
    def test_a_short_announcement_is_one_part_with_no_marker(self) -> None:
        parts = split_announcement("Chào buổi sáng.")
        assert len(parts) == 1
        assert parts[0].total == 1
        assert marker_for(parts[0].number, parts[0].total) == ""

    def test_a_message_just_under_the_limit_stays_whole(self) -> None:
        body = "a" * (DEFAULT_PART_LIMIT - 10)
        assert len(split_announcement(body)) == 1

    def test_a_message_over_the_limit_is_split(self) -> None:
        body = "\n\n".join([PARAGRAPH] * 40)
        parts = split_announcement(body)
        assert len(parts) > 1

    def test_nothing_is_ever_dropped(self) -> None:
        """The behaviour this replaces truncated at 3000 characters, silently."""
        body = "\n\n".join(f"Đoạn {index}. {PARAGRAPH}" for index in range(40))
        parts = split_announcement(body)
        assert _letters("".join(part.content for part in parts)) == _letters(body)

    def test_every_part_fits_telegram_once_escaped(self) -> None:
        body = "\n\n".join([f"{PARAGRAPH} <b>&</b>"] * 60)
        for part in split_announcement(body):
            assert rendered_length(part.content) <= DEFAULT_PART_LIMIT

    def test_parts_are_numbered_in_order(self) -> None:
        parts = split_announcement("\n\n".join([PARAGRAPH] * 40))
        assert [part.number for part in parts] == list(range(1, len(parts) + 1))
        assert {part.total for part in parts} == {len(parts)}

    def test_splitting_prefers_paragraph_boundaries(self) -> None:
        """A boundary between paragraphs is invisible; one mid-word is not."""
        body = "\n\n".join([PARAGRAPH] * 40)
        for part in split_announcement(body):
            assert part.content.startswith("Chào mọi người")

    def test_a_paragraph_too_long_to_fit_falls_back_to_sentences(self) -> None:
        sentence = "Đây là một câu dài vừa phải để kiểm tra việc cắt theo câu. "
        body = sentence * 200
        parts = split_announcement(body)
        assert len(parts) > 1
        assert _letters("".join(part.content for part in parts)) == _letters(body)
        for part in parts:
            assert part.content.rstrip().endswith(".")

    def test_one_unpunctuated_wall_of_text_still_loses_nothing(self) -> None:
        body = "x" * (DEFAULT_PART_LIMIT * 3)
        parts = split_announcement(body)
        assert len(parts) >= 3
        assert "".join(part.content for part in parts) == body

    def test_multibyte_expansion_is_measured_not_guessed(self) -> None:
        """A body of ampersands is five times longer once escaped.

        A splitter that measured ``len`` would produce parts Telegram refuses,
        which is the failure this module exists to remove.
        """
        body = "&" * (DEFAULT_PART_LIMIT * 2)
        for part in split_announcement(body):
            assert rendered_length(part.content) <= DEFAULT_PART_LIMIT

    def test_an_empty_announcement_produces_no_parts(self) -> None:
        assert split_announcement("   \n  ") == ()

    def test_markers_only_appear_when_there_is_more_than_one_part(self) -> None:
        assert marker_for(1, 1) == ""
        assert marker_for(2, 3) == "(2/3)"
