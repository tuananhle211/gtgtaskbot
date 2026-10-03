"""The README and the guides must not contradict the code, or each other.

Documentation drift is not a cosmetic problem here. The 0.6.0a1 README said HR
did not exist while the HR module was shipping, and said the worker ran three
queues while the outbox needed a fourth that nobody was consuming. Both were
readable in the repository the whole time; neither was checked.

These tests check the handful of claims that are cheap to verify and expensive
to get wrong: the version, the migration head, the queue list, and whether a
feature is described as both present and absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = ROOT / "docs"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


def test_the_readme_names_the_package_version(readme: str) -> None:
    from meobot import __version__

    assert f"Phiên bản hiện tại: **{__version__}" in readme


def test_the_readme_names_the_real_alembic_head(readme: str) -> None:
    """The head is read from the filesystem, not from a number in a sentence."""
    revisions = {
        match.group(1)
        for path in (ROOT / "alembic" / "versions").glob("*.py")
        for match in [re.search(r'^revision: str = "(\d+)"', path.read_text("utf-8"), re.M)]
        if match
    }
    down_pattern = re.compile(r'^down_revision: str \| None = "(\d+)"', re.M)
    down = {
        match.group(1)
        for path in (ROOT / "alembic" / "versions").glob("*.py")
        for match in [down_pattern.search(path.read_text("utf-8"))]
        if match
    }
    heads = revisions - down
    assert len(heads) == 1, f"expected one head, found {heads}"
    head = heads.pop()

    assert f"head hiện tại là **`{head}`**" in readme
    assert f"Có **{len(revisions)} migration** sẵn" in readme


def test_the_readme_lists_the_queues_the_worker_actually_runs(readme: str) -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    queue_line = next(
        line.strip() for line in compose.splitlines() if line.strip().startswith("- q_default")
    )
    queues = queue_line.removeprefix("- ").split(",")
    assert "q_notifications" in queues

    for queue in queues:
        assert queue in readme, f"{queue} is not mentioned in the README"
    # And the service table must show the same list, not a shorter one.
    assert ",".join(queues) in readme


def test_the_readme_does_not_say_hr_is_missing(readme: str) -> None:
    """HR shipped in 0.6.0A. The status table said otherwise until 0.6.0a2."""
    status_rows = [line for line in readme.splitlines() if line.startswith("|")]
    for row in status_rows:
        if "Chưa có" in row and "HR" in row:
            pytest.fail(f"the status table still calls HR missing: {row.strip()}")


def test_the_readme_does_not_say_reminders_are_a_placeholder(readme: str) -> None:
    lowered = readme.lower()
    for contradiction in (
        "lịch nhắc chưa được xây",
        "lịch nhắc là placeholder",
        "reminders/          # placeholder",
    ):
        assert contradiction not in lowered


def test_the_multi_group_guide_opens_with_the_promised_sentence() -> None:
    """The spec fixed this opening, because it sets the expectation correctly.

    Somebody who believes they must type an exact Telegram group title will not
    try "group idea" - so the first sentence has to say they need not.
    """
    guide = (DOCS / "HUONG_DAN_DANG_KY_GROUP_VA_GUI_NHIEU_NOI.md").read_text(encoding="utf-8")
    assert "**Bạn không cần nhớ chính xác tên group." in guide
    assert "MeoBot sẽ tìm các group phù hợp và cho bạn kiểm tra danh sách trước khi gửi.**" in guide


def test_the_reminder_guide_opens_with_the_promised_sentence() -> None:
    """The spec fixed this opening, because it sets the expectation correctly."""
    guide = (DOCS / "HUONG_DAN_LICH_NHAC.md").read_text(encoding="utf-8")
    assert "**Bạn chỉ cần nhắn MeoBot thời gian và nội dung cần nhắc." in guide
    assert "MeoBot sẽ cho bạn kiểm tra lại trước khi tạo lịch.**" in guide


@pytest.mark.parametrize(
    "name",
    [
        "HUONG_DAN_MEMBER.md",
        "HUONG_DAN_DIEU_PHOI_THONG_BAO.md",
        "HUONG_DAN_LICH_NHAC.md",
        "HUONG_DAN_DANG_KY_GROUP_VA_GUI_NHIEU_NOI.md",
    ],
)
def test_user_guides_contain_no_internal_vocabulary(name: str) -> None:
    """A guide is for somebody who does not read Python.

    No enum values, no class names, no table names, no UUIDs. The one allowed
    exception is a cross-reference to another guide by filename.
    """
    text = (DOCS / name).read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if "HUONG_DAN" not in line)

    forbidden = [
        "PENDING_APPROVAL",
        "AUTHORIZED_ONCE",
        "OutboxStatus",
        "NotificationRouter",
        "ReminderService",
        "outbound_messages",
        "reminder_occurrences",
        "telegram_chat_assignments",
        "deferred_guest_messages",
        "PrivacyClassification",
        "EMPLOYEE",
        "TEAM_LEAD",
    ]
    for token in forbidden:
        assert token not in body, f"{name} leaks internal vocabulary: {token}"

    # No bare UUIDs either.
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", body), name


@pytest.mark.parametrize(
    "name",
    [
        "HUONG_DAN_MEMBER.md",
        "HUONG_DAN_DIEU_PHOI_THONG_BAO.md",
        "HUONG_DAN_LICH_NHAC.md",
        "HUONG_DAN_DANG_KY_GROUP_VA_GUI_NHIEU_NOI.md",
    ],
)
def test_user_guides_use_the_visible_role_labels(name: str) -> None:
    from meobot.domain.identity.labels import ROLE_LABELS
    from meobot.domain.identity.models import Role

    text = (DOCS / name).read_text(encoding="utf-8")
    if "vai trò" not in text.lower() and ROLE_LABELS[Role.OWNER] not in text:
        pytest.skip("this guide does not discuss roles")
    for role in Role:
        assert role.value not in text, f"{name} shows the internal role name {role.value}"


def test_the_unfinished_list_is_the_same_in_code_and_in_the_guide() -> None:
    """A feature declared unfinished in one place and shipped in another is a lie.

    Checked against the capability registry rather than a second written list,
    because the registry is what MeoBot actually tells a user.
    """
    from meobot.application.capability_service import NOT_IMPLEMENTED

    guide = (DOCS / "HUONG_DAN_DIEU_PHOI_THONG_BAO.md").read_text(encoding="utf-8").lower()
    unfinished_section = guide.split("những việc đang được hoàn thiện")[1]

    joined = " ".join(NOT_IMPLEMENTED).lower()
    for topic in ("giao việc", "seeding", "quản lý kênh", "mạng xã hội"):
        assert topic in joined, f"the capability registry no longer lists {topic}"
        assert topic in unfinished_section, f"the guide no longer lists {topic}"


def test_the_smoke_checklist_exists_and_is_not_called_done(readme: str) -> None:
    """The release must not describe itself as production-ready."""
    assert "Checklist smoke test Telegram thật cho 0.6.0a2" in readme
    assert "Đừng gọi bản này là production-ready" in readme


def test_the_multi_group_release_has_its_own_smoke_checklist(readme: str) -> None:
    """Automated tests never touch Telegram, so something has to say so.

    Every one of the four reported defects was found by a person using the real
    bot and none of them by a test - which is the argument for this section
    existing, and for it being a condition rather than a suggestion.
    """
    assert "### Kiểm thử thật trên Telegram (0.6.0a3)" in readme
    section = readme.split("### Kiểm thử thật trên Telegram (0.6.0a3)")[1]
    assert "production-ready" in section
    for step in ("Tất cả", "Xác nhận", "Thử lại nơi lỗi", "đúng thứ tự"):
        assert step in section, f"the checklist does not cover {step}"


def test_every_new_setting_is_documented_in_env_example() -> None:
    """A setting nobody can find is a setting nobody will set."""
    from meobot.core.config import Settings

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    new_prefixes = ("reminder_", "announcement_", "deferred_guest_", "notification_")
    missing = [
        name.upper()
        for name in Settings.model_fields
        if name.startswith(new_prefixes) and name.upper() not in env_example
    ]
    assert missing == [], f"undocumented settings: {missing}"
