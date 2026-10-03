"""Meta Graph endpoints, the API version, and the scopes MeoBot asks for.

Every URL the Meta connector will ever contact is built from the literals in
this module. That is the SSRF answer, and it is the same one
:mod:`meobot.integrations.youtube.constants` gives: there is no code path where
a channel row, a request body, or a field inside a Graph response can decide
where a request goes. In particular the connector **never fetches
``pr_channels.url``** - a person typed that.

One version, in one place
-------------------------

:data:`DEFAULT_GRAPH_VERSION` is the only Graph version string in the codebase,
and it is overridable through ``META_GRAPH_API_VERSION`` so that bumping it is a
configuration change rather than a patch. MeoBot never calls an unversioned
Graph endpoint: unversioned means "whatever Meta considers current today", which
turns a Meta release into an unannounced change in MeoBot's behaviour.

Meta retires a Graph version roughly two years after it ships, so this value has
a shelf life. It is a setting precisely so that the response to a deprecation
notice is a restart.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The Graph version this connector was written and tested against.
#:
#: **Verify before deploying.** Meta publishes a changelog and a deprecation
#: schedule; a version that has been retired answers with an error rather than
#: with data, and the fix is ``META_GRAPH_API_VERSION`` rather than a release.
DEFAULT_GRAPH_VERSION = "v23.0"

#: Scopes for a **Facebook Page** connection.
#:
#: Three, all read-only, and each earns its place:
#:
#: * ``pages_show_list`` - enumerate the Pages this person manages. Without it
#:   there is no discovery step and no way to offer a choice;
#: * ``pages_read_engagement`` - read a Page's own fields, which is where
#:   ``followers_count`` lives, and obtain the Page access token;
#: * ``read_insights`` - read Page Insights. This is the only one that returns
#:   the windowed metrics, and nothing else grants it.
#:
#: Deliberately **not** requested: ``pages_manage_posts``, ``pages_manage_engagement``,
#: ``publish_video``, ``pages_messaging``, ``ads_management``, ``ads_read``,
#: ``business_management``. MeoBot reads numbers. A consent screen asking a
#: marketing manager to grant posting or messaging rights so a dashboard can
#: show a follower count is asking for the wrong thing, and the blast radius of
#: a leaked grant is the difference between these three lines and those.
#:
#: Also not requested, and this one is a live question rather than an obvious
#: no: **``pages_read_user_content``**. It is what a Page post's
#: ``reactions.summary`` and ``comments.summary`` need - a reaction and a comment
#: are somebody else's content, which ``pages_read_engagement`` does not cover -
#: so without it ``reactions_30d`` and ``comments_30d`` are permanently ``NULL``.
#: The connector degrades around that rather than failing, see
#: :mod:`meobot.integrations.meta.posts`.
#:
#: Three things stand between here and adding it, and none is a technical one:
#: it needs **App Review**; it is a **wider grant than the two counts justify**,
#: because the permission that counts comments also reads them and can delete
#: them; and adding it would require **every existing Page connection to
#: reauthorize**, since a scope added to the app does not appear in a token
#: already issued. Adding a line here is the last step of that decision, not the
#: first.
FACEBOOK_SCOPES: tuple[str, ...] = (
    "pages_show_list",
    "pages_read_engagement",
    "read_insights",
)

#: Scopes for an **Instagram professional account** connection.
#:
#: Instagram professional accounts are reached *through* the Facebook Page they
#: are linked to - that is Meta's model, not a shortcut - so the two Page scopes
#: are required to find the account at all, plus:
#:
#: * ``instagram_basic`` - read the account's id, username, ``followers_count``
#:   and ``media_count``;
#: * ``instagram_manage_insights`` - read Instagram Insights. Despite the name
#:   this is the **read** permission for insights; Meta has no
#:   ``instagram_read_insights``, which is worth writing down because the name
#:   invites the assumption that something is being managed.
#:
#: Not requested: ``instagram_content_publish``, ``instagram_manage_comments``,
#: ``instagram_manage_messages``.
INSTAGRAM_SCOPES: tuple[str, ...] = (
    "pages_show_list",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_manage_insights",
)


@dataclass(frozen=True, slots=True)
class MetaEndpoints:
    """Meta's OAuth and Graph hosts, for one API version.

    A frozen dataclass rather than loose module constants so a test can point
    the whole connector at a local base URL without monkey-patching a module,
    while production has no configuration surface that could redirect it:
    nothing reads a host from the environment, only the version.
    """

    version: str = DEFAULT_GRAPH_VERSION
    authorize_base: str = "https://www.facebook.com"
    graph_base: str = "https://graph.facebook.com"

    @property
    def authorize(self) -> str:
        """The consent dialog. Versioned, like everything else."""
        return f"{self.authorize_base}/{self.version}/dialog/oauth"

    @property
    def graph(self) -> str:
        """The versioned Graph root every API call hangs off."""
        return f"{self.graph_base}/{self.version}"

    @property
    def token(self) -> str:
        return f"{self.graph}/oauth/access_token"

    def node(self, node_id: str, edge: str = "") -> str:
        """A Graph node, or one of its edges.

        ``node_id`` always comes from a Graph response or from a stored
        connection - never from a request body. The selection endpoint verifies
        a chosen id against the server-side discovery result before anything is
        built from it, which is what stops a caller naming an arbitrary node.
        """
        return f"{self.graph}/{node_id}/{edge}" if edge else f"{self.graph}/{node_id}"


#: Seconds. Generous enough for Insights, which is slower than a field read, and
#: bounded because a hung socket must not hold a worker slot until Celery's task
#: time limit.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: How many days behind "today" Meta Insights is assumed to be settled.
#:
#: Meta documents that insights can take up to 48 hours to finalise, and a
#: window whose last day is still moving would produce a different idempotency
#: fingerprint on every retry - which would turn the deduplication of Step
#: 1F.2.4b into a no-op. Two days is the conservative choice that makes a window
#: reproducible, and it matches what the YouTube connector settled on for the
#: same reason.
INSIGHTS_LAG_DAYS = 2

#: How many posts one ``published_posts`` request asks for.
#:
#: Twenty-five rather than Graph's maximum, because each row carries three
#: interaction summaries and a message body: a page of 100 is a large response
#: to hold in memory and to time out on, and the number of *requests* saved is
#: the thing that matters, which :data:`MAX_POST_PAGES` already bounds.
POST_PAGE_SIZE = 25

#: How many such pages one sync will ever ask for.
#:
#: Four - a hundred posts in a thirty-day window, or better than three posts a
#: day, which is well above what the department's busiest channel publishes. A
#: Page that exceeds it is **not** silently summarised from the prefix: the
#: window is marked truncated and every count over it is stored as ``NULL``. See
#: :mod:`meobot.integrations.meta.posts`.
#:
#: The cap exists because a Page's feed has no end and a sync must have one. Set
#: here as a constant rather than a setting on purpose: an operator raising it
#: to "get the full numbers" would be raising the request cost of every sync of
#: every Facebook channel to fix one channel's card, and the honest fix for that
#: channel is a card that says the month was too busy to count.
MAX_POST_PAGES = 4

__all__: list[str] = [
    "DEFAULT_GRAPH_VERSION",
    "DEFAULT_TIMEOUT_SECONDS",
    "FACEBOOK_SCOPES",
    "INSIGHTS_LAG_DAYS",
    "INSTAGRAM_SCOPES",
    "MAX_POST_PAGES",
    "POST_PAGE_SIZE",
    "MetaEndpoints",
]
