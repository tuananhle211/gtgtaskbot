"""What a Page published in a window, and what it got for it.

Step 1F.2.4d. Page Insights answers *"how much engagement did this Page get"*
and cannot answer *"how many posts was that spread over"*, *"which one worked"*
or *"how much of it was shares rather than reactions"* - and those are the three
questions a PR manager asks second, immediately after the follower count.

The answers are on the posts themselves, so this module reads them, and it is
built around one constraint: **a Page's feed is unbounded and MeoBot's interest
in it is not.**

The bound, and why it is three separate bounds
-----------------------------------------------

* a **window**. Only the settled 30-day window is fetched, passed to Graph as
  ``since``/``until`` so the filtering happens on Meta's side rather than by
  downloading a Page's history and discarding most of it;
* a **page size** and a **page count**. :data:`~meobot.integrations.meta.constants.POST_PAGE_SIZE`
  rows at a time, at most :data:`~meobot.integrations.meta.constants.MAX_POST_PAGES`
  times, so one sync's post fetch costs a fixed, small number of requests
  whatever the Page does;
* **no per-post request, ever.** Reactions, comments and shares arrive as
  summaries on the *same* call that lists the posts - ``reactions.summary(total_count).limit(0)``
  returns the count and none of the rows. A hundred posts therefore cost the
  same four requests as one, where a per-post insights call would have cost a
  hundred and four.

Required data and optional data are asked for together and lost separately
--------------------------------------------------------------------------

That last bound had a sharp edge, and production found it. Graph does not serve
the fields it will and skip the ones it will not: **one refused field refuses
the whole request**. So a Page token holding ``pages_read_engagement`` - which
reads every post's id, timestamp, permalink, message and share count perfectly -
was refused ``reactions.summary`` and ``comments.summary``, because those are
other people's activity and Meta gates them separately, and the refusal took the
readable half down with it. A channel whose followers, fans and engagements were
all sitting there failed its entire sync with ``INSUFFICIENT_SCOPE``.

The fields are therefore two lists, not one: :data:`CORE_POST_FIELDS`, which
every post-derived count that does not involve another person is computed from,
and :data:`INTERACTION_SUMMARY_FIELDS`, which are asked for hopefully. One
request still asks for both. When that request is refused *as a request*, the
same window is asked for again with the core fields alone - once, at the same
cursor - and what comes back is a real window of real posts whose reaction and
comment counts are ``None``.

``None`` rather than ``0``, and :func:`field_availability` records which of its
five words applied, because "nobody reacted" and "we are not allowed to know"
are different facts and a card that shows ``0`` for the second one is lying.

The retry happens **at most once per walk**, so the post fetch stays bounded by
``MAX_POST_PAGES + 1`` requests and the bullet above still holds: no per-post
call, on any path.

Truncation is reported, never rounded off
------------------------------------------

A Page that published more posts in a month than
:data:`~meobot.integrations.meta.constants.MAX_POST_PAGES` pages of
:data:`~meobot.integrations.meta.constants.POST_PAGE_SIZE` can hold
hits the cap, and what MeoBot then holds is a *prefix* of the window rather than
the window. :attr:`PostWindow.truncated` says so, and the provider writes
``None`` into ``posts_count_30d``, ``reactions_30d``, ``comments_30d`` and
``shares_30d`` rather than a floor that would read on a card as a total.

That is the whole reason the flag exists. A truncated count is not a smaller
count; it is a different number wearing the right name, and it would be
invisible on the screen and wrong in every report built from it.

What is deliberately not fetched
---------------------------------

**Per-post video views.** Graph exposes them through ``/{post_id}/insights``,
which is one request per post - a hundred requests per Page per day for one
secondary card. Video plays are read at the Page level instead, from a metric
asked for alongside the others and left ``None`` when this Graph version does
not serve it. Under-reporting nothing is better than a rate-limit spiral.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from meobot.domain.pr.channel_analytics import TOP_POST_EXCERPT_LIMIT
from meobot.integrations.meta.client import parse_count

#: The fields a ``published_posts`` request asks for **and cannot do without**.
#:
#: Everything here answers a question that does not involve another person's
#: activity: what was published, when, where it lives, and how far the Page
#: itself spread it. ``posts_count_7d``, ``posts_count_30d``, the window's
#: coverage and its truncation flag are all computed from these alone, which is
#: the property that matters: a token that cannot read reactions can still
#: answer *how many posts, and when*.
#:
#: ``shares`` sits here rather than with the summaries because it is not one.
#: Graph returns ``{"count": n}`` when there are shares and **omits the field
#: entirely when there are none**, which is why :func:`parse_post` reads an
#: absent ``shares`` as ``0`` and an absent ``reactions`` as unavailable. The
#: two absences mean different things and this split is where that starts.
CORE_POST_FIELDS: tuple[str, ...] = (
    "id",
    "created_time",
    "permalink_url",
    "message",
    "shares",
)

#: The interaction summaries, which are **optional** and are what this split
#: exists for.
#:
#: ``.limit(0)`` asks Graph for the total and none of the rows: MeoBot needs
#: "how many comments", never the comments themselves, and downloading somebody's
#: comment threads to count them would be both slow and a quantity of other
#: people's writing that MeoBot has no reason to hold.
#:
#: They are optional because they are **other people's content**, and Meta gates
#: that behind a permission this app was never granted. A Page token holding
#: ``pages_show_list``, ``pages_read_engagement`` and ``read_insights`` reads
#: every field in :data:`CORE_POST_FIELDS` and is refused these two - and Graph
#: refuses the *whole request* rather than answering with the fields it will
#: serve, which is how two optional counts took a channel's entire sync down
#: with ``INSUFFICIENT_SCOPE``. See
#: :meth:`~meobot.integrations.meta.provider.FacebookChannelMetricsProvider._fetch_post_window`
#: for the retry that now contains it.
INTERACTION_SUMMARY_FIELDS: tuple[str, ...] = (
    "reactions.summary(total_count).limit(0)",
    "comments.summary(total_count).limit(0)",
)

#: The core fields as one ``fields=`` parameter. The listing MeoBot falls back
#: to when Graph will not serve the summaries.
CORE_POST_FIELD_SPEC = ",".join(CORE_POST_FIELDS)

#: Core plus summaries. What one request asks for first, because when it is
#: granted a hundred posts still cost the same four requests as one.
POST_FIELDS = ",".join((*CORE_POST_FIELDS, *INTERACTION_SUMMARY_FIELDS))

#: Graph answered and there was something in it.
POST_FIELD_AVAILABLE = "available"
#: Graph answered and there was nothing in it - no posts in the window, or no
#: post that was ever shared. A measurement, not a gap.
POST_FIELD_EMPTY = "empty"
#: Graph refused the *caller*: this grant does not cover this field. The fix is
#: a wider consent, and until there is one the count is ``NULL`` rather than 0.
POST_FIELD_NOT_PERMITTED = "not_permitted"
#: Graph answered the listing and simply did not include the field. Neither a
#: refusal nor a zero, and worth its own word so a snapshot read months later
#: does not have to guess which it was.
POST_FIELD_ABSENT = "absent"
#: The listing itself could not be read, so nothing about its fields is known.
POST_FIELD_NOT_READ = "not_read"


@dataclass(frozen=True, slots=True)
class FacebookPost:
    """One published post, with the three counts that arrived beside it.

    ``reactions`` and ``comments`` are ``int | None`` because Graph may decline
    the summary; ``shares`` is a plain ``int`` because its absence is
    unambiguous - Graph omits the field when a post has no shares, so nothing
    can be lost by reading that as zero.
    """

    post_id: str
    created_time: datetime | None = None
    permalink_url: str | None = None
    message: str | None = None
    reactions: int | None = None
    comments: int | None = None
    shares: int = 0

    @property
    def engagements(self) -> int:
        """Reactions + comments + shares, counting an unavailable summary as 0.

        The one place in this step where a missing value is treated as zero, and
        it is defensible only because of what this number is used for: ranking
        posts against each other to find the month's best. A post whose reaction
        summary Graph declined is not a post with no reactions, but it is a post
        MeoBot cannot rank on them, and excluding it from the ranking entirely
        would be worse than ranking it on what did arrive.

        Nothing stored in a canonical column is computed from this property -
        the aggregate sums in :func:`summarize` refuse to add an unavailable
        summary and go ``None`` instead, which is the behaviour a card needs.
        """
        return (self.reactions or 0) + (self.comments or 0) + self.shares


@dataclass(frozen=True, slots=True)
class PostWindow:
    """Every post MeoBot read for one window, and whether that is all of them."""

    posts: tuple[FacebookPost, ...] = ()
    #: ``True`` when the page cap was reached and Graph still had more. The
    #: window is then a prefix, and every count over it is a floor rather than a
    #: total - see the module docstring.
    truncated: bool = False
    #: ``False`` when Graph refused :data:`INTERACTION_SUMMARY_FIELDS` and this
    #: window was read with :data:`CORE_POST_FIELDS` alone.
    #:
    #: The posts are then real and complete; only their reaction and comment
    #: counts are unknown, and unknown is not zero. Recorded on the window
    #: rather than inferred from the posts because the two are genuinely
    #: different situations: a window whose summaries were *refused* and a
    #: window of posts that simply have no reactions both come back with the
    #: same rows.
    summaries_permitted: bool = True


@dataclass(frozen=True, slots=True)
class PostTotals:
    """The aggregate of one window's posts. Every field may be ``None``.

    ``None`` propagates from any post whose summary was unavailable, because a
    sum missing one term is not a sum - it is a smaller number with no way to
    tell. ``0`` on the other hand is a real answer and survives: a Page that
    posted three times and got nothing back really has ``reactions = 0``.
    """

    posts_count: int | None = None
    reactions: int | None = None
    comments: int | None = None
    shares: int | None = None
    top_post: FacebookPost | None = None


def parse_post(row: Mapping[str, Any]) -> FacebookPost | None:
    """One Graph row as a post, or ``None`` when it is not usable.

    ``None`` for a row with no id: it cannot be counted, deduplicated or linked
    to, and keeping it would inflate a post count with something that is not a
    post. Every other field degrades on its own - an unparseable timestamp
    leaves ``created_time`` empty and the post is still counted, because *how
    many posts* does not depend on being able to read *when*.
    """
    post_id = _text(row.get("id"))
    if post_id is None:
        return None
    return FacebookPost(
        post_id=post_id,
        created_time=_moment(row.get("created_time")),
        permalink_url=_text(row.get("permalink_url")),
        message=_text(row.get("message")),
        reactions=_summary_total(row.get("reactions")),
        comments=_summary_total(row.get("comments")),
        # Absent means none. Graph omits ``shares`` for an unshared post rather
        # than sending a zero, so this is the one count where absence is known
        # to be zero rather than unknown.
        shares=_shares(row.get("shares")),
    )


def summarize(window: PostWindow) -> PostTotals:
    """Total one window's posts, refusing to total what is missing.

    Everything is ``None`` when the window was truncated. That is not caution
    for its own sake: the counts really are unknown, because the posts the cap
    cut off had reactions too, and a partial sum on a card headed "Reactions"
    would be read as the month's reactions by everybody who saw it.
    """
    if window.truncated:
        return PostTotals()
    posts = window.posts
    return PostTotals(
        posts_count=len(posts),
        reactions=_sum_or_none(post.reactions for post in posts),
        comments=_sum_or_none(post.comments for post in posts),
        # Always available; see :func:`parse_post`.
        shares=sum(post.shares for post in posts),
        top_post=max(posts, key=_ranking) if posts else None,
    )


def covers(window: PostWindow, *, since: datetime) -> bool:
    """Whether this window certainly holds every post published at or after ``since``.

    A truncated window is not useless - it is truncated at the **old** end.
    ``published_posts`` returns newest first, so the cap cuts the oldest posts
    off, and a 7-day slice of a 30-day fetch that ran out of pages is still
    complete *provided the cut fell earlier than seven days back*. This is that
    proviso, checked rather than assumed: if the oldest post MeoBot actually
    read is itself inside the shorter window, the cut fell inside it too and the
    slice is a prefix like its parent.

    A truncated window with no readable dates cannot be shown to cover
    anything, so it covers nothing.
    """
    if not window.truncated:
        return True
    dated = [post.created_time for post in window.posts if post.created_time is not None]
    return bool(dated) and min(dated) < since


def posts_within(window: PostWindow, *, start: datetime, end: datetime) -> PostWindow:
    """The subset published inside ``[start, end)``, as its own window.

    How the 7-day figures are produced without a second Graph call: the 30-day
    fetch already holds them, and filtering a list MeoBot has is free where
    asking Meta again is not.

    A post whose ``created_time`` could not be read is **excluded**, not assumed
    to be inside. Counting an undated post in a 7-day window would move it there
    on no evidence.

    The result inherits truncation from :func:`covers` rather than from its
    parent: a 7-day slice of a truncated 30-day fetch is usually complete, and
    reporting it as truncated would blank a card that is perfectly good.
    """
    return PostWindow(
        posts=tuple(
            post
            for post in window.posts
            if post.created_time is not None and start <= post.created_time < end
        ),
        truncated=not covers(window, since=start),
        # A slice of a listing read without summaries was read without them too.
        summaries_permitted=window.summaries_permitted,
    )


def top_post_payload(post: FacebookPost) -> dict[str, Any]:
    """The best post, shaped for ``extra_metrics``.

    Plain JSON types only - this is stored in a JSON column and read back by
    :func:`~meobot.domain.pr.channel_analytics.top_post_from`, which validates
    every field again on the way out because a stored blob is untrusted input by
    the time anybody reads it.
    """
    return {
        "post_id": post.post_id,
        "engagements": post.engagements,
        "permalink_url": post.permalink_url,
        "created_time": post.created_time.isoformat() if post.created_time else None,
        # Truncated on the way *in*. A summary card does not need the whole
        # post, and a JSON column is not where a Page's copy belongs.
        "excerpt": _excerpt(post.message),
        "reactions": post.reactions,
        "comments": post.comments,
        "shares": post.shares,
    }


def field_availability(window: PostWindow | None) -> dict[str, str]:
    """Which post fields this reading actually got, as safe words for storage.

    Written into ``extra_metrics`` so that a blank ``reactions_30d`` can be
    explained a year later without re-running anything. Three of the values it
    can take are the same three the capability probe reports, deliberately: an
    operator comparing a snapshot with a probe run should not have to translate.

    **Nothing provider-shaped travels in here.** No error payload, no message,
    no code, no token - one of five words this module chose, per field. A Graph
    error string is somebody else's prose about somebody else's system and it
    would end up on a Vietnamese screen if it were allowed to travel.
    """
    if window is None:
        # The listing itself was unreadable. Nothing is known about its fields,
        # and saying "not permitted" would be inventing a cause.
        return dict.fromkeys(("reactions", "comments", "shares"), POST_FIELD_NOT_READ)
    posts = window.posts
    return {
        "reactions": _summary_availability(window, tuple(post.reactions for post in posts)),
        "comments": _summary_availability(window, tuple(post.comments for post in posts)),
        # Never refused on its own: ``shares`` is the Page's own content and
        # rides on the core listing, so the only question it can answer is
        # whether anything was actually shared.
        "shares": (
            POST_FIELD_AVAILABLE if any(post.shares for post in posts) else POST_FIELD_EMPTY
        ),
    }


def _summary_availability(window: PostWindow, values: tuple[int | None, ...]) -> str:
    """One optional summary's fate, in the order the causes have to be ruled out."""
    if not window.summaries_permitted:
        return POST_FIELD_NOT_PERMITTED
    if not values:
        return POST_FIELD_EMPTY
    if all(value is None for value in values):
        # Graph served the listing and left the summary out of every row. Not a
        # refusal - it never said no - and not a zero either.
        return POST_FIELD_ABSENT
    return POST_FIELD_AVAILABLE


# ---------------------------------------------------------------------------
# Reading Graph's own JSON, which is untrusted input
# ---------------------------------------------------------------------------


def _ranking(post: FacebookPost) -> tuple[int, str]:
    """Engagements, then post id, so "the best post" is never ambiguous.

    Two posts with identical engagement must not swap places between two syncs -
    the panel would show a different "bài tốt nhất" every day for no reason.
    """
    return post.engagements, post.post_id


def _summary_total(value: Any) -> int | None:
    """``{"summary": {"total_count": n}}`` as an int, or ``None``."""
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    if not isinstance(summary, dict):
        return None
    return parse_count(summary.get("total_count"))


def _shares(value: Any) -> int:
    """``{"count": n}`` as an int; anything else - including absence - as 0."""
    if not isinstance(value, dict):
        return 0
    return parse_count(value.get("count")) or 0


def _sum_or_none(values: Iterable[int | None]) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _excerpt(message: str | None) -> str | None:
    if message is None:
        return None
    trimmed = " ".join(message.split())
    if len(trimmed) <= TOP_POST_EXCERPT_LIMIT:
        return trimmed or None
    return trimmed[:TOP_POST_EXCERPT_LIMIT].rstrip() + "…"


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _moment(value: Any) -> datetime | None:
    """Graph's ``2026-08-14T09:12:33+0000`` as an aware UTC datetime.

    Meta writes the offset without a colon, which ``fromisoformat`` accepts from
    Python 3.11 onwards. A value it will not parse leaves the post undated
    rather than raising: the post still counts towards the month.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__: list[str] = [
    "CORE_POST_FIELDS",
    "CORE_POST_FIELD_SPEC",
    "INTERACTION_SUMMARY_FIELDS",
    "POST_FIELDS",
    "POST_FIELD_ABSENT",
    "POST_FIELD_AVAILABLE",
    "POST_FIELD_EMPTY",
    "POST_FIELD_NOT_PERMITTED",
    "POST_FIELD_NOT_READ",
    "FacebookPost",
    "PostTotals",
    "PostWindow",
    "covers",
    "field_availability",
    "parse_post",
    "posts_within",
    "summarize",
    "top_post_payload",
]
