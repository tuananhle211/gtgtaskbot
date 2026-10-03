"""Stage names as the team says them.

Step 1F.2.3b moved this table out of ``tools/pr_presenters.py``, where it had
lived since Step 1D, for a plain layering reason: the notification service is in
the application layer and needs to write *"nội dung quay lại bước Chờ Trưởng
phòng duyệt"*, and importing a Telegram presenter from there pulled the entire
tool registry - and, through it, the service bundle - into a circular import.

The words are unchanged and ``tools.pr_presenters`` still exports them, so every
existing caller reads the same table. What moved is only where it is defined:
the domain, which has no dependencies to be circular with.

Display only. The enum value stays the machine-readable truth and is what every
service argument and every stored row uses; nothing here is ever parsed back.
"""

from __future__ import annotations

from meobot.domain.pr.channel_connections import (
    PrChannelConnectionState,
    PrChannelSyncStatus,
)
from meobot.domain.pr.channel_metrics import (
    PrChannelMetricsStatus,
    PrChannelPlatform,
)
from meobot.domain.pr.models import (
    PrContentDerivativeType,
    PrContentResourceType,
    PrContentType,
    PrPriority,
    PrWorkflowStage,
)
from meobot.domain.pr.reporting import PrMetricSource

STAGE_LABELS: dict[PrWorkflowStage, str] = {
    PrWorkflowStage.IDEA: "Ý tưởng",
    PrWorkflowStage.BRIEFING: "Brief",
    PrWorkflowStage.SCRIPTING: "Viết kịch bản",
    PrWorkflowStage.AI_REVIEW: "Chờ AI review",
    PrWorkflowStage.TEAM_LEAD_REVIEW: "Chờ Trưởng nhóm duyệt",
    PrWorkflowStage.HEAD_REVIEW: "Chờ Trưởng phòng duyệt",
    PrWorkflowStage.APPROVED: "Đã duyệt",
    PrWorkflowStage.PRODUCTION: "Đang sản xuất",
    PrWorkflowStage.INTERNAL_REVIEW: "Duyệt nội bộ",
    PrWorkflowStage.READY_TO_PUBLISH: "Sẵn sàng đăng",
    PrWorkflowStage.PUBLISHED: "Đã đăng",
    PrWorkflowStage.MEASURED: "Đã đo số liệu",
    PrWorkflowStage.ARCHIVED: "Lưu trữ",
    PrWorkflowStage.CANCELLED: "Đã huỷ",
}


def stage_label(stage: PrWorkflowStage) -> str:
    """The Vietnamese name of a stage, or its code if somebody adds one."""
    return STAGE_LABELS.get(stage, stage.value)


#: Step 1F.2.3d. Four words for four levels, and the words are the product
#: decision rather than a translation of the codes: *Ưu tiên* rather than "Cao"
#: because a person triaging a queue marks something as prioritised, not as
#: measuring high on a scale; *Rất gấp* rather than "Khẩn cấp" because it is the
#: same word as *Gấp* with more of it, which is what the level means.
PRIORITY_LABELS: dict[PrPriority, str] = {
    PrPriority.CRITICAL: "Rất gấp",
    PrPriority.URGENT: "Gấp",
    PrPriority.HIGH: "Ưu tiên",
    PrPriority.NORMAL: "Bình thường",
}


def priority_label(priority: PrPriority) -> str:
    """The Vietnamese name of a priority, or its code if somebody adds one."""
    return PRIORITY_LABELS.get(priority, priority.value)


#: Step 1F.2.3e. The six formats, in the order a person is offered them.
CONTENT_TYPE_LABELS: dict[PrContentType, str] = {
    PrContentType.ULTRA_SHORT_SCRIPT: "Kịch bản siêu ngắn",
    PrContentType.SHORT_VIDEO_SCRIPT: "Kịch bản video ngắn",
    PrContentType.FACEBOOK_POST: "Bài đăng Facebook",
    PrContentType.LONG_YOUTUBE_SCRIPT: "Kịch bản YouTube dài",
    PrContentType.PRESS_ARTICLE: "Báo chí",
    PrContentType.CORPORATE_TVC: "TVC doanh nghiệp",
}

#: What a content item with no type is called. Step 1F.2.3e made the column
#: required for new content and left historical rows ``NULL`` rather than
#: guessing at them, so this is a real state with a real name - not a placeholder
#: for missing data, and not an em dash.
UNCLASSIFIED_CONTENT_TYPE_LABEL = "Chưa phân loại"


def content_type_label(content_type: PrContentType | None) -> str:
    """The Vietnamese name of a content type, or *Chưa phân loại* for ``None``."""
    if content_type is None:
        return UNCLASSIFIED_CONTENT_TYPE_LABEL
    return CONTENT_TYPE_LABELS.get(content_type, content_type.value)


#: Step 1F.2.3e. The seven kinds of supporting material a reviewer might need.
RESOURCE_TYPE_LABELS: dict[PrContentResourceType, str] = {
    PrContentResourceType.REFERENCE: "Tài liệu tham khảo",
    PrContentResourceType.IMAGE: "Hình ảnh",
    PrContentResourceType.VIDEO: "Video tham khảo",
    PrContentResourceType.DRIVE_FILE: "File / Google Drive",
    PrContentResourceType.SOURCE: "Nguồn thông tin",
    PrContentResourceType.BRAND_ASSET: "Tài nguyên thương hiệu",
    PrContentResourceType.OTHER: "Khác",
}


def resource_type_label(resource_type: PrContentResourceType) -> str:
    """The Vietnamese name of a resource type, or its code if somebody adds one."""
    return RESOURCE_TYPE_LABELS.get(resource_type, resource_type.value)


#: Step 1F.2.3f. The six kinds of derivative production output.
#:
#: The words describe *how the file differs from the master* rather than where it
#: is bound for, matching the enum - see
#: :class:`~meobot.domain.pr.models.PrContentDerivativeType`. *Cắt ngắn* rather
#: than "Bản TikTok": the same 25-second cut is used on three platforms within a
#: month, and a label naming one of them would be wrong on the other two.
DERIVATIVE_TYPE_LABELS: dict[PrContentDerivativeType, str] = {
    PrContentDerivativeType.REMIX: "Remix",
    PrContentDerivativeType.CUTDOWN: "Cắt ngắn",
    PrContentDerivativeType.RECUT: "Cắt dựng lại",
    PrContentDerivativeType.REFORMAT: "Chuyển định dạng",
    PrContentDerivativeType.CAPTION_VARIANT: "Biến thể caption",
    PrContentDerivativeType.OTHER: "Khác",
}


def derivative_type_label(derivative_type: PrContentDerivativeType) -> str:
    """The Vietnamese name of a derivative type, or its code if somebody adds one."""
    return DERIVATIVE_TYPE_LABELS.get(derivative_type, derivative_type.value)


#: Step 1F.2.4a. The six canonical networks, as their own brands spell them.
#:
#: Five of the six are proper nouns and are written the way the companies write
#: them - *TikTok* with the capital K, not *Tiktok*. Only ``OTHER`` gets a
#: Vietnamese word, because it is the only one that is a category rather than a
#: name.
CHANNEL_PLATFORM_LABELS: dict[PrChannelPlatform, str] = {
    PrChannelPlatform.FACEBOOK: "Facebook",
    PrChannelPlatform.INSTAGRAM: "Instagram",
    PrChannelPlatform.TIKTOK: "TikTok",
    PrChannelPlatform.YOUTUBE: "YouTube",
    PrChannelPlatform.WEBSITE: "Website",
    PrChannelPlatform.OTHER: "Khác",
}

#: What a channel sitting on a platform outside the canonical six is called.
#:
#: A real state with a real name, exactly like ``UNCLASSIFIED_CONTENT_TYPE_LABEL``:
#: Step 1F.2.4a refused to guess a platform from a code, a name or a URL, so
#: *Chưa xác định* is the honest answer and the prompt to go and set one.
UNKNOWN_CHANNEL_PLATFORM_LABEL = "Chưa xác định"


def channel_platform_label(platform: PrChannelPlatform | None) -> str:
    """The name of a canonical platform, or *Chưa xác định* for ``None``."""
    if platform is None:
        return UNKNOWN_CHANNEL_PLATFORM_LABEL
    return CHANNEL_PLATFORM_LABELS.get(platform, platform.value)


#: Step 1F.2.4a. Where a channel's numbers come from, in words that promise
#: exactly as much as the system does.
#:
#: ``MANUAL`` is *Nhập thủ công* and never "Live" or "Đang theo dõi": the most
#: recent reading was typed by a person at whatever moment they typed it, and
#: the panel shows that moment beside it. ``CONNECTED_API`` has a label because
#: the vocabulary is complete; nothing in this step can produce it.
METRICS_STATUS_LABELS: dict[PrChannelMetricsStatus, str] = {
    PrChannelMetricsStatus.DISCONNECTED: "Chưa kết nối",
    PrChannelMetricsStatus.MANUAL: "Dữ liệu thủ công",
    PrChannelMetricsStatus.CONNECTED_API: "Đã kết nối API",
    # Step 1F.2.4b. Deliberately not a variant of "connected": the credential
    # has stopped working, the numbers stopped moving with it, and a person has
    # to do something. A green badge over three-week-old data would be the most
    # expensive thing this screen could say.
    PrChannelMetricsStatus.ACTION_REQUIRED: "Cần xác thực lại",
}


def metrics_status_label(status: PrChannelMetricsStatus) -> str:
    """The Vietnamese name of a data status, or its code if somebody adds one."""
    return METRICS_STATUS_LABELS.get(status, status.value)


#: Step 1F.2.4b. The connector's own state - about the **credential**, never
#: about the last run.
CONNECTION_STATE_LABELS: dict[PrChannelConnectionState, str] = {
    PrChannelConnectionState.CONNECTED: "Đã kết nối",
    PrChannelConnectionState.ACTION_REQUIRED: "Cần xác thực lại",
    PrChannelConnectionState.DISCONNECTED: "Chưa kết nối",
    # Step 1F.2.4c. Not a failure and not a success - a step the person is in
    # the middle of, so the words name the thing they have to do.
    PrChannelConnectionState.PENDING_SELECTION: "Chờ chọn tài khoản",
}


def connection_state_label(state: PrChannelConnectionState) -> str:
    """The Vietnamese name of a connection state."""
    return CONNECTION_STATE_LABELS.get(state, state.value)


#: Step 1F.2.4b. How the most recent attempt went, which is a different question
#: from whether the connection works - see
#: :class:`~meobot.domain.pr.channel_connections.PrChannelSyncStatus`.
SYNC_STATUS_LABELS: dict[PrChannelSyncStatus, str] = {
    PrChannelSyncStatus.NEVER_SYNCED: "Chưa đồng bộ lần nào",
    PrChannelSyncStatus.SYNCING: "Đang đồng bộ…",
    PrChannelSyncStatus.SUCCESS: "Đồng bộ thành công",
    PrChannelSyncStatus.FAILED: "Đồng bộ thất bại",
}


def sync_status_label(status: PrChannelSyncStatus) -> str:
    """The Vietnamese name of a sync outcome."""
    return SYNC_STATUS_LABELS.get(status, status.value)


#: Step 1F.2.4a. How one stored observation was obtained. Step 1B's
#: :class:`~meobot.domain.pr.reporting.PrMetricSource` had no words until a
#: screen needed to print them; the enum is unchanged.
METRIC_SOURCE_LABELS: dict[PrMetricSource, str] = {
    PrMetricSource.API: "Tự động từ nền tảng",
    PrMetricSource.MANUAL: "Nhập thủ công",
    PrMetricSource.IMPORT: "Nhập từ tệp",
}


def metric_source_label(source: PrMetricSource) -> str:
    """The Vietnamese name of a metric source, or its code if somebody adds one."""
    return METRIC_SOURCE_LABELS.get(source, source.value)


#: Step 1F.2.9. What each TikTok scope lets MeoChat read, in words a person can
#: check against what is on the screen.
#:
#: The keys are the scope strings themselves, written out here rather than
#: imported from
#: :data:`~meobot.integrations.tiktok.constants.TIKTOK_SCOPES`: the domain layer
#: does not import an integration, and a label table that did would invert the
#: dependency for four literals. What keeps the two in step is a test asserting
#: this mapping covers exactly the scopes the connector asks for - which is the
#: only property that actually matters, and it fails loudly when somebody adds a
#: fifth.
#:
#: The pairing is the point of the whole panel. A TikTok reviewer reading
#: "Thống kê tài khoản" next to four follower numbers can see that
#: ``user.info.stats`` is used for what it was asked for, which is the evidence
#: app review is looking for and which a bare list of scope strings does not
#: provide.
TIKTOK_SCOPE_LABELS: dict[str, tuple[str, str]] = {
    "user.info.basic": (
        "Thông tin cơ bản",
        "Ảnh đại diện, tên hiển thị và mã tài khoản TikTok.",
    ),
    "user.info.profile": (
        "Hồ sơ TikTok",
        "Tên người dùng, liên kết hồ sơ, tiểu sử và trạng thái xác minh.",
    ),
    "user.info.stats": (
        "Thống kê tài khoản",
        "Số người theo dõi, số đang theo dõi, tổng lượt thích và số video.",
    ),
    "video.list": (
        "Danh sách video công khai",
        "Các video công khai gần đây và chỉ số của từng video.",
    ),
}


def tiktok_scope_label(scope: str) -> str:
    """The Vietnamese name of a TikTok scope, or the scope string itself.

    Falling back to the raw string rather than to "Khác" on purpose: a scope
    nobody has written a label for is still something a person consented to, and
    hiding its name behind a generic word would make the permissions list less
    honest than the consent screen it describes.
    """
    return TIKTOK_SCOPE_LABELS.get(scope, (scope, ""))[0]


def tiktok_scope_description(scope: str) -> str:
    """What a TikTok scope lets MeoChat read, or an empty string."""
    return TIKTOK_SCOPE_LABELS.get(scope, (scope, ""))[1]


#: Step 1F.2.9. Why a field is not on the screen, in a person's words.
#:
#: The five verdicts the connector already distinguishes - see
#: :data:`~meobot.integrations.tiktok.provider.FIELD_AVAILABLE` - plus
#: ``not_read`` for a group a particular call never asked for. They are kept
#: apart because they need different actions, and collapsing them into "không có
#: dữ liệu" is exactly the thing the whole degradation mechanism exists to
#: avoid: an account with no videos and an app with no ``video.list`` approval
#: produce the same blank card and want opposite sentences under it.
FIELD_AVAILABILITY_LABELS: dict[str, str] = {
    "available": "Đã lấy được",
    "empty": "TikTok trả về rỗng",
    "not_returned": "TikTok không trả về trường này",
    "not_permitted": "Chưa được cấp quyền",
    "unsupported": "Phiên bản API không hỗ trợ",
    "not_read": "Chưa đọc trong lần này",
}


def field_availability_label(word: str) -> str:
    """The Vietnamese sentence for one availability word, or the word itself."""
    return FIELD_AVAILABILITY_LABELS.get(word, word)


__all__: list[str] = [
    "CHANNEL_PLATFORM_LABELS",
    "CONNECTION_STATE_LABELS",
    "CONTENT_TYPE_LABELS",
    "DERIVATIVE_TYPE_LABELS",
    "FIELD_AVAILABILITY_LABELS",
    "METRICS_STATUS_LABELS",
    "METRIC_SOURCE_LABELS",
    "PRIORITY_LABELS",
    "RESOURCE_TYPE_LABELS",
    "STAGE_LABELS",
    "SYNC_STATUS_LABELS",
    "TIKTOK_SCOPE_LABELS",
    "UNCLASSIFIED_CONTENT_TYPE_LABEL",
    "UNKNOWN_CHANNEL_PLATFORM_LABEL",
    "channel_platform_label",
    "connection_state_label",
    "content_type_label",
    "derivative_type_label",
    "field_availability_label",
    "metric_source_label",
    "metrics_status_label",
    "priority_label",
    "resource_type_label",
    "stage_label",
    "sync_status_label",
    "tiktok_scope_description",
    "tiktok_scope_label",
]
