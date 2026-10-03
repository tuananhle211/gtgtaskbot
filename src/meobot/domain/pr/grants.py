"""What an explicit approval grant covers, and whether it covers this item.

An approval grant used to be one line - *this person may decide at this gate* -
and Step 1F.2.7 splits that line in two. A grant now says **where** the right
applies, along two axes the PR module already models:

* the **content classification**, :class:`~meobot.domain.pr.models.PrContentType`
  on ``pr_content_items.content_type``;
* the **channel**, ``pr_channels.id`` reached through ``pr_content_targets``.

Both are canonical ids the schema already stores. Nothing here reads a title, a
Vietnamese label or a platform name: a scope derived from a display string would
change meaning the day somebody renamed a channel.

Exact scope, and nothing beyond it
----------------------------------

:meth:`GrantScope.covers` is a conjunction and every part of it fails closed.
The two interesting cases are the ones a content item can be *missing*:

* **no classification.** ``content_type`` is nullable - thousands of rows
  predate the column - and a grant scoped to ``SELECTED`` classifications does
  **not** cover them unless it says ``include_unclassified_content``. Reading
  "unclassified" as "matches everything" would hand a Facebook-post approver
  every historical row in the database;
* **no channel.** An item with no ``pr_content_targets`` row has no
  distribution, and a grant scoped to ``SELECTED`` channels does not cover it
  unless it says ``include_unassigned_channel``. Same reason, same direction:
  absent is not a wildcard.

The channel test is a **subset** test, not an intersection. An item that goes to
CH-0001 *and* CH-0009 is approved once, for both, so a grant that names only
CH-0001 must not decide it - approving it would be approving the CH-0009
posting, which is exactly the implicit expansion this module refuses. A grant
naming both channels decides it; so does a grant scoped to ``ALL``.

``ALL`` is a mode, not a list
-----------------------------

Scoping to every channel is :attr:`PrGrantScopeMode.ALL`, not a row per channel.
A grant written as "the eleven channels that exist today" silently stops
covering the twelfth, and nobody notices until an approval is refused; a grant
written as ``ALL`` means what the person granting it meant. ``ALL`` also covers
the missing case - an ``ALL`` channel scope covers an item with no targets -
because "every channel including none of them" is the only reading of *all* that
does not have a hole in it.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum

from meobot.domain.pr.models import PrContentType


class PrGrantScopeMode(StrEnum):
    """How one axis of a grant's scope is expressed.

    Two values, and the asymmetry is deliberate: ``ALL`` needs no list and
    survives new channels and new content types, while ``SELECTED`` is exactly
    the set written down beside it and never widens on its own.
    """

    #: Every value on this axis, present and future, including the unset case.
    ALL = "ALL"
    #: Only the values recorded against the grant.
    SELECTED = "SELECTED"


@dataclass(frozen=True, slots=True)
class ContentScopeKey:
    """The two canonical facts a scope decision is taken against.

    Built from a content item and its targets, never from labels. ``None`` and
    the empty frozenset are the real, expected states described in the module
    docstring - not missing data.
    """

    content_type: PrContentType | None
    channel_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class GrantScope:
    """Where one approval grant applies.

    Fail-closed defaults: an instance built with no arguments is ``SELECTED``
    on both axes with nothing selected, and therefore covers nothing. That is
    the shape a bug produces, and it refuses rather than admits.
    """

    content_type_scope: PrGrantScopeMode = PrGrantScopeMode.SELECTED
    content_types: frozenset[PrContentType] = field(default_factory=frozenset)
    include_unclassified_content: bool = False
    channel_scope: PrGrantScopeMode = PrGrantScopeMode.SELECTED
    channel_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    include_unassigned_channel: bool = False

    @classmethod
    def everything(cls) -> GrantScope:
        """Both axes ``ALL``. What a pre-Step-1F.2.7 grant is migrated to."""
        return cls(
            content_type_scope=PrGrantScopeMode.ALL,
            channel_scope=PrGrantScopeMode.ALL,
            include_unclassified_content=True,
            include_unassigned_channel=True,
        )

    @property
    def is_empty(self) -> bool:
        """True when this scope can never match anything.

        A ``SELECTED`` axis with nothing selected and no include flag. Refused
        at the write rather than stored: a grant that covers nothing is a
        grant somebody meant to be a grant.
        """
        return self._axis_empty(
            self.content_type_scope, bool(self.content_types), self.include_unclassified_content
        ) or self._axis_empty(
            self.channel_scope, bool(self.channel_ids), self.include_unassigned_channel
        )

    @staticmethod
    def _axis_empty(mode: PrGrantScopeMode, has_values: bool, include_unset: bool) -> bool:
        return mode is PrGrantScopeMode.SELECTED and not has_values and not include_unset

    def covers_content_type(self, content_type: PrContentType | None) -> bool:
        """Whether this grant's classification axis admits ``content_type``."""
        if self.content_type_scope is PrGrantScopeMode.ALL:
            return True
        if content_type is None:
            return self.include_unclassified_content
        return content_type in self.content_types

    def covers_channels(self, channel_ids: Collection[uuid.UUID]) -> bool:
        """Whether this grant's channel axis admits **every** given channel.

        Subset, not intersection - see the module docstring. An empty
        collection is the unassigned case and takes the include flag.
        """
        if self.channel_scope is PrGrantScopeMode.ALL:
            return True
        if not channel_ids:
            return self.include_unassigned_channel
        return set(channel_ids) <= set(self.channel_ids)

    def covers(self, key: ContentScopeKey) -> bool:
        """Both axes, conjoined. The whole scope decision, in one place."""
        return self.covers_content_type(key.content_type) and self.covers_channels(key.channel_ids)


__all__: list[str] = ["ContentScopeKey", "GrantScope", "PrGrantScopeMode"]
