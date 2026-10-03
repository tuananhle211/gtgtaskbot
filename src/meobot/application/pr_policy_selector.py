"""Choosing which pinned policy rules one review actually sees.

Step 1F.1 release hardening. A policy pack is complete and auditable; a prompt
is not, because TikTok's Community Guidelines alone extract to hundreds of
sections. Those are two different problems and this module is the second one.

```
complete pinned pack ─→ deterministic selection ─→ bounded prompt context
   (durable, whole)          (this module)             (what the LLM sees)
```

What this replaced
------------------

Pack construction used to keep the **24 longest sections** per source. That is
not a budget, it is data loss with a plausible-looking rule: a one-line
prohibition loses to a three-paragraph explanation every time, and the pack -
the thing an auditor reads two years later - simply did not contain it.

Now the pack keeps everything substantive, and the bounding happens here, per
review, reversibly, with a version attached.

Coverage before relevance
-------------------------

Selection runs three passes, in order, and the order is the invariant:

1. **Coverage.** One rule from every ``(scope, category)`` present in the pack.
   Categories are the headings the *pack* carries - never a hard-coded English
   list, because the categories are whatever the official page had. This is
   what stops a single large category from eating the budget and taking every
   short one with it.
2. **Relevance.** Remaining budget goes to rules whose text overlaps the
   content, scored lexically.
3. **Backstop.** Any budget still unspent goes to further rules in document
   order, so a small pack is sent whole rather than artificially thinned.

Composite paid packs get their coverage per **scope**, so a TikTok paid review
cannot spend its whole budget on Community Guidelines and omit the Advertising
Policies it is actually being judged against.

Deterministic, and provably so
------------------------------

No model, no network, no embeddings, no vector store. Scoring is lexical
overlap with deterministic tie-breaking on ``rule_id``, so the same content
version, pack version and :data:`POLICY_SELECTOR_VERSION` always select the same
rule ids in the same order - which is what makes a stored review re-explainable
rather than merely re-runnable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.domain.pr.ai_review import PolicyContext, PolicyRuleRef

logger = get_logger(__name__)

#: Bumped when selection changes in a way that would pick different rules for
#: the same content and pack. Stored with the review, so "why did it cite that"
#: stays answerable after this file changes.
POLICY_SELECTOR_VERSION = "policy-select-v1"

#: Characters of policy text one review may carry, across every platform. A
#: character budget rather than tokens: the provider abstraction exposes no
#: counter, and a documented character bound with margin is honest where a
#: guessed token count would not be. Roughly 6k tokens at 4 chars/token, which
#: leaves the content and the instructions comfortable room.
DEFAULT_POLICY_CONTEXT_BUDGET = 24_000

#: A rule longer than this is truncated at a paragraph boundary rather than
#: excluded, so one enormous section cannot silently drop a whole category.
MAX_RULE_CHARS = 2_400

#: Words carrying no signal for overlap scoring. Deliberately short: a long
#: stop-list is a tuning knob, and this only has to stop "the" from deciding
#: which policy a review sees.
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
        "not",
        "no",
        "do",
        "does",
        "can",
        "may",
        "must",
        "và",
        "của",
        "các",
        "là",
        "có",
        "không",
        "được",
        "cho",
        "khi",
        "này",
        "đó",
        "với",
        "những",
        "một",
        "trong",
        "đến",
        "trên",
    ]
)

_WORD = re.compile(r"[0-9a-zà-ỹ]+", re.IGNORECASE)


class PolicyCoverageError(MeoBotError):
    """A pinned pack cannot be covered, so the review must not claim grounding.

    Raised when a pack contains no usable rules at all. Selecting an arbitrary
    handful instead would produce a review that looks policy-grounded and is
    not, which is worse than a failure the operator can see.
    """

    code = "policy_coverage_unavailable"


@dataclass(frozen=True, slots=True)
class PolicySelection:
    """What one review will be shown, and enough metadata to explain it later."""

    contexts: tuple[PolicyContext, ...]
    selector_version: str
    #: Every rule id sent, in order, across every platform. The audit trail.
    selected_rule_ids: tuple[str, ...]
    #: How many rules the pinned packs held in total, before bounding.
    available_rule_count: int
    used_chars: int


def _terms(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOP_WORDS}


def _category_of(rule: PolicyRuleRef) -> str:
    """The heading this rule sits under, which is the pack's own category.

    Taken from ``section_path``'s last segment, falling back to the title. Never
    a hard-coded taxonomy: the categories are whatever the official page
    published, and inventing a list of "expected" ones would quietly drop
    anything the platform words differently.
    """
    if rule.section_path and ">" in rule.section_path:
        return rule.section_path.rsplit(">", 1)[-1].strip()
    return (rule.section_path or rule.title).strip()


def _clip(text: str) -> str:
    """Bound one rule at a paragraph edge, never mid-sentence."""
    if len(text) <= MAX_RULE_CHARS:
        return text
    head = text[:MAX_RULE_CHARS]
    cut = max(head.rfind("\n\n"), head.rfind(". "))
    return (head[: cut + 1] if cut > MAX_RULE_CHARS // 2 else head).rstrip() + " […]"


def _score(rule: PolicyRuleRef, wanted: set[str]) -> int:
    """Lexical overlap with the content. Deterministic, local, explainable."""
    if not wanted:
        return 0
    text = _terms(f"{rule.title} {rule.text}")
    # Title matches count double: a heading is the platform's own summary of
    # what the rule is about, so overlap there is a stronger signal than a word
    # appearing once in a long body.
    return len(text & wanted) + len(_terms(rule.title) & wanted)


def select_policy_context(
    contexts: Sequence[PolicyContext],
    *,
    content_terms: Iterable[str] = (),
    budget_chars: int = DEFAULT_POLICY_CONTEXT_BUDGET,
) -> PolicySelection:
    """Bound the pinned packs to what one prompt can carry.

    Operates **only** on what it is given. It does not resolve the active pack,
    fetch anything, or change pack membership - the contexts here came from
    ``pr_ai_review_run_policy_packs`` and are already pinned.

    Raises:
        PolicyCoverageError: The pinned packs contain no rules. A review cannot
            be called policy-grounded against nothing.
    """
    available = sum(len(context.rules) for context in contexts)
    if contexts and available == 0:
        raise PolicyCoverageError(
            "Bộ chính sách đã ghim không có điều khoản nào.",
            details={
                "reason": "empty_pinned_pack",
                "packs": [context.pack_label for context in contexts],
            },
        )

    wanted = {term.lower() for term in content_terms if term} or set()
    per_context = _split_budget(contexts, budget_chars)

    chosen: list[PolicyContext] = []
    ordered_ids: list[str] = []
    used = 0
    for context in contexts:
        picked, spent = _select_for_context(context, wanted, per_context[context.platform_code])
        used += spent
        ordered_ids.extend(rule.rule_id for rule in picked)
        chosen.append(
            PolicyContext(
                platform_code=context.platform_code,
                distribution_mode=context.distribution_mode,
                pack_label=context.pack_label,
                pack_version=context.pack_version,
                rules=tuple(picked),
            )
        )

    logger.info(
        "policy_context_selected",
        extra={
            "selector_version": POLICY_SELECTOR_VERSION,
            "packs": [context.pack_label for context in contexts],
            "available_rules": available,
            "selected_rules": len(ordered_ids),
            "policy_chars": used,
            "budget_chars": budget_chars,
        },
    )
    return PolicySelection(
        contexts=tuple(chosen),
        selector_version=POLICY_SELECTOR_VERSION,
        selected_rule_ids=tuple(ordered_ids),
        available_rule_count=available,
        used_chars=used,
    )


def _split_budget(contexts: Sequence[PolicyContext], budget: int) -> dict[str, int]:
    """Share the budget evenly across platforms.

    Evenly rather than proportionally to pack size: TikTok's guidelines are
    thirty times the size of a Meta sub-page, and a proportional split would let
    the larger pack starve the smaller one - which on a multi-platform review
    means one platform's rules effectively vanish.
    """
    if not contexts:
        return {}
    share = max(1, budget // len(contexts))
    return {context.platform_code: share for context in contexts}


def _select_for_context(
    context: PolicyContext, wanted: set[str], budget: int
) -> tuple[list[PolicyRuleRef], int]:
    """Three passes over one platform's pinned rules. Coverage first."""
    remaining = budget
    picked: list[PolicyRuleRef] = []
    taken: set[str] = set()

    def take(rule: PolicyRuleRef) -> bool:
        nonlocal remaining
        clipped = _clip(rule.text)
        cost = len(clipped) + len(rule.title)
        if cost > remaining:
            return False
        remaining -= cost
        taken.add(rule.rule_id)
        picked.append(
            PolicyRuleRef(
                rule_id=rule.rule_id,
                title=rule.title,
                text=clipped,
                source_url=rule.source_url,
                section_path=rule.section_path,
            )
        )
        return True

    # --- Pass 1: coverage. One rule per category, cheapest first within each,
    # so a category represented only by a long rule still gets in.
    by_category: dict[str, list[PolicyRuleRef]] = {}
    for rule in context.rules:
        by_category.setdefault(_category_of(rule), []).append(rule)
    for category in sorted(by_category):
        candidates = sorted(by_category[category], key=lambda rule: (len(rule.text), rule.rule_id))
        for candidate in candidates:
            if take(candidate):
                break

    # --- Pass 2: relevance, over what coverage did not already take.
    scored = sorted(
        (rule for rule in context.rules if rule.rule_id not in taken),
        key=lambda rule: (-_score(rule, wanted), rule.rule_id),
    )
    for rule in scored:
        if remaining <= 0:
            break
        if _score(rule, wanted) == 0:
            break
        take(rule)

    # --- Pass 3: backstop. Document order, so a small pack goes whole.
    for rule in context.rules:
        if remaining <= 0:
            break
        if rule.rule_id not in taken:
            take(rule)

    # Emitted in the pack's own order, so a reader follows the official page
    # rather than this function's passes.
    order = {rule.rule_id: index for index, rule in enumerate(context.rules)}
    picked.sort(key=lambda rule: order[rule.rule_id])
    return picked, budget - remaining


__all__ = [
    "DEFAULT_POLICY_CONTEXT_BUDGET",
    "MAX_RULE_CHARS",
    "POLICY_SELECTOR_VERSION",
    "PolicyCoverageError",
    "PolicySelection",
    "select_policy_context",
]
