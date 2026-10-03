"""AI review of one exact script version, against one exact rubric version.

What makes a stored review trustworthy later is provenance: the version it
judged, the rubric version it judged against, a snapshot of that rubric, the
provider and model, and the raw structured answer. All of it is written in one
transaction with the status change.

The LLM's verdict is advisory. It never changes the workflow state beyond
"this has been reviewed" - approving for production stays a human action in
:mod:`meobot.application.script_service`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import LLMError, NotFoundError, WorkflowStateError
from meobot.core.logging import get_logger
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.script_review import ScriptReview
from meobot.db.models.script_type import ScriptType, ScriptTypeVersion
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.script_types.rubric import ReviewRubric, validate_rubric_payload
from meobot.domain.scripts.models import ScriptReviewResult
from meobot.domain.scripts.workflow import ScriptStatus, can_transition_script
from meobot.integrations.llm.base import LLMProvider, StructuredRequest

logger = get_logger(__name__)

#: Statuses a review may start from. ``in_production`` and ``archived`` are out.
REVIEWABLE_STATUSES: frozenset[ScriptStatus] = frozenset(
    {
        ScriptStatus.DRAFT,
        ScriptStatus.IMPORTED,
        ScriptStatus.SUBMITTED_FOR_REVIEW,
        ScriptStatus.REVIEWING,
        ScriptStatus.AI_REVIEWED,
        ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
        ScriptStatus.REVISION_REQUIRED,
        ScriptStatus.APPROVED_FOR_PRODUCTION,
    }
)

REVIEW_SYSTEM_PROMPT = """\
Bạn là biên tập viên nội dung video ngắn giàu kinh nghiệm của một team truyền \
thông Việt Nam. Bạn chấm điểm kịch bản theo đúng rubric được cung cấp.

Nguyên tắc:
- Chấm theo rubric, không theo cảm tính. Tổng điểm 0-100.
- Nêu vấn đề cụ thể, kèm cách sửa; không nói chung chung.
- Nếu nội dung liên quan sức khoẻ/y tế, nêu rõ rủi ro cần chuyên môn kiểm chứng \
trong medical_risk_notes.
- Nêu rủi ro thương hiệu và rủi ro sai sự thật nếu có.
- short_summary_for_telegram phải ngắn (tối đa 3 câu), tiếng Việt, cho người duyệt đọc nhanh.
- Bạn KHÔNG có quyền duyệt kịch bản. Bạn chỉ đưa ra khuyến nghị.
- Trả lời bằng JSON đúng schema, tiếng Việt.
"""


def review_json_schema() -> dict[str, Any]:
    """JSON schema handed to the provider, derived from the domain model."""
    schema = ScriptReviewResult.model_json_schema()
    schema["additionalProperties"] = False
    return schema


class ScriptReviewService:
    """Runs and stores AI reviews.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing the same session.
        llm: Provider producing the structured review. May be ``None`` for the
            read-only methods; running a review without one fails loudly.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        llm: LLMProvider | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._llm = llm

    async def review_script(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        script_id: uuid.UUID,
        brand_instructions: str | None = None,
    ) -> ScriptReview:
        """Review a script's *current* version and store the result.

        Raises:
            NotFoundError: When the script or its current version is missing.
            WorkflowStateError: When the script cannot be reviewed right now.
            LLMError: When no provider is configured, or the provider fails or
                returns an invalid payload.
        """
        if self._llm is None:
            raise LLMError("Không có LLM provider để chấm điểm kịch bản.")

        script = await self._session.get(Script, script_id)
        if script is None:
            raise NotFoundError(f"Không tìm thấy kịch bản {script_id}")
        if script.current_version_id is None:
            raise NotFoundError("Kịch bản chưa có nội dung để review.")
        version = await self._session.get(ScriptVersion, script.current_version_id)
        if version is None:
            raise NotFoundError("Không tìm thấy phiên bản hiện tại của kịch bản.")

        if script.status not in REVIEWABLE_STATUSES:
            raise WorkflowStateError(
                f"Kịch bản đang ở trạng thái {script.status.value!r}, không thể review.",
                details={"status": script.status.value},
            )

        rubric, type_version, script_type = await self._load_rubric(script.script_type_id)
        status_before = script.status
        # Every reviewable status can reach the queue, and the queue can reach
        # 'reviewing' - walking through it keeps the state machine authoritative
        # instead of assigning statuses directly.
        self._move(script, ScriptStatus.SUBMITTED_FOR_REVIEW)
        self._move(script, ScriptStatus.REVIEWING)
        await self._session.flush()

        try:
            result, provider, model, usage = await self._ask(
                rubric=rubric,
                script_type=script_type,
                version=version,
                author=script.author,
                brand_instructions=brand_instructions,
            )
        except LLMError as exc:
            # Put the script back where a retry can pick it up.
            self._move(script, ScriptStatus.SUBMITTED_FOR_REVIEW)
            await self._session.flush()
            await self._audit.record_action(
                request_id=request_id,
                actor=actor,
                action=AuditAction.SCRIPT_REVIEW_FAILED.value,
                result=AuditResult.FAILED,
                entity_type="script",
                entity_id=str(script.id),
                error_message=exc.message,
            )
            raise

        review = ScriptReview(
            script_id=script.id,
            script_version_id=version.id,
            script_type_version_id=type_version.id if type_version is not None else None,
            provider=provider,
            model=model,
            rubric_snapshot=rubric.model_dump(mode="json"),
            overall_score=result.overall_score,
            verdict=result.verdict,
            summary=result.short_summary_for_telegram,
            strengths=list(result.strengths),
            critical_issues=list(result.critical_issues),
            recommendations=list(result.improvement_recommendations),
            revised_hook_suggestion=result.revised_hook_suggestion,
            revised_script_suggestion=result.revised_script_suggestion,
            structured_response=result.model_dump(mode="json"),
            usage_metadata=dict(usage),
        )
        self._session.add(review)

        self._move(script, ScriptStatus.AI_REVIEWED)
        self._move(script, ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL)
        await self._session.flush()

        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.SCRIPT_REVIEWED.value,
            result=AuditResult.SUCCESS,
            entity_type="script",
            entity_id=str(script.id),
            before_data={"status": status_before.value},
            after_data={
                "status": script.status.value,
                "script_version_id": str(version.id),
                "review_id": str(review.id),
                "overall_score": review.overall_score,
                "verdict": review.verdict.value,
                "provider": provider,
                "model": model,
            },
        )
        logger.info(
            "script_reviewed",
            extra={
                "script_id": str(script.id),
                "version": version.version_number,
                "score": review.overall_score,
                "provider": provider,
            },
        )
        return review

    async def latest_review(self, script_id: uuid.UUID) -> ScriptReview | None:
        """The most recent review of any version of a script."""
        result = await self._session.execute(
            select(ScriptReview)
            .where(ScriptReview.script_id == script_id)
            .order_by(ScriptReview.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def review_for_version(self, version_id: uuid.UUID) -> ScriptReview | None:
        """The most recent review of one exact version."""
        result = await self._session.execute(
            select(ScriptReview)
            .where(ScriptReview.script_version_id == version_id)
            .order_by(ScriptReview.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_reviews(self, script_id: uuid.UUID) -> list[ScriptReview]:
        """Every review of a script, newest first."""
        result = await self._session.execute(
            select(ScriptReview)
            .where(ScriptReview.script_id == script_id)
            .order_by(ScriptReview.created_at.desc())
        )
        return list(result.scalars().all())

    # --- Internals --------------------------------------------------------
    async def _load_rubric(
        self,
        script_type_id: uuid.UUID | None,
    ) -> tuple[ReviewRubric, ScriptTypeVersion | None, ScriptType | None]:
        """Load the active rubric version, or fall back to a generic rubric."""
        if script_type_id is None:
            return _default_rubric(), None, None

        script_type = await self._session.get(ScriptType, script_type_id)
        if script_type is None:
            return _default_rubric(), None, None

        result = await self._session.execute(
            select(ScriptTypeVersion).where(
                ScriptTypeVersion.script_type_id == script_type_id,
                ScriptTypeVersion.version == script_type.current_version,
            )
        )
        type_version = result.scalar_one_or_none()
        if type_version is None:
            return _default_rubric(), None, script_type
        return validate_rubric_payload(type_version.review_rubric), type_version, script_type

    async def _ask(
        self,
        *,
        rubric: ReviewRubric,
        script_type: ScriptType | None,
        version: ScriptVersion,
        author: str | None,
        brand_instructions: str | None,
    ) -> tuple[ScriptReviewResult, str, str, dict[str, int]]:
        """Call the provider and validate its answer.

        Raises:
            LLMError: On a provider failure or an unusable payload.
        """
        assert self._llm is not None  # guarded by review_script
        configuration: dict[str, Any] = {}
        payload = {
            "script_type": {
                "code": script_type.code if script_type else "generic",
                "name": script_type.name if script_type else "Kịch bản video ngắn",
                "configuration": configuration,
            },
            "rubric": rubric.model_dump(mode="json"),
            "script": {
                "title": version.title,
                "hook": version.hook or "",
                "script_body": version.script_body,
                "production_notes": version.production_notes or "",
                "author": author or "",
                "version": version.version_number,
            },
            "review_instructions": brand_instructions or "",
        }

        response = await self._llm.complete_structured(
            StructuredRequest(
                task="script_review",
                system_prompt=REVIEW_SYSTEM_PROMPT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                json_schema=review_json_schema(),
                schema_name="script_review",
                temperature=0.2,
                max_output_tokens=4000,
                context={
                    "title": version.title,
                    "hook": version.hook or "",
                    "script_body": version.script_body,
                    "production_notes": version.production_notes or "",
                },
            )
        )

        try:
            result = ScriptReviewResult.model_validate(response.payload)
        except PydanticValidationError as exc:
            raise LLMError(
                "LLM trả về kết quả review không đúng định dạng.",
                details={"errors": exc.error_count()},
            ) from exc

        return result, response.provider, response.model, response.usage

    @staticmethod
    def _move(script: Script, target: ScriptStatus) -> None:
        """Advance the script's status when the transition is legal.

        An illegal transition is skipped rather than raised: the review itself
        is still valid data, and the status guard that matters (approval) is
        enforced separately in the approval path.
        """
        if script.status is target:
            return
        if can_transition_script(script.status, target):
            script.status = target


def _default_rubric() -> ReviewRubric:
    """Generic rubric for a script whose type has no rubric configured."""
    return validate_rubric_payload(
        {
            "criteria": [
                {
                    "code": "hook",
                    "name": "Hook mở đầu",
                    "weight": 30,
                    "guidance": "3 giây đầu có giữ được người xem không?",
                },
                {
                    "code": "structure",
                    "name": "Cấu trúc",
                    "weight": 30,
                    "guidance": "Mạch nội dung rõ ràng, có cao trào và kết.",
                },
                {
                    "code": "clarity",
                    "name": "Độ rõ ràng",
                    "weight": 20,
                    "guidance": "Câu chữ dễ hiểu, phù hợp lời thoại.",
                },
                {
                    "code": "call_to_action",
                    "name": "Kêu gọi hành động",
                    "weight": 20,
                    "guidance": "Kết thúc có dẫn dắt người xem làm gì tiếp theo.",
                },
            ],
            "passing_score": 70,
            "notes": "Rubric mặc định khi kịch bản chưa gắn thể loại.",
        }
    )
