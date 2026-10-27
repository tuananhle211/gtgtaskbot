"""The names people read for each scheduled task.

The schedule itself lives in Celery's ``beat_schedule``
(:mod:`meobot.tasks.celery_app`); this only says what each task is, in
Vietnamese, and which group it sits in. A task scheduled without an entry here
still shows up, under its technical name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobLabel:
    label: str
    group: str
    description: str = ""


GROUP_SYSTEM = "Hệ thống"
GROUP_NOTIFICATIONS = "Thông báo"
GROUP_PR = "PR"
GROUP_GOOGLE = "Google Sheets / Drive"
GROUP_ADS = "Phòng Ads"

JOB_LABELS: dict[str, JobLabel] = {
    "system.periodic_heartbeat": JobLabel(
        "Nhịp tim hệ thống", GROUP_SYSTEM, "Kiểm tra worker và hàng đợi còn chạy."
    ),
    "conversations.cleanup_expired": JobLabel(
        "Dọn hội thoại hết hạn", GROUP_SYSTEM, "Xoá trạng thái hội thoại bot đã quá hạn."
    ),
    "cron.watchdog": JobLabel(
        "Canh tác vụ định kỳ", GROUP_SYSTEM, "Phát hiện tác vụ bị trễ, báo chủ sở hữu."
    ),
    "cron.purge_runs": JobLabel(
        "Dọn lịch sử tác vụ", GROUP_SYSTEM, "Xoá lịch sử chạy cũ theo hạn lưu."
    ),
    "scripts.review_pending": JobLabel(
        "Duyệt kịch bản đang chờ", GROUP_SYSTEM, "AI xem các kịch bản còn chờ review."
    ),
    "notifications.drain_outbox": JobLabel(
        "Gửi tin trong hàng đợi", GROUP_NOTIFICATIONS, "Gửi các tin Telegram đang chờ (outbox)."
    ),
    "notifications.recover_stale": JobLabel(
        "Gỡ tin gửi dở", GROUP_NOTIFICATIONS, "Trả lại hàng đợi các tin bị kẹt khi worker chết."
    ),
    "notifications.sweep_destination_health": JobLabel(
        "Kiểm tra nơi nhận", GROUP_NOTIFICATIONS, "Thử các nhóm Telegram còn nhận được tin không."
    ),
    "notifications.purge_deferred_guest_messages": JobLabel(
        "Dọn tin khách chờ duyệt", GROUP_NOTIFICATIONS, "Xoá tin của khách lạ đã quá hạn giữ."
    ),
    "reminders.sweep_due": JobLabel(
        "Gửi nhắc việc đến hạn", GROUP_NOTIFICATIONS, "Bắn các lời nhắc đã tới giờ."
    ),
    "sheets.sync_all_active_profiles": JobLabel(
        "Đồng bộ Google Sheets", GROUP_GOOGLE, "Đọc lại các sheet đang theo dõi."
    ),
    "drive.reconcile_created_spreadsheets": JobLabel(
        "Đối soát file Drive đã tạo", GROUP_GOOGLE, "Tìm file Drive tạo dở khi worker chết."
    ),
    "pr.sweep_content_work": JobLabel(
        "Ghi công việc PR", GROUP_PR, "Ghi công việc tự động từ nội dung PR."
    ),
    "pr.recover_stale_content_work": JobLabel(
        "Gỡ ghi công việc PR kẹt", GROUP_PR, "Chạy lại phần ghi công việc bị kẹt."
    ),
    "pr.sweep_channel_syncs": JobLabel(
        "Đồng bộ số liệu kênh PR", GROUP_PR, "Kéo số liệu các kênh đến hạn."
    ),
    "pr.release_stale_channel_syncs": JobLabel(
        "Gỡ đồng bộ kênh kẹt", GROUP_PR, "Mở khoá các lượt đồng bộ kênh bị kẹt."
    ),
    "pr.sweep_recurring_work": JobLabel(
        "Tạo công việc định kỳ PR", GROUP_PR, "Sinh các công việc lặp lại đến hạn."
    ),
    "pr.sweep_ai_review_runs": JobLabel(
        "AI review nội dung PR", GROUP_PR, "Chạy các lượt AI review đang chờ."
    ),
    "pr.recover_stale_ai_review_runs": JobLabel(
        "Gỡ AI review kẹt", GROUP_PR, "Trả lại các lượt AI review bị kẹt."
    ),
    "ads.sync_spend": JobLabel(
        "Đồng bộ chi phí Meta", GROUP_ADS, "Chi tiêu, mess, SĐT hôm qua từ các BM (08:00, 23:00)."
    ),
    "ads.sync_charges": JobLabel(
        "Đồng bộ hoá đơn Meta", GROUP_ADS, "Các lần Meta trừ tiền trong 5 ngày gần nhất."
    ),
    "pr.refresh_policy_sources": JobLabel(
        "Cập nhật chính sách nền tảng", GROUP_PR, "Tải lại chính sách TikTok / Meta / YouTube."
    ),
}


def label_for(task_name: str) -> JobLabel:
    return JOB_LABELS.get(task_name) or JobLabel(task_name, GROUP_SYSTEM)


__all__ = ["JOB_LABELS", "JobLabel", "label_for"]
