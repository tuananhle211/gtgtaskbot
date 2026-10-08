"""The one place a PR service error becomes a Vietnamese sentence.

The PR services speak structural English on purpose - Step 1C made them
transport-agnostic because Telegram is client #1 and a web admin UI is client
#2, and the wording a human reads belongs to whichever of them is talking. That
decision is only worth anything if the translation happens **once**, at the
transport boundary, rather than as a ``try/except`` beside every tool.

This module is that boundary. :func:`pr_errors` wraps a tool body; any
:class:`~meobot.core.errors.MeoBotError` a PR service raises comes back out as
a :class:`~meobot.core.errors.ToolExecutionError` whose ``message`` is Vietnamese
and whose ``details`` still carry the original ``code`` - so the audit trail and
the logs keep the machine-readable fact while the person gets a sentence.

Translation is keyed on :attr:`~meobot.core.errors.MeoBotError.code`, never on
message text. An error whose code is unknown here is reported as a neutral
refusal rather than by leaking English internals: the fallback is deliberately
useless-but-safe, because the alternative - passing the raw message through -
would look like it worked until the day a service message mentioned a column
name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from meobot.core.errors import MeoBotError, ToolExecutionError
from meobot.core.logging import get_logger

logger = get_logger(__name__)

#: What a person is told when the code is one nobody has written a sentence
#: for. Neutral on purpose - see the module docstring.
FALLBACK = "Thao tác không thực hiện được. Bạn kiểm tra lại thông tin giúp mình nhé."


def _stale_version(details: Mapping[str, Any]) -> str:
    current = details.get("current_version")
    expected = details.get("expected_version")
    return (
        "Nội dung này vừa được cập nhật. "
        f"Phiên bản hiện tại là v{current}; thao tác của bạn dựa trên v{expected}. "
        "Bạn xem lại bản mới nhất rồi thao tác lại nhé."
    )


def _review_version_mismatch(details: Mapping[str, Any]) -> str:
    current = details.get("current_version")
    reviewed = details.get("version_reviewed") or details.get("reviewed_version")
    return (
        f"Bản duyệt này thuộc về v{reviewed}, nhưng nội dung hiện đã ở v{current}. "
        "Mỗi lần duyệt chỉ có giá trị cho đúng phiên bản đã đọc."
    )


def _invalid_transition(details: Mapping[str, Any]) -> str:
    current = details.get("current")
    target = details.get("target")
    allowed = details.get("allowed") or []
    # A refusal that is *about* the content rather than about the stage edge -
    # policy readiness, since Step 1F.1 - already carries the sentence a person
    # should read. Prefer it over describing a stage this error never named.
    blocked = details.get("blocked_message")
    if isinstance(blocked, str) and blocked:
        return blocked
    # Step 1F.2.3f.5. ``MEASURED`` is retired, so a request for it is a stale
    # client rather than a missing precondition. Said as what it is: the stage is
    # gone, publication is where a piece stays, and there is nothing to do to
    # make the old move work.
    reason = details.get("reason")
    if isinstance(reason, str) and reason.endswith("_stage_retired"):
        return (
            "Bước “Đã đo hiệu quả” đã được bỏ. Nội dung giữ nguyên trạng thái đã đăng, "
            "và chỉ chuyển sang lưu trữ khi bạn chủ động lưu trữ."
        )
    if details.get("reason") == "missing_team_lead_approval":
        return "Chưa có phê duyệt của Trưởng nhóm cho phiên bản này nên chưa thể duyệt tiếp."
    if details.get("reason") == "no_producer":
        return "Nội dung này chưa có người nhận sản xuất nên chưa gửi duyệt nội bộ được."
    if details.get("reason") == "no_producer_assigned":
        return "Bạn cần phân công hoặc nhận người sản xuất trước khi bắt đầu sản xuất."
    if details.get("reason") == "no_production_submission":
        return "Cần gửi file sản xuất trước khi chuyển sang bước duyệt nội bộ."
    if details.get("reason") == "no_content_version":
        return "Nội dung này chưa có bản kịch bản nào nên chưa gửi duyệt được."
    if target == "TEAM_LEAD_REVIEW":
        # Step 1F.2.10. The direct submission asked from the wrong stage. Said
        # as the business fact - which step this is - rather than as a stage
        # edge a person did not choose.
        return "Không thể gửi duyệt Trưởng nhóm ở trạng thái hiện tại."
    if details.get("expected") == "PRODUCTION":
        return f"Nội dung đang ở bước {current}, không phải bước sản xuất."
    head = f"Nội dung đang ở bước {current}"
    if target:
        head += f", không thể chuyển thẳng sang {target}"
    if allowed:
        return f"{head}. Từ đây chỉ có thể chuyển sang: {', '.join(allowed)}."
    return f"{head}."


def _approval_stage_mismatch(details: Mapping[str, Any]) -> str:
    expected = details.get("expected_stage")
    if expected:
        return f"Nội dung đang chờ duyệt ở bước {expected}, không phải bước bạn vừa chọn."
    return "Nội dung này hiện không đứng ở bước duyệt nào."


def _forbidden(details: Mapping[str, Any]) -> str:
    reason = details.get("reason")
    if reason == "missing_grant":
        return "Bạn chưa được cấp quyền duyệt ở bước này. Trưởng phòng có thể cấp quyền cho bạn."
    if reason == "actor_has_no_user_row" or reason == "no_user_record":
        return "Tài khoản của bạn chưa được đăng ký trong TasksBot nên chưa thao tác được."
    # Step 1F.2.3. The delete rule refuses for three different reasons and they
    # need three different next steps: ask for the right, ask the owner, or stop
    # asking - so the sentence says which one this was.
    if reason == "not_responsible":
        return "Nội dung này không phải của bạn nên bạn không xóa được."
    if reason == "already_produced":
        return (
            "Bạn không thể xóa nội dung đã bước vào sản xuất. "
            "Trưởng nhóm hoặc trưởng phòng có thể xóa giúp bạn."
        )
    if reason == "not_the_producer":
        return "Chỉ người đang nhận sản xuất mới thao tác được với phần sản xuất này."
    if reason == "not_your_action":
        return "Chỉ người vừa thực hiện thao tác đó (hoặc quản lý) mới hoàn tác được."
    return "Bạn chưa có quyền thực hiện thao tác này."


def _assignment_overlap(details: Mapping[str, Any]) -> str:
    start = details.get("existing_from")
    end = details.get("existing_to") or "hiện tại"
    return (
        "Người này đã được phân công vai trò đó trên kênh này trong khoảng thời gian trùng "
        f"({start} → {end}). Bạn chọn mốc thời gian khác giúp mình nhé."
    )


def _ai_review_required(details: Mapping[str, Any]) -> str:
    version = details.get("version_no")
    return (
        f"Phiên bản v{version} chưa có kết quả AI review và cũng chưa được gửi thẳng "
        "cho Trưởng nhóm nên chưa thể duyệt. Bạn gửi nội dung đi AI review hoặc "
        "gửi duyệt Trưởng nhóm trước nhé."
    )


def _production_claimed(_details: Mapping[str, Any]) -> str:
    """Step 1F.2.3. The loser of a claim race.

    Deliberately reassuring rather than alarming: nothing went wrong, somebody
    else simply pressed first, and the only useful next step is to look at
    something else. The producer's *name* is not quoted even though ``details``
    carries their id - resolving a user here would mean a query inside an error
    formatter, and the panel shows the name from the reloaded card anyway.
    """
    return "Nội dung đã được người khác nhận sản xuất."


#: Why an undo is not available, in the words a person can act on. Keyed on
#: ``details['reason']`` from ``PrWorkflowUndoService``.
_UNDO_REASONS: Mapping[str, str] = {
    "nothing_to_undo": "Không còn thao tác nào có thể hoàn tác.",
    "not_reversible": "Bước vừa rồi không hoàn tác được.",
    "superseded": "Không thể hoàn tác vì đã có bước xử lý tiếp theo.",
    "published": "Không thể hoàn tác sau khi nội dung đã xuất bản.",
    "production_handed_off": (
        "Không thể hoàn tác duyệt vì nội dung đã được bàn giao cho sản xuất."
    ),
    "production_started": "Không thể hoàn tác duyệt vì nội dung đã bắt đầu sản xuất.",
    "production_submitted": "Không thể hoàn tác duyệt vì đã có file sản xuất được gửi.",
    "already_published": "Không thể hoàn tác vì nội dung đã được đăng.",
    "new_version_written": "Không thể hoàn tác vì đã có phiên bản nội dung mới.",
    "new_submission": "Không thể hoàn tác vì đã có file sản xuất mới.",
}


def _undo_not_available(details: Mapping[str, Any]) -> str:
    reason = details.get("reason")
    if isinstance(reason, str) and reason in _UNDO_REASONS:
        return _UNDO_REASONS[reason]
    return "Không còn thao tác nào có thể hoàn tác."


def _published_content(_details: Mapping[str, Any]) -> str:
    """Step 1F.2.3a. The one delete refusal nobody can route around.

    Says what to do instead, because "no" with no alternative is what makes
    somebody go looking for an admin who can override - and there is deliberately
    nobody who can.
    """
    return "Nội dung đã xuất bản không thể xóa vĩnh viễn. Hãy lưu trữ nội dung thay thế."


def _recorded_work(_details: Mapping[str, Any]) -> str:
    """The other delete refusal nobody can route around: the piece produced
    recorded work, and a month may already be counted against it. Nothing about
    the asker changes that, so no "try again" - the sentence says why and stops.
    """
    return "Nội dung đã phát sinh công việc được ghi nhận nên không thể xóa vĩnh viễn."


#: Why a production file reference was refused, in the words a person can act
#: on. Keyed on ``details['reason']`` from
#: :func:`~meobot.domain.pr.production.normalize_artifact`.
_ARTIFACT_REASONS: Mapping[str, str] = {
    "empty": "Bạn cần dán link hoặc đường dẫn file sản xuất trước khi gửi duyệt nội bộ.",
    "too_long": "Link file quá dài. Bạn dùng link chia sẻ gọn hơn nhé.",
    "missing_scheme": "Link cần bắt đầu bằng https:// (hoặc http://).",
    "unsafe_scheme": "Link này không an toàn nên mình không nhận. Chỉ nhận http:// hoặc https://.",
    "unsupported_scheme": "Mình chỉ nhận link http:// hoặc https://.",
    "missing_host": "Link chưa có tên miền nên không mở được.",
    "not_a_drive_host": (
        "Link này không phải Google Drive. Bạn đổi loại file hoặc dán lại link Drive."
    ),
    "not_a_path": "Bạn đang chọn kiểu đường dẫn NAS nhưng lại dán link. Chọn lại kiểu file nhé.",
    "not_absolute": "Đường dẫn NAS cần đầy đủ từ gốc, ví dụ /volume1/PR/ten-file.mp4",
    "incomplete_unc_path": "Đường dẫn NAS chưa đủ, cần cả tên máy và thư mục chia sẻ.",
}


def _validation(details: Mapping[str, Any]) -> str:
    """Field-level refusals, with the production ones worded properly.

    ``pr_validation_error`` is one code covering every "that input will not do",
    so this dispatches on ``reason`` where there is one worth saying and falls
    back to naming the field otherwise.
    """
    reason = details.get("reason")
    if isinstance(reason, str) and reason in _ARTIFACT_REASONS:
        return _ARTIFACT_REASONS[reason]
    if reason == "inactive_user":
        return "Tài khoản này đang không hoạt động nên không nhận việc được."
    if reason == "not_eligible":
        return "Người này không làm sản xuất nên không giao việc sản xuất được."
    return f"Thông tin chưa hợp lệ ({details.get('field', 'dữ liệu')}). Bạn kiểm tra lại nhé."


def _not_found(details: Mapping[str, Any]) -> str:
    if "code" in details:
        return f"Mình không tìm thấy {details['code']}."
    return "Mình không tìm thấy dữ liệu bạn nhắc tới."


#: ``code`` -> the sentence a person reads. Functions rather than templates
#: because most of these are only useful when they quote the numbers the
#: service already put in ``details``.
MESSAGES: Mapping[str, Callable[[Mapping[str, Any]], str]] = {
    "pr_not_found": _not_found,
    "pr_validation_error": _validation,
    "pr_invalid_transition": _invalid_transition,
    "pr_production_claimed": _production_claimed,
    "pr_published_content": _published_content,
    "pr_content_delete_blocked_recorded_work": _recorded_work,
    "pr_undo_not_available": _undo_not_available,
    "pr_stale_version": _stale_version,
    "pr_review_version_mismatch": _review_version_mismatch,
    "pr_ai_review_required": _ai_review_required,
    "pr_approval_stage_mismatch": _approval_stage_mismatch,
    "pr_forbidden": _forbidden,
    "pr_assignment_overlap": _assignment_overlap,
    "pr_immutable_field": lambda details: (
        f"Trường {details.get('current', 'này')} không thể thay đổi sau khi đã tạo."
    ),
    "pr_conflict": lambda _details: "Thao tác này trùng với dữ liệu đã có.",
    # ``pr_reviewer_separation`` was here until Step 1F.2.2 and is gone with the
    # rule: one person holding both grants may now sign both gates, so there is
    # no refusal left to word.
    # Inherited generic codes, in case a PR service raises a base class.
    "not_found": _not_found,
    "forbidden": _forbidden,
    "invalid_workflow_state": _invalid_transition,
}


def translate(error: MeoBotError) -> str:
    """The Vietnamese sentence for one PR error."""
    render = MESSAGES.get(error.code)
    if render is None:
        return FALLBACK
    try:
        return render(error.details)
    except Exception:  # pragma: no cover - a broken template must not hide the error
        logger.exception("pr_error_translation_failed", extra={"error_code": error.code})
        return FALLBACK


@contextmanager
def pr_errors() -> Iterator[None]:
    """Translate any PR service error raised inside the block.

    Wrap a whole tool body, not a single call: a tool that resolves a code,
    checks a capability and then writes can fail at any of the three, and every
    one of those failures deserves the same treatment.

    The original error becomes the ``__cause__`` of the translated one, so a
    traceback in the logs still shows where it came from - what the *user* sees
    is only ever the sentence.
    """
    try:
        yield
    except ToolExecutionError:
        # Already translated - a nested tool body, or a deliberate refusal.
        raise
    except MeoBotError as error:
        logger.info(
            "pr_tool_refused",
            extra={"error_code": error.code, "details": dict(error.details)},
        )
        raise ToolExecutionError(
            translate(error),
            details={"pr_error_code": error.code, **dict(error.details)},
        ) from error


__all__: list[str] = ["FALLBACK", "MESSAGES", "pr_errors", "translate"]
