"""Integration fakes, retry boundary and the publish guard."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from meobot.core.errors import (
    IntegrationAuthError,
    IntegrationNotConfiguredError,
    IntegrationRateLimitError,
    IntegrationTimeoutError,
    WorkflowStateError,
)
from meobot.domain.videos.workflow import VideoStatus
from meobot.integrations.base import IntegrationConfig, build_idempotency_key, call_with_retries
from meobot.integrations.google.drive import FakeDriveClient, NotConfiguredDriveClient
from meobot.integrations.google.sheets import FakeSheetsClient, SheetRange
from meobot.integrations.meta.metrics import FakeFacebookMetricsClient, PostMetrics
from meobot.integrations.meta.publisher import (
    FakeFacebookPublisher,
    NotConfiguredFacebookPublisher,
    PublishRequest,
)
from meobot.integrations.tiktok.metrics import FakeTikTokMetricsClient, TikTokVideoMetrics

CONFIG = IntegrationConfig(
    provider="test",
    timeout_seconds=0.2,
    max_retries=2,
    backoff_base_seconds=0.001,
)


# --- Retry boundary ---------------------------------------------------------
async def test_successful_call_is_not_retried() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await call_with_retries(operation, config=CONFIG, operation_name="op") == "ok"
    assert calls == 1


async def test_transient_error_is_retried_then_succeeds() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IntegrationRateLimitError("slow down", provider="test")
        return "ok"

    assert await call_with_retries(operation, config=CONFIG, operation_name="op") == "ok"
    assert calls == 2


async def test_transient_error_gives_up_after_the_budget() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise IntegrationRateLimitError("slow down", provider="test")

    with pytest.raises(IntegrationRateLimitError):
        await call_with_retries(operation, config=CONFIG, operation_name="op")
    assert calls == CONFIG.max_retries + 1


async def test_permanent_error_is_not_retried() -> None:
    """Auth failures will not fix themselves; retrying only burns time."""
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise IntegrationAuthError("bad credentials", provider="test")

    with pytest.raises(IntegrationAuthError):
        await call_with_retries(operation, config=CONFIG, operation_name="op")
    assert calls == 1


async def test_timeout_is_enforced() -> None:
    async def operation() -> str:
        await asyncio.sleep(5)
        return "never"

    with pytest.raises(IntegrationTimeoutError):
        await call_with_retries(operation, config=CONFIG, operation_name="op")


def test_idempotency_key_is_deterministic() -> None:
    first = build_idempotency_key("publish", "video-1", "page-9", 3)
    second = build_idempotency_key("publish", "video-1", "page-9", 3)
    other = build_idempotency_key("publish", "video-1", "page-9", 4)
    assert first == second
    assert first != other


# --- Google -----------------------------------------------------------------
async def test_fake_sheets_client_returns_keyed_rows() -> None:
    client = FakeSheetsClient()
    client.load(
        "sheet-a",
        "Kịch bản",
        ["Mã KB", "Tiêu đề"],
        [["TT-01", "Tiêu đề 1"], ["TT-02", "Tiêu đề 2"]],
    )
    target = SheetRange(spreadsheet_id="sheet-a", sheet_name="Kịch bản")

    headers = await client.read_headers(target)
    rows = await client.read_rows(target)

    assert headers == ["Mã KB", "Tiêu đề"]
    assert rows[0] == {"Mã KB": "TT-01", "Tiêu đề": "Tiêu đề 1"}


async def test_fake_sheets_pads_short_rows() -> None:
    client = FakeSheetsClient()
    client.load("s", "t", ["a", "b", "c"], [["1"]])
    rows = await client.read_rows(SheetRange(spreadsheet_id="s", sheet_name="t"))
    assert rows[0] == {"a": "1", "b": "", "c": ""}


async def test_unconfigured_google_client_fails_loudly() -> None:
    """Missing credentials must raise, not silently return nothing."""
    from meobot.integrations.google.sheets import NotConfiguredSheetsClient

    with pytest.raises(IntegrationNotConfiguredError):
        await NotConfiguredSheetsClient().read_rows(SheetRange(spreadsheet_id="s", sheet_name="t"))


async def test_fake_drive_is_idempotent() -> None:
    client = FakeDriveClient()
    first = await client.create_spreadsheet("Báo cáo tuần", idempotency_key="report:2026-W31")
    second = await client.create_spreadsheet("Báo cáo tuần", idempotency_key="report:2026-W31")
    assert first.file_id == second.file_id
    assert len(client.files) == 1


async def test_unconfigured_drive_fails_loudly() -> None:
    with pytest.raises(IntegrationNotConfiguredError):
        await NotConfiguredDriveClient().create_spreadsheet("x")


# --- Meta -------------------------------------------------------------------
def publish_request(status: VideoStatus, key: str = "k1") -> PublishRequest:
    return PublishRequest(
        page_id="page-1",
        video_id="video-1",
        caption="Nội dung",
        media_url="https://example.invalid/v.mp4",
        video_status=status,
        idempotency_key=key,
    )


async def test_publisher_refuses_unapproved_video() -> None:
    """The publisher re-checks approval even though policy already did."""
    publisher = FakeFacebookPublisher()
    with pytest.raises(WorkflowStateError):
        await publisher.publish_video(publish_request(VideoStatus.EDITING))
    assert publisher.published == {}


async def test_publisher_publishes_approved_video() -> None:
    publisher = FakeFacebookPublisher()
    result = await publisher.publish_video(publish_request(VideoStatus.APPROVED_FOR_PUBLISH))
    assert result.post_id
    assert result.was_duplicate is False


async def test_publishing_twice_with_the_same_key_does_not_duplicate() -> None:
    publisher = FakeFacebookPublisher()
    first = await publisher.publish_video(publish_request(VideoStatus.APPROVED_FOR_PUBLISH))
    second = await publisher.publish_video(publish_request(VideoStatus.APPROVED_FOR_PUBLISH))
    assert second.post_id == first.post_id
    assert second.was_duplicate is True


async def test_unconfigured_publisher_fails_loudly() -> None:
    with pytest.raises(IntegrationNotConfiguredError):
        await NotConfiguredFacebookPublisher().publish_video(
            publish_request(VideoStatus.APPROVED_FOR_PUBLISH)
        )


async def test_fake_facebook_metrics() -> None:
    client = FakeFacebookMetricsClient()
    client.snapshots["p1"] = PostMetrics(
        post_id="p1", page_id="page-1", captured_on=date(2026, 7, 28), reach=100
    )
    client.page_posts["page-1"] = ["p1"]

    ids = await client.fetch_page_post_ids(
        "page-1", since=date(2026, 7, 1), until=date(2026, 7, 31)
    )
    metrics = await client.fetch_post_metrics("page-1", ids)

    assert [m.reach for m in metrics] == [100]


# --- TikTok -----------------------------------------------------------------
async def test_fake_tiktok_metrics() -> None:
    client = FakeTikTokMetricsClient()
    client.snapshots["v1"] = TikTokVideoMetrics(
        video_id="v1", account_id="acc", captured_on=date(2026, 7, 28), views=2500
    )
    client.account_videos["acc"] = ["v1"]

    ids = await client.fetch_account_video_ids(
        "acc", since=date(2026, 7, 1), until=date(2026, 7, 31)
    )
    metrics = await client.fetch_video_metrics("acc", ids)

    assert [m.views for m in metrics] == [2500]
