"""Task methods every provider implements the same way.

``review_script`` and ``propose_sheet_mapping`` are advisory: they turn a
schema-bound JSON answer into a validated domain object and hand it to code
that decides. That translation is identical for the real provider and the
offline one, so it lives here rather than being written twice.

The mixin only ever calls :meth:`complete_structured`, which both providers
implement. It never touches HTTP, credentials or a database.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import ValidationError as PydanticValidationError

from meobot.core.errors import LLMError
from meobot.core.logging import get_logger
from meobot.domain.pr.ai_review import PrFullReviewOutput
from meobot.domain.scripts.models import MappingProposal, ScriptReviewResult
from meobot.integrations.llm.base import (
    PrFullReviewRequest,
    ScriptReviewRequest,
    SheetMappingRequest,
    StructuredRequest,
    StructuredResponse,
)

logger = get_logger(__name__)


class _SupportsStructured(Protocol):
    async def complete_structured(self, request: StructuredRequest) -> StructuredResponse: ...


class StructuredTaskMixin:
    """Typed wrappers over :meth:`complete_structured`."""

    async def review_script(
        self: _SupportsStructured, request: ScriptReviewRequest
    ) -> ScriptReviewResult:
        """Score a script against a rubric.

        Raises:
            LLMError: On a provider failure, or when the answer does not
                validate. A half-parsed review is never returned - the caller
                would store it, and a stored score nobody can trust is worse
                than no score.
        """
        response = await self.complete_structured(
            StructuredRequest(
                task="script_review",
                system_prompt=request.system_prompt,
                user_prompt=json.dumps(request.payload, ensure_ascii=False),
                json_schema=request.json_schema,
                schema_name="script_review",
                temperature=0.2,
                max_output_tokens=request.max_output_tokens,
                context=request.context,
            )
        )
        try:
            return ScriptReviewResult.model_validate(response.payload)
        except PydanticValidationError as exc:
            raise LLMError(
                "LLM trả về kết quả review không đúng định dạng.",
                details={"errors": exc.error_count(), "task": "script_review"},
            ) from exc

    async def review_pr_content(
        self: _SupportsStructured, request: PrFullReviewRequest
    ) -> PrFullReviewOutput:
        """Review one PR content draft, structurally.

        Step 1F. Written once here, for both providers, exactly as
        :meth:`review_script` is - the translation from a schema-bound payload
        to a validated domain object is identical whether a real model or the
        offline one answered.

        Raises:
            LLMError: On a provider failure, or when the answer does not
                validate. ``PrFullReviewOutput`` forbids extra fields, so a
                model that invents a ``verdict`` fails here rather than having
                it quietly ignored - and the caller retries or gives up without
                a workflow ever moving on a malformed review.
        """
        response = await self.complete_structured(
            StructuredRequest(
                task="pr_full_review",
                system_prompt=request.system_prompt,
                user_prompt=json.dumps(request.payload, ensure_ascii=False),
                json_schema=request.json_schema,
                schema_name="pr_full_review",
                # Low, and lower than the chat path: a review that reaches a
                # different verdict on the same draft twice is not a gate.
                temperature=0.1,
                max_output_tokens=request.max_output_tokens,
                context=request.context,
            )
        )
        try:
            return PrFullReviewOutput.model_validate(response.payload)
        except PydanticValidationError as exc:
            raise LLMError(
                "AI review trả về kết quả không đúng định dạng.",
                details={"errors": exc.error_count(), "task": "pr_full_review"},
            ) from exc

    async def propose_sheet_mapping(
        self: _SupportsStructured, request: SheetMappingRequest
    ) -> MappingProposal:
        """Propose a column mapping for a spreadsheet.

        Raises:
            LLMError: On a provider failure or an unusable payload. The caller
                falls back to the deterministic alias table.
        """
        user_prompt = json.dumps(
            {
                "headers": request.headers,
                # Truncated so a long script body cannot dominate the prompt.
                "sample_rows": [
                    {key: value[:200] for key, value in row.items()}
                    for row in request.sample_rows[:3]
                ],
            },
            ensure_ascii=False,
        )
        response = await self.complete_structured(
            StructuredRequest(
                task="sheet_mapping",
                system_prompt=request.system_prompt,
                user_prompt=user_prompt,
                json_schema=request.json_schema,
                schema_name="sheet_mapping",
                temperature=0.0,
                max_output_tokens=request.max_output_tokens,
                context={"headers": list(request.headers)},
            )
        )
        try:
            return MappingProposal.model_validate(response.payload)
        except PydanticValidationError as exc:
            raise LLMError(
                "LLM trả về mapping không đúng định dạng.",
                details={"errors": exc.error_count(), "task": "sheet_mapping"},
            ) from exc
