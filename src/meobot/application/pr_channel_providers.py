"""Which platform has a connector, and how to build it.

Step 1F.2.4b. One lookup, deterministic, with no fallback.

The absent fallback is the design. A registry that returned some generic
provider for an unregistered platform would turn "MeoBot cannot sync TikTok"
into a runtime failure somewhere deep in a sync job, days later, with a
confusing error. Here it is a typed refusal at the point of the request, and the
UI turns it into a sentence: *"Đồng bộ API tự động chưa được hỗ trợ cho nền tảng
này."*

Step 1F.2.4b registered exactly one platform, and the two steps since have each
added entries without changing anything above this line - which was the promise
the port made. Step 1F.2.4c added Facebook and Instagram; Step 1F.2.6 added
TikTok.

Website and Other remain **absent**, and there is no class for them anywhere in
the tree - not even a stub raising ``NotImplementedError``. A stub would put
real-sounding class names in the codebase for a reader to mistake for
integrations that exist, and would make the "is this supported" question a
matter of reading a method body instead of a mapping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from meobot.core.config import Settings
from meobot.domain.pr.channel_metrics import ChannelMetricsProvider, PrChannelPlatform
from meobot.domain.pr.errors import PrValidationError
from meobot.integrations.meta.client import MetaGraphClient
from meobot.integrations.meta.constants import MetaEndpoints
from meobot.integrations.meta.provider import (
    FacebookChannelMetricsProvider,
    InstagramChannelMetricsProvider,
)
from meobot.integrations.tiktok.provider import TikTokChannelMetricsProvider
from meobot.integrations.youtube.provider import YouTubeChannelMetricsProvider


class PrConnectorNotConfiguredError(PrValidationError):
    """The platform has a connector, but this deployment has not set it up.

    Separate from "unsupported" because the two need opposite responses: an
    operator can fix this one by setting three environment variables, and
    telling them the platform is unsupported would send them to the wrong place
    entirely. The API turns it into *"Cấu hình YouTube chưa sẵn sàng"*.
    """

    code = "pr.connector_not_configured"


def _build_youtube(settings: Settings) -> ChannelMetricsProvider:
    if not settings.youtube_connector_enabled:
        raise PrConnectorNotConfiguredError(
            "Cấu hình YouTube chưa sẵn sàng. Hãy đặt YOUTUBE_OAUTH_CLIENT_ID, "
            "YOUTUBE_OAUTH_CLIENT_SECRET và PR_SECRET_ENCRYPTION_KEY.",
            details={"provider": PrChannelPlatform.YOUTUBE.value},
        )
    assert settings.youtube_oauth_client_id is not None
    assert settings.youtube_oauth_client_secret is not None
    return YouTubeChannelMetricsProvider(
        client_id=settings.youtube_oauth_client_id,
        client_secret=settings.youtube_oauth_client_secret.get_secret_value(),
        redirect_uri=settings.youtube_redirect_uri,
    )


def _meta_client(settings: Settings) -> MetaGraphClient:
    """The shared Graph transport both Meta providers sit on.

    One object, built the same way for Facebook and Instagram, because they are
    one API. The Graph version comes from a single setting - see
    :attr:`~meobot.core.config.Settings.meta_graph_api_version`.
    """
    if not settings.meta_connector_enabled:
        raise PrConnectorNotConfiguredError(
            "Cấu hình Meta chưa sẵn sàng. Hãy đặt META_APP_ID, META_APP_SECRET "
            "và PR_SECRET_ENCRYPTION_KEY.",
            details={"provider": "META"},
        )
    assert settings.meta_app_id is not None
    assert settings.meta_app_secret is not None
    return MetaGraphClient(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        redirect_uri=settings.meta_redirect_uri,
        endpoints=MetaEndpoints(version=settings.meta_graph_api_version),
    )


def _build_tiktok(settings: Settings) -> ChannelMetricsProvider:
    """The TikTok connector, on Login Kit and the Display API.

    No shared-transport helper of its own, unlike Meta's: TikTok is one platform
    with one provider, so the client is built inline here exactly as YouTube's
    is. A second function would be an abstraction over one caller.
    """
    if not settings.tiktok_connector_enabled:
        raise PrConnectorNotConfiguredError(
            "Cấu hình TikTok chưa sẵn sàng. Hãy đặt TIKTOK_CLIENT_KEY, "
            "TIKTOK_CLIENT_SECRET và PR_SECRET_ENCRYPTION_KEY.",
            details={"provider": PrChannelPlatform.TIKTOK.value},
        )
    assert settings.tiktok_client_key is not None
    assert settings.tiktok_client_secret is not None
    return TikTokChannelMetricsProvider(
        client_key=settings.tiktok_client_key,
        client_secret=settings.tiktok_client_secret.get_secret_value(),
        redirect_uri=settings.tiktok_redirect_uri,
    )


def _build_facebook(settings: Settings) -> ChannelMetricsProvider:
    return FacebookChannelMetricsProvider(_meta_client(settings))


def _build_instagram(settings: Settings) -> ChannelMetricsProvider:
    return InstagramChannelMetricsProvider(_meta_client(settings))


#: The whole registry. A platform absent from this mapping has no connector, and
#: that is a product statement rather than a gap somebody forgot to fill.
#:
#: Step 1F.2.4c added the two Meta entries and changed nothing else - no new
#: abstraction, no second registry, no branch anywhere above this line. That was
#: the promise Step 1F.2.4b made when it wrote the port, and adding a second
#: *platform family* is the test of it.
PROVIDER_BUILDERS: Mapping[PrChannelPlatform, Callable[[Settings], ChannelMetricsProvider]] = {
    PrChannelPlatform.YOUTUBE: _build_youtube,
    PrChannelPlatform.FACEBOOK: _build_facebook,
    PrChannelPlatform.INSTAGRAM: _build_instagram,
    PrChannelPlatform.TIKTOK: _build_tiktok,
}


def supports(platform: PrChannelPlatform | None) -> bool:
    """Whether a connector exists for this platform. No configuration involved."""
    return platform is not None and platform in PROVIDER_BUILDERS


def build_provider(
    platform: PrChannelPlatform | None, settings: Settings
) -> ChannelMetricsProvider:
    """The provider for ``platform``, or a refusal saying which kind of no.

    **No transport is threaded through here.** This module is application code
    and must not name an HTTP type - ``test_no_pr_service_imports_an_llm_or_telegram_client``
    enforces that, and it is right to: a PR service that could hold an
    ``httpx.AsyncClient`` is a PR service that could make a request. Injection
    for tests happens one layer down, where a test constructs
    :class:`~meobot.integrations.youtube.provider.YouTubeChannelMetricsProvider`
    with a ``MockTransport`` directly; the services take a whole ``provider_client``
    instead, which is a cleaner seam anyway.

    Args:
        platform: The channel's canonical platform. ``None`` for a channel whose
            platform is outside the canonical six.
        settings: Deployment configuration - the OAuth client lives here, never
            on a channel row.

    Raises:
        PrValidationError: No connector exists for this platform.
        PrConnectorNotConfiguredError: One exists and is not configured here.
    """
    builder = PROVIDER_BUILDERS.get(platform) if platform is not None else None
    if builder is None:
        raise PrValidationError(
            "Đồng bộ API tự động chưa được hỗ trợ cho nền tảng này.",
            details={
                "field": "platform",
                "reason": "connector_unsupported",
                "platform": platform.value if platform is not None else None,
                "supported": sorted(item.value for item in PROVIDER_BUILDERS),
            },
        )
    return builder(settings)


__all__: list[str] = [
    "PROVIDER_BUILDERS",
    "PrConnectorNotConfiguredError",
    "build_provider",
    "supports",
]
