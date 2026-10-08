"""Application settings loaded from environment variables / ``.env``.

Settings are immutable and cached per process. Never log a :class:`Settings`
instance directly - secrets are wrapped in :class:`~pydantic.SecretStr` but the
safest habit is to log :meth:`Settings.safe_summary` instead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AliasChoices,
    BeforeValidator,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production", "test"]
LogFormat = Literal["json", "console"]


def _empty_to_none(value: Any) -> Any:
    """Treat empty/whitespace-only env values as unset.

    ``.env`` files commonly contain placeholder keys such as ``LLM_API_KEY=``.
    Without this, Pydantic would try to coerce ``""`` into ``int``/``SecretStr``.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


OptionalStr = Annotated[str | None, BeforeValidator(_empty_to_none)]
OptionalInt = Annotated[int | None, BeforeValidator(_empty_to_none)]
OptionalSecret = Annotated[SecretStr | None, BeforeValidator(_empty_to_none)]


class Settings(BaseSettings):
    """Typed view over the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- Application ------------------------------------------------------
    app_env: AppEnv = "development"
    app_name: str = "TasksBot"
    app_timezone: str = "Asia/Ho_Chi_Minh"
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    # --- API --------------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - container-internal bind, published on 127.0.0.1
    api_port: int = 8000
    api_docs_enabled: bool = True

    # --- Database ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://meobot:change_me@postgres:5432/meobot"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- Redis / Celery ---------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
    celery_task_always_eager: bool = False

    # --- Telegram ---------------------------------------------------------
    telegram_bot_token: OptionalSecret = None
    meobot_owner_telegram_id: OptionalInt = None

    # --- LLM --------------------------------------------------------------
    llm_provider: str = "fake"
    llm_api_key: OptionalSecret = None
    llm_model: OptionalStr = None
    #: Override for OpenAI-compatible gateways (OpenRouter, vLLM, ...).
    llm_base_url: OptionalStr = None
    #: LLM calls are slower than ordinary HTTP; they get their own budget.
    llm_timeout_seconds: float = Field(default=90.0, gt=0)

    # --- Integrations -----------------------------------------------------
    google_service_account_file: OptionalStr = None
    #: Shared Drive ("Team Drive") that owns MeoBot's folders. Strongly
    #: recommended: a file created by a service account in *My Drive* has no
    #: human owner and cannot be recovered by the team.
    google_shared_drive_id: OptionalStr = None
    #: When set, MeoBot refuses to register or use any folder outside this tree.
    google_drive_root_folder_id: OptionalStr = None
    #: Human-designed templates copied by ``files.copy``. Optional: without
    #: them MeoBot generates a blank standard spreadsheet instead.
    google_work_sheet_template_id: OptionalStr = None
    google_script_sheet_template_id: OptionalStr = None
    meta_app_id: OptionalStr = None
    meta_app_secret: OptionalSecret = None
    tiktok_client_key: OptionalStr = None
    tiktok_client_secret: OptionalSecret = None

    # --- Conversation (natural chat) --------------------------------------
    #: Master switch. When false, free-text messages still go through the
    #: planner, but ``mode=chat`` replies are refused with an explanation.
    chat_enabled: bool = True
    #: Recent messages replayed to the provider. The rolling summary carries
    #: everything older, so the prompt stays bounded no matter how long the
    #: thread is.
    chat_history_max_messages: int = Field(default=20, ge=2, le=100)
    #: Messages in a thread before summarisation is worth running.
    chat_summary_trigger_messages: int = Field(default=30, ge=4, le=500)
    chat_response_max_tokens: int = Field(default=1200, ge=64, le=8000)
    chat_typing_indicator: bool = True
    #: In a group, only answer when addressed. Turning this off makes MeoBot
    #: reply to every message in the group, which is rarely what anyone wants.
    chat_group_requires_mention: bool = True

    # --- Member interaction (0.6.0A) --------------------------------------
    #: How long a half-finished Member flow (filing leave, submitting proof)
    #: survives. Long enough to answer a question after a meeting, short enough
    #: that a forgotten flow does not silently capture tomorrow's message.
    member_flow_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    #: How long "việc số 2" keeps meaning what it meant. Shorter than the flow
    #: TTL on purpose: a stale numbered reference is the dangerous one.
    member_list_context_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    member_max_proofs_per_submission: int = Field(default=5, ge=1, le=20)
    #: Telegram refuses very tall keyboards, and a list nobody can scan is not
    #: a list. Longer results are truncated with a count.
    member_max_button_items: int = Field(default=8, ge=1, le=30)
    member_typing_indicator: bool = True
    member_show_ai_allowance_on_home: bool = True
    #: Turn off if a house style ever collides with the abbreviation table;
    #: accents and case are still folded, only expansion stops.
    member_vietnamese_normalization_enabled: bool = True

    # --- Cross-chat notification routing (0.6.0a1) -------------------------
    #: Master switch. Off means business actions still commit and outbound
    #: rows are still written - only the worker stops sending them.
    notification_outbox_enabled: bool = True
    notification_worker_batch_size: int = Field(default=20, ge=1, le=200)
    notification_max_attempts: int = Field(default=5, ge=1, le=20)
    notification_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    notification_retry_max_seconds: int = Field(default=1800, ge=1, le=86400)
    #: A claim older than this belongs to a worker that died. Far longer than
    #: any real send, so a live delivery is never stolen.
    notification_processing_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    notification_callback_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    notification_read_receipt_enabled: bool = True
    #: Post neutral approved-absence updates to the registered attendance group.
    notification_attendance_group_enabled: bool = True
    #: Guard against a typo turning one announcement into hundreds of sends.
    notification_max_broadcast_destinations: int = Field(default=5, ge=1, le=50)

    # --- Destination health, failure alerts and replay (0.6.0a2) ----------
    #: Probe registered groups with ``getChat``/``getChatMember`` on a schedule
    #: instead of waiting for a real message to fail against them. Never sends
    #: a visible test message, and never probes a private chat.
    notification_destination_health_enabled: bool = True
    notification_destination_health_interval_seconds: int = Field(default=1800, ge=300, le=86400)
    #: Bounded so a deployment with many groups spreads its probes over several
    #: sweeps rather than making one burst of API calls.
    notification_destination_health_batch_size: int = Field(default=10, ge=1, le=100)
    #: Tell whoever asked for a message that it could not be delivered, rather
    #: than waiting for them to think of asking.
    notification_failure_alert_enabled: bool = True
    #: Also tell them when a destination that had failed starts working again.
    notification_recovery_alert_enabled: bool = True
    #: How many consecutive unhealthy probes before a destination is believed
    #: to be broken. One is not enough: Telegram has brief outages.
    notification_health_failure_threshold: int = Field(default=2, ge=1, le=10)

    #: Answer a stranger's original question automatically once the owner
    #: approves it, instead of making them ask again.
    notification_guest_replay_enabled: bool = True
    #: How long an undecided question stays answerable. Past this the person has
    #: moved on, and a reply to a day-old message is worse than none.
    deferred_guest_message_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    #: How long a resolved question's metadata is kept once its text is purged.
    deferred_guest_message_retention_seconds: int = Field(default=2592000, ge=3600, le=31536000)

    # --- Announcement read receipts (0.6.0a2) -----------------------------
    announcement_read_receipt_enabled: bool = True
    announcement_read_callback_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    #: Ceiling on a "nhắc những người chưa đọc" fan-out. A reminder that goes to
    #: eighty people by accident cannot be recalled.
    announcement_max_private_reminders: int = Field(default=30, ge=1, le=200)

    # --- Reminders (0.6.0a2) ----------------------------------------------
    reminder_enabled: bool = True
    #: The due sweep. Every minute: a reminder that fires 40 seconds late is
    #: fine, one that fires 15 minutes late is not a reminder.
    reminder_sweep_interval_seconds: int = Field(default=60, ge=15, le=3600)
    reminder_batch_size: int = Field(default=50, ge=1, le=500)
    #: How late a missed firing may be and still be delivered. Beyond this the
    #: occurrence is recorded as skipped - advice about a moment two days gone
    #: is noise, and delivering every missed one turns a restart into a flood.
    reminder_missed_grace_seconds: int = Field(default=3600, ge=60, le=86400)
    #: Falls back to ``APP_TIMEZONE`` when empty, which is the normal case.
    reminder_default_timezone: str = ""

    # --- Assistant identity and workspace ---------------------------------
    #: Rendered into the ``[WORKSPACE]`` prompt section and shown by
    #: ``/assistant_profile``. Configuration rather than constants so a second
    #: deployment needs no code change and no private detail is committed.
    meobot_organization_name: str = ""
    meobot_department_name: str = ""
    meobot_department_size: str = ""
    #: Seeds the configured OWNER's profile on first contact. The profile row
    #: wins afterwards - these are defaults, not overrides.
    meobot_owner_title: str = ""
    meobot_owner_preferred_address: str = ""

    # --- Behaviour --------------------------------------------------------
    confirmation_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    http_timeout_seconds: float = Field(default=15.0, gt=0)
    http_max_retries: int = Field(default=2, ge=0, le=5)
    #: How long a half-finished Telegram conversation (adding a sheet, typing a
    #: revision comment) survives before it is cleaned up.
    conversation_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    #: How often ``sheets.sync_all_active_profiles`` runs from Celery beat.
    sheet_sync_interval_seconds: int = Field(default=1800, ge=300, le=86400)
    #: Automatically review scripts that are waiting for one.
    auto_review_enabled: bool = True

    # --- PR AI review execution (Step 1F) ---------------------------------
    #: Master switch. Off means content still enters ``AI_REVIEW`` and simply
    #: waits there for a person to move it, which is the pre-Step-1F behaviour
    #: and is safe - nothing is skipped, and no human gate is bypassed.
    pr_ai_review_enabled: bool = True
    #: How many times one execution may be started before it is failed for
    #: good. Bounds the spend when a provider is misbehaving.
    pr_ai_review_max_attempts: int = Field(default=3, ge=1, le=10)
    #: How often the sweeper looks for queued executions. This is the latency
    #: between entering ``AI_REVIEW`` and the review starting.
    pr_ai_review_sweep_interval_seconds: int = Field(default=20, ge=5, le=600)
    #: A run claimed longer ago than this is assumed lost to a dead worker and
    #: is re-queued. Comfortably longer than the task time limit.
    pr_ai_review_stale_after_seconds: int = Field(default=900, ge=60, le=7200)

    # --- PR platform policy ingestion (Step 1F.1) -------------------------
    #: Master switch for the scheduled re-read of official policy pages. Off
    #: leaves every ACTIVE pack in place and simply stops looking for changes;
    #: reviews keep working, because refresh never touched them anyway.
    pr_policy_refresh_enabled: bool = True
    #: How often to re-read. Daily by default: policy pages change rarely, and
    #: a snapshot is only useful when something actually moved.
    pr_policy_refresh_interval_seconds: int = Field(default=86400, ge=3600, le=604800)

    # --- PR channel connectors (Step 1F.2.4b) -----------------------------
    #: Key for secrets that have to live in the database and be read back -
    #: today, exactly one thing: a channel connector's OAuth refresh token.
    #: 32 bytes, base64. See :mod:`meobot.core.secrets` for the construction and
    #: for why hashing (what ``web_sessions`` does) is not an option here.
    #:
    #: Unset is a supported state. The application starts, everything else
    #: works, and the YouTube connector reports that it is not configured -
    #: refusing to boot would take the bot down over a feature this deployment
    #: is not using.
    pr_secret_encryption_key: OptionalSecret = None
    #: Alternative delivery: a file containing the same base64 key. This is how
    #: the deployment already hands the Google service account to the services
    #: that need it - a host file mounted read-only at ``/run/secrets``.
    pr_secret_encryption_key_file: OptionalStr = None
    #: Names the key new values are written with. Only worth setting when
    #: rotating, and then it must not collide with a retired id.
    # S105 fires on the *name* resembling a credential. This is a key
    # identifier - it names which key was used, and is written into every
    # ciphertext envelope in plaintext on purpose.
    pr_secret_encryption_key_id: str = "primary"  # noqa: S105
    #: Retired keys, ``<key_id>:<base64 key>`` comma separated. They decrypt and
    #: never encrypt, so a rotation is a restart rather than a migration.
    pr_secret_encryption_keys_old: OptionalSecret = None

    #: Google OAuth client for the YouTube connector. Server-side credentials:
    #: they belong to the deployment, never to a channel row, and the secret is
    #: never sent to a browser.
    youtube_oauth_client_id: OptionalStr = None
    youtube_oauth_client_secret: OptionalSecret = None
    #: Must match a redirect URI registered on the Google OAuth client exactly.
    #: Defaults to ``{web_base_url}/api/pr/channels/connections/youtube/callback``
    #: when left unset - see :meth:`youtube_redirect_uri`.
    youtube_oauth_redirect_uri: OptionalStr = None
    #: How long an authorization attempt may sit unfinished. Short: this is the
    #: window in which a state token is worth anything at all.
    youtube_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    #: Meta app for the Facebook Page and Instagram connectors. Server-side
    #: credentials: they belong to the deployment, never to a channel row, and
    #: the secret is never sent to a browser.
    #:
    #: ``meta_app_id`` and ``meta_app_secret`` already existed for the
    #: milestone-era publisher stubs and are reused rather than duplicated - one
    #: Meta app, one pair of credentials.
    meta_oauth_redirect_uri: OptionalStr = None
    #: The **one** Graph version string in the deployment. MeoBot never calls an
    #: unversioned Graph endpoint, so a Meta release cannot silently change what
    #: MeoBot sees; and bumping this when a version is retired is a restart
    #: rather than a patch. See ``docs/pr/STEP_1F24C_META_CHANNEL_CONNECTOR.md``.
    meta_graph_api_version: str = "v23.0"
    #: How long a Meta connection may sit waiting for somebody to pick a Page or
    #: an Instagram account before the credential it is holding is dropped.
    #: Short: it holds a long-lived user token, which is exactly the thing not
    #: to leave lying around after a flow is abandoned.
    meta_account_selection_ttl_seconds: int = Field(default=1800, ge=300, le=86400)

    #: TikTok app for the TikTok channel connector. Server-side credentials:
    #: they belong to the deployment, never to a channel row, and the secret is
    #: never sent to a browser.
    #:
    #: ``tiktok_client_key`` and ``tiktok_client_secret`` already existed for the
    #: milestone-era metrics stubs and are reused rather than duplicated - one
    #: TikTok app, one pair of credentials. Note the names: TikTok calls the
    #: public half a *client key* rather than a client id, and a request sending
    #: ``client_id`` is refused with an error that names nothing.
    tiktok_oauth_redirect_uri: OptionalStr = None

    #: Master switch for scheduled channel syncing. Off leaves connections in
    #: place and stops the beat job; "Đồng bộ ngay" keeps working.
    pr_channel_sync_enabled: bool = True
    #: **M4B.** Whether beat generates work from active recurring templates.
    #: A kill switch rather than a feature flag: a deployment that has to stop
    #: the scheduler at 3am should not have to end every template one at a time
    #: and then remember which ones to restart. Templates keep their state and
    #: their cursors; when it is turned back on, catch-up resumes from where it
    #: stopped, bounded by ``MAX_CATCH_UP_DAYS``.
    pr_recurring_work_enabled: bool = True
    #: How often beat looks for templates with occurrences due. Five minutes:
    #: the sweep is one indexed query that usually returns nothing, and this
    #: interval is how long after 09:00 a routine job appears on a board.
    pr_recurring_sweep_interval_seconds: int = 300
    #: How often the sweeper looks for connections that are due. This is not the
    #: sync cadence - :attr:`pr_channel_sync_min_interval_seconds` is.
    pr_channel_sync_sweep_interval_seconds: int = Field(default=3600, ge=300, le=86400)
    #: The actual cadence: a connection is due when its last **successful** sync
    #: is older than this. Daily by default. Channel-level operational metrics
    #: do not change fast enough to justify spending YouTube quota hourly, and
    #: Analytics data for the current day is incomplete anyway.
    pr_channel_sync_min_interval_seconds: int = Field(default=86400, ge=3600, le=604800)
    #: How many connections one sweep may claim. Bounds a burst against a
    #: provider that has just come back after an outage.
    pr_channel_sync_batch_size: int = Field(default=20, ge=1, le=200)
    #: A sync claimed longer ago than this is assumed lost to a dead worker and
    #: is released. Comfortably longer than the task time limit.
    pr_channel_sync_stale_after_seconds: int = Field(default=900, ge=60, le=7200)
    #: Consecutive failures before a connection is rested for a full cadence
    #: rather than retried every sweep. Backoff, not abandonment.
    pr_channel_sync_max_consecutive_failures: int = Field(default=5, ge=1, le=50)

    # --- Web admin panel (Step 1E) ----------------------------------------
    #: Public origin the browser reaches the panel on, e.g.
    #: ``https://pr.example.com``. Used to build the magic link the bot sends,
    #: and as the default allowed CORS origin. **Empty disables login-link
    #: issuing** rather than emitting a link to nowhere: a link with the wrong
    #: host is a token handed to a stranger's server.
    web_base_url: str = ""
    #: How long a login link stays usable. Short: it is single-use, arrives
    #: instantly in a Telegram DM, and a link that works tomorrow is a link
    #: that works for whoever reads the chat tomorrow.
    web_login_token_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    #: How long a browser session lasts before the person asks the bot again.
    #: One working day, so nobody is signed in overnight on a shared machine.
    web_session_ttl_seconds: int = Field(default=43200, ge=300, le=604800)
    #: ``Secure`` on the session cookie. Must stay true anywhere the panel is
    #: reachable over a network; turn it off only for ``http://localhost``
    #: development, where browsers refuse ``Secure`` cookies outright.
    web_cookie_secure: bool = True
    #: Extra browser origins allowed to call the API with credentials, comma
    #: separated. Deliberately not ``*``: the session is a cookie, so a wildcard
    #: origin would be a CSRF gift, and browsers reject the combination anyway.
    #:
    #: **Empty means no CORS middleware is installed at all**, which is correct
    #: for the supported topology: Next.js proxies ``/api`` so the browser only
    #: ever sees one origin. Step 1E installed CORS whenever ``web_base_url`` was
    #: set, which added a credentialed cross-origin path that nothing needed.
    web_extra_allowed_origins: str = ""
    #: The password every account starts on (0045). Signing in with it works
    #: but the session must choose a new one before anything else. Never
    #: stored: ``users.password_hash IS NULL`` is what "still on the default"
    #: looks like. Env ``MEOBOT_WEB_DEFAULT_PASSWORD``.
    web_default_password: SecretStr = Field(
        default=SecretStr("Apm@2026"),
        validation_alias=AliasChoices(
            "MEOBOT_WEB_DEFAULT_PASSWORD", "WEB_DEFAULT_PASSWORD", "web_default_password"
        ),
    )
    #: Consecutive failed password logins before the account is locked, and
    #: for how long. Telegram-link login is never locked.
    web_login_max_failures: int = Field(default=5, ge=1, le=50)
    web_login_lockout_seconds: int = Field(default=900, ge=60, le=86400)

    # --- The internal HTTP surface (Step 1E.1) ----------------------------
    #: Mount the milestone-1 ``/api/v1/*`` routers.
    #:
    #: **Off by default, and that is the security fix of Step 1E.1.** Those 51
    #: routes were written before any authentication existed: 24 of them are
    #: writes attributed to a synthetic ``OWNER``, and 26 are reads with no actor
    #: at all. The worst is ``PATCH /api/v1/users/{id}/role`` - an unauthenticated
    #: caller could promote themselves to OWNER, and since PR capability
    #: administration is gated on ``user.role.manage``, everything in the PR
    #: module followed from there.
    #:
    #: They are development and operations tooling, not a product surface:
    #: nothing in the bot, the workers or ``scripts/nas.sh`` calls them. So the
    #: safe default is not to serve them, and turning them on is a deliberate act
    #: for a port nothing untrusted can reach.
    #:
    #: ``/health/*``, ``/`` and the Step 1E web surface are unaffected.
    api_internal_routers_enabled: bool = False

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """Normalise the DSN so SQLAlchemy always gets the asyncpg driver."""
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("web_default_password")
    @classmethod
    def _validate_default_password(cls, value: SecretStr) -> SecretStr:
        """An empty or trivially short default would make every account open."""
        if len(value.get_secret_value()) < 8:
            raise ValueError("MEOBOT_WEB_DEFAULT_PASSWORD must be at least 8 characters.")
        return value

    @field_validator("app_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - config error
            raise ValueError(f"Unknown timezone: {value!r}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError(f"Invalid LOG_LEVEL: {value!r}")
        return level

    @model_validator(mode="after")
    def _refuse_unsafe_production_web(self) -> Settings:
        """Refuse to build production settings that would serve the panel unsafely.

        Two combinations are rejected outright rather than warned about, because
        both silently downgrade authentication and neither has a legitimate use in
        production:

        * ``WEB_COOKIE_SECURE=false`` - the session cookie would travel in clear,
          and anybody on the path could copy it. The temptation to set this is
          real: a browser refuses a ``Secure`` cookie over ``http://``, so the
          fastest way to make a plain-HTTP deployment "work" is to turn it off.
          That is exactly the fix this refuses. Serve HTTPS instead;
        * a non-``https://`` ``WEB_BASE_URL`` - the login link is a credential, and
          emitting one that starts ``http://`` publishes it to the network.

        Raised as ``ValueError`` so Pydantic reports it as a settings error at
        construction, which is how every other validator here fails and means the
        process dies at startup rather than halfway through serving.

        ``development``, ``staging`` and ``test`` are unaffected: local work over
        ``http://localhost`` needs ``WEB_COOKIE_SECURE=false`` and there is no
        network to sniff.
        """
        if self.app_env != "production":
            return self
        if not self.web_cookie_secure:
            raise ValueError(
                "APP_ENV=production requires WEB_COOKIE_SECURE=true. A session "
                "cookie without Secure travels in clear text; serve the panel "
                "over HTTPS instead of turning the flag off."
            )
        base = self.web_base_url.strip()
        if base and not base.startswith("https://"):
            raise ValueError(
                "APP_ENV=production requires WEB_BASE_URL to be https://. A login "
                f"link is a credential and {base!r} would send it unencrypted."
            )
        return self

    # --- Derived helpers --------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def timezone(self) -> ZoneInfo:
        """Display timezone. The database always stores UTC."""
        return ZoneInfo(self.app_timezone)

    @property
    def reminder_timezone(self) -> ZoneInfo:
        """The zone reminder wall clocks are interpreted in.

        Separate from :attr:`timezone` so a deployment can move its display
        timezone without silently rescheduling every existing reminder - though
        in practice it is the same zone, and empty means exactly that.
        """
        configured = self.reminder_default_timezone.strip()
        if not configured:
            return self.timezone
        try:
            return ZoneInfo(configured)
        except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - config error
            return self.timezone

    @property
    def telegram_enabled(self) -> bool:
        """The bot container refuses to start without a token; others do not care."""
        return self.telegram_bot_token is not None

    @property
    def google_enabled(self) -> bool:
        """True when a Google service-account file is configured."""
        path = self.google_service_account_file
        return bool(path and path.strip())

    @property
    def drive_enabled(self) -> bool:
        """True when Drive operations can be attempted.

        Drive shares the Sheets credential; there is no separate Drive secret.
        Template and root-folder ids are optional refinements, not requirements.
        """
        return self.google_enabled

    @property
    def youtube_redirect_uri(self) -> str:
        """Where Google sends the browser back after consent.

        Derived from :attr:`web_base_url` unless overridden, because the two
        must agree and a deployment that set one and forgot the other would get
        a ``redirect_uri_mismatch`` from Google with nothing local to look at.

        Whatever this resolves to has to be registered on the Google OAuth
        client **exactly**. It is never taken from a request.
        """
        configured = (self.youtube_oauth_redirect_uri or "").strip()
        if configured:
            return configured
        base = self.web_base_url.strip().rstrip("/")
        if not base:
            return ""
        return f"{base}/api/pr/channels/connections/youtube/callback"

    @property
    def meta_redirect_uri(self) -> str:
        """Where Meta sends the browser back after consent.

        Derived from :attr:`web_base_url` unless overridden, for the reason the
        YouTube one is: the two must agree, and a deployment that set one and
        forgot the other gets a redirect-mismatch from Meta with nothing local
        to look at. Whatever this resolves to must be registered on the Meta app
        as a Valid OAuth Redirect URI **exactly**. It is never taken from a
        request.
        """
        configured = (self.meta_oauth_redirect_uri or "").strip()
        if configured:
            return configured
        base = self.web_base_url.strip().rstrip("/")
        if not base:
            return ""
        return f"{base}/api/pr/channels/connections/meta/callback"

    @property
    def tiktok_redirect_uri(self) -> str:
        """Where TikTok sends the browser back after consent.

        Derived from :attr:`web_base_url` unless overridden, for the reason the
        YouTube and Meta ones are: the two must agree, and a deployment that set
        one and forgot the other gets a redirect-mismatch from TikTok with
        nothing local to look at.

        Whatever this resolves to must be registered on the TikTok app as a
        **Redirect URI** exactly, and TikTok requires it to be ``https`` - it
        rejects a plain-HTTP redirect outright, which the Google and Meta
        consoles tolerate for localhost and TikTok does not. It is never taken
        from a request.
        """
        configured = (self.tiktok_oauth_redirect_uri or "").strip()
        if configured:
            return configured
        base = self.web_base_url.strip().rstrip("/")
        if not base:
            return ""
        return f"{base}/api/pr/channels/connections/tiktok/callback"

    @property
    def tiktok_connector_enabled(self) -> bool:
        """True when a TikTok connection could actually be established.

        The same four things the other connectors need - an app, a secret, a
        redirect URI and somewhere to keep the credential - checked together,
        because a connector missing any one of them fails at a different and
        much more confusing moment. The encryption key in particular fails only
        *after* somebody has already consented in TikTok's UI.
        """
        return bool(
            self.tiktok_client_key
            and self.tiktok_client_secret is not None
            and self.tiktok_redirect_uri
            and (self.pr_secret_encryption_key is not None or self.pr_secret_encryption_key_file)
        )

    @property
    def meta_connector_enabled(self) -> bool:
        """True when a Meta connection could actually be established.

        The same four things the YouTube connector needs - an app, a secret, a
        redirect URI and somewhere to keep the credential - checked together,
        because a connector missing any one of them fails at a different and
        much more confusing moment. The encryption key in particular fails only
        *after* somebody has already consented in Meta's UI.
        """
        return bool(
            self.meta_app_id
            and self.meta_app_secret is not None
            and self.meta_redirect_uri
            and (self.pr_secret_encryption_key is not None or self.pr_secret_encryption_key_file)
        )

    @property
    def youtube_connector_enabled(self) -> bool:
        """True when a YouTube connection could actually be established.

        Three things and all of them deployment-side: an OAuth client, a
        redirect URI, and somewhere to keep the refresh token. Checked together
        because a connector missing any one of them fails at a different and
        much more confusing moment - after the operator has already consented in
        Google's UI, in the case of the key.
        """
        return bool(
            self.youtube_oauth_client_id
            and self.youtube_oauth_client_secret is not None
            and self.youtube_redirect_uri
            and (self.pr_secret_encryption_key is not None or self.pr_secret_encryption_key_file)
        )

    @property
    def llm_is_fake(self) -> bool:
        """True when the deterministic offline provider is in use."""
        return self.llm_provider.strip().lower() == "fake"

    @property
    def llm_base_url_host(self) -> str | None:
        """Host of ``LLM_BASE_URL`` - never the path, query string or userinfo.

        ``/chat_status`` shows an operator *where* MeoBot is calling without
        showing anything that could carry a credential. A gateway URL with a
        key in its query string is a real pattern, so only the host is exposed.
        """
        from urllib.parse import urlsplit

        raw = (self.llm_base_url or "").strip()
        if not raw:
            return None
        try:
            return urlsplit(raw).hostname
        except ValueError:  # pragma: no cover - defensive
            return None

    @property
    def llm_key_configured(self) -> bool:
        """True when an API key is set. Never reveals the key itself."""
        return self.llm_api_key is not None

    @property
    def callback_secret(self) -> str:
        """Key used to sign Telegram inline-button payloads.

        Derived from the bot token so it is stable across restarts and differs
        per deployment, without adding another secret to configure. Falls back
        to the database DSN (also deployment-specific) when no token is set -
        the bot cannot run without a token anyway, so that path only matters to
        the API and the worker, which merely *render* buttons.
        """
        if self.telegram_bot_token is not None:
            return f"meobot-callback:{self.telegram_bot_token.get_secret_value()}"
        return f"meobot-callback:{self.database_url}"

    def safe_summary(self) -> dict[str, str | bool | None]:
        """Secret-free snapshot suitable for logs and ``/api/v1/system/info``."""
        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "app_timezone": self.app_timezone,
            "log_level": self.log_level,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "telegram_configured": self.telegram_enabled,
            "google_configured": self.google_enabled,
            "chat_enabled": self.chat_enabled,
            "drive_root_configured": bool(self.google_drive_root_folder_id),
            "shared_drive_configured": bool(self.google_shared_drive_id),
            "owner_configured": self.meobot_owner_telegram_id is not None,
            "database_host": (
                self.database_url.rsplit("@", 1)[-1] if "@" in self.database_url else None
            ),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached, immutable)."""
    return Settings()
