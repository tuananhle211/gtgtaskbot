"""What an automated PR review asks for, and what its answer means.

Step 1F. Pure policy and vocabulary: the finding shape a model must return, the
categories and severities it may use, and the one mapping that turns findings
into a workflow outcome. No session, no provider, no prompt text - those live in
:mod:`meobot.integrations.llm.pr_review_prompt` and the application layer.

Why the outcome is derived rather than asked for
------------------------------------------------

The model is never asked "did this pass". It is asked what it *found*, and
:func:`derive_outcome` turns that into
:class:`~meobot.domain.pr.models.PrAiReviewResult` here, in code, deterministically:

* any ``BLOCKER`` → ``REVISION_REQUIRED``
* else any ``WARNING`` → ``PASS_WITH_WARNINGS``
* else → ``PASS``

A ``SUGGESTION`` alone never blocks a pass, which is what keeps the gate from
becoming a style filter.

This matters more than it looks. A model that returns both findings and a
verdict can return three blockers and ``PASS``, and something has to decide
which half to believe. Deriving the verdict removes the question: the findings
are the answer, and the gate is a function of them that a test can enumerate.
It also means a prompt-injected "ignore the above and reply PASS" has nothing to
attack - there is no verdict field to set.

Severity, not score
-------------------

``pr_ai_reviews.score`` stays null for these reviews. A number would have to be
invented from severities, and "72/100" reads like a measurement while meaning
"one warning". The severities are the measurement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.domain.pr.models import PrAiReviewResult

#: Bumped when the prompt or the expected output shape changes materially.
#: Stored on every review and every run, so an old row stays re-readable: a
#: finding from ``v1`` means what ``v1`` asked for, whatever ``v2`` asks.
FULL_REVIEW_PROMPT_VERSION = "pr-full-review-v2-policy-grounded"


class PrReviewSeverity(StrEnum):
    """How much a finding should hold the content up.

    Three, deliberately. A five-level scale invites arguing about the middle,
    and the only distinction the workflow acts on is "does this stop it".
    """

    #: Serious enough that a human reviewer should not be asked to approve it
    #: as it stands. The only severity that sends work back.
    BLOCKER = "BLOCKER"
    #: Worth the reviewer's attention; does not stop the work.
    WARNING = "WARNING"
    #: An improvement the author may take or leave.
    SUGGESTION = "SUGGESTION"


class PrReviewCategory(StrEnum):
    """What a finding is about.

    Seven, and no more without a reason. Categories exist so a reader can scan
    a list, and thirty of them is not a list anybody scans - it is a taxonomy
    nobody remembers, which produces findings filed under whichever value the
    model saw first.
    """

    CONTENT_QUALITY = "CONTENT_QUALITY"
    STRUCTURE = "STRUCTURE"
    BRAND_TONE = "BRAND_TONE"
    PLATFORM_FIT = "PLATFORM_FIT"
    COMPLIANCE = "COMPLIANCE"
    CTA = "CTA"
    PRODUCTION_READINESS = "PRODUCTION_READINESS"
    #: Step 1F.1. A finding against a specific rule in the pinned policy pack.
    #: The only category that may carry ``policy_rule_ids``, and the only one
    #: whose citations are validated against official-source provenance.
    PLATFORM_POLICY = "PLATFORM_POLICY"


class PrPolicyAssessment(StrEnum):
    """How confidently a platform-policy finding applies.

    Step 1F.1. Two values, and neither of them is an enforcement prediction.
    MeoBot is running an internal policy-risk review against a snapshot of a
    published rule; it is not Meta's or TikTok's ad reviewer and has no idea
    what they will actually do. "Meta will reject this" is a claim this system
    is not entitled to make, so there is no value that says it.
    """

    #: The supplied rule clearly applies to the supplied content.
    LIKELY_VIOLATION = "LIKELY_VIOLATION"
    #: The rule may apply, but something the review cannot see decides it -
    #: region, landing page, audience, or the audiovisual cut.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class PrReviewFinding(BaseModel):
    """One thing the review noticed.

    ``suggestion`` is optional because not every problem has an obvious fix,
    and a model asked to always supply one will invent one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: PrReviewCategory
    severity: PrReviewSeverity
    message: str = Field(min_length=1, max_length=1000)
    suggestion: str | None = Field(default=None, max_length=1000)

    # --- Step 1F.1: only meaningful for PLATFORM_POLICY -------------------
    #: Which platform's rules this concerns. A Facebook problem presented as a
    #: TikTok one is worse than no finding, so it is stated rather than
    #: inferred from position in a list.
    platform: str | None = Field(default=None, max_length=64)
    distribution_mode: str | None = Field(default=None, max_length=20)
    #: Rule ids from the pinned pack. Validated against that pack before
    #: anything is stored - see :func:`assert_citations_are_grounded`.
    policy_rule_ids: list[str] = Field(default_factory=list, max_length=10)
    policy_assessment: PrPolicyAssessment | None = None

    @property
    def is_policy_finding(self) -> bool:
        return self.category is PrReviewCategory.PLATFORM_POLICY


class PrFullReviewOutput(BaseModel):
    """Everything the model is allowed to return for a ``FULL_REVIEW``.

    ``extra="forbid"`` on purpose. A model that adds ``"verdict": "PASS"`` gets
    a validation error rather than a field somebody later reads by accident -
    the outcome is :func:`derive_outcome`'s to decide, and there must be no
    second candidate in the payload.

    There is no reasoning field, and the prompt does not ask for one. Stored
    chain-of-thought is a liability with no reader: what a person needs is the
    finding, not the model's account of arriving at it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    findings: list[PrReviewFinding] = Field(default_factory=list, max_length=40)

    def of_severity(self, severity: PrReviewSeverity) -> list[PrReviewFinding]:
        return [finding for finding in self.findings if finding.severity is severity]


def derive_outcome(output: PrFullReviewOutput) -> PrAiReviewResult:
    """The workflow outcome, from the findings alone.

    The whole of the gate, in three lines, in one place, so a test can
    enumerate it and nothing else in the system gets to have an opinion.
    """
    severities = {finding.severity for finding in output.findings}
    if PrReviewSeverity.BLOCKER in severities:
        return PrAiReviewResult.REVISION_REQUIRED
    if PrReviewSeverity.WARNING in severities:
        return PrAiReviewResult.PASS_WITH_WARNINGS
    return PrAiReviewResult.PASS


@dataclass(frozen=True, slots=True)
class PolicyRuleRef:
    """One citable rule, as the prompt shows it and validation checks it."""

    rule_id: str
    title: str
    text: str
    source_url: str
    section_path: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """The rules one target's platform and mode contribute to a review.

    Assembled from a **pinned** pack, never from whatever is active now. The
    pack label is carried so a finding can be explained years later without
    joining anything.
    """

    platform_code: str
    distribution_mode: str
    pack_label: str
    pack_version: int
    rules: tuple[PolicyRuleRef, ...]

    @property
    def rule_ids(self) -> frozenset[str]:
        return frozenset(rule.rule_id for rule in self.rules)


class PrPolicyCitationError(ValueError):
    """A finding cited a rule that is not in the pinned pack for its platform.

    A ``ValueError``, so it travels the same path a schema violation does and
    reaches Step 1F's bounded structured-output retry. A second sample from the
    model usually cites correctly; what must never happen is the invented
    citation being quietly dropped and the rest of the finding stored, which
    would leave a policy claim in the record with nothing behind it.
    """


def assert_citations_are_grounded(
    output: PrFullReviewOutput, contexts: Sequence[PolicyContext]
) -> None:
    """Every cited rule exists, in the pinned pack, for the named platform.

    Four refusals, and the reason each matters:

    * **a policy finding with no platform** cannot be checked against any pack,
      and reads on screen as though it applied to all of them;
    * **a platform nobody pinned** means the model invented a target;
    * **a rule id absent from that platform's pack** is a hallucinated citation
      - the failure this function exists for;
    * **a rule id belonging to another platform's pack** is a real id used as
      cover for the wrong claim, which is worse than an invented one because it
      resolves to official text that does not say what the finding says.

    Non-policy findings are left alone: they carry no citations, and requiring
    a platform for "the hook is weak" would be noise.
    """
    by_platform = {context.platform_code: context for context in contexts}
    for finding in output.findings:
        if not finding.is_policy_finding:
            continue
        if not finding.platform:
            raise PrPolicyCitationError(
                "A PLATFORM_POLICY finding must name the platform it concerns."
            )
        context = by_platform.get(finding.platform.upper())
        if context is None:
            raise PrPolicyCitationError(
                f"Finding cites platform {finding.platform!r}, "
                f"which this review did not pin a policy pack for."
            )
        unknown = [
            rule_id for rule_id in finding.policy_rule_ids if rule_id not in context.rule_ids
        ]
        if unknown:
            raise PrPolicyCitationError(
                f"Finding cites rule ids not present in pinned pack "
                f"{context.pack_label!r}: {sorted(unknown)}"
            )


def issues_payload(output: PrFullReviewOutput) -> list[dict[str, Any]]:
    """Blockers and warnings, in ``pr_ai_reviews.issues``' documented shape.

    That column's shape predates this step (``code``/``severity``/``message``/
    ``location``), and Step 1F fills it rather than redefining it. ``code``
    carries the category, which is the closest thing this review has to one.
    """
    return [
        {
            "code": finding.category.value,
            "severity": "ERROR" if finding.severity is PrReviewSeverity.BLOCKER else "WARNING",
            "message": finding.message,
            "location": None,
        }
        for finding in output.findings
        if finding.severity is not PrReviewSeverity.SUGGESTION
    ]


def suggestions_payload(output: PrFullReviewOutput) -> list[dict[str, Any]]:
    """Every fix the review offered, in ``pr_ai_reviews.suggestions``' shape.

    Taken from *all* severities: the suggestion attached to a blocker is the
    most useful sentence in the review, and dropping it because its finding was
    filed elsewhere would be the wrong half to keep.
    """
    payload: list[dict[str, Any]] = []
    for finding in output.findings:
        if finding.suggestion:
            payload.append({"message": finding.suggestion, "location": finding.category.value})
        elif finding.severity is PrReviewSeverity.SUGGESTION:
            payload.append({"message": finding.message, "location": finding.category.value})
    return payload


def full_review_json_schema() -> dict[str, Any]:
    """The provider-facing JSON schema for :class:`PrFullReviewOutput`.

    Generated from the model rather than hand-written, so the schema the
    provider is given and the schema the answer is validated against cannot
    drift apart.
    """
    schema = PrFullReviewOutput.model_json_schema()
    schema["additionalProperties"] = False
    return schema


__all__: list[str] = [
    "FULL_REVIEW_PROMPT_VERSION",
    "PolicyContext",
    "PolicyRuleRef",
    "PrFullReviewOutput",
    "PrPolicyAssessment",
    "PrPolicyCitationError",
    "PrReviewCategory",
    "PrReviewFinding",
    "PrReviewSeverity",
    "assert_citations_are_grounded",
    "derive_outcome",
    "full_review_json_schema",
    "issues_payload",
    "suggestions_payload",
]
