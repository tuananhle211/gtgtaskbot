"""ORM models.

Importing this package registers every table on ``Base.metadata`` - Alembic's
``env.py`` relies on that for autogenerate.
"""

from meobot.db.models.access import GroupMemberResponsePolicy, PendingGuestAccessRequest
from meobot.db.models.actor_profile import ActorProfileRow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.confirmation_request import ConfirmationRequestRow
from meobot.db.models.conversation import (
    ConversationMessage,
    ConversationSummary,
    ConversationThread,
)
from meobot.db.models.conversation_state import ConversationState
from meobot.db.models.deferred import DeferredGuestMessage
from meobot.db.models.dispatch import (
    MessageDispatch,
    MessageDispatchDraft,
    MessageDispatchDraftRecipient,
    MessageDispatchPart,
    MessageDispatchRecipient,
    MessageDispatchRecipientPart,
)
from meobot.db.models.drive import CreatedSpreadsheet, DriveFolder, SheetTemplate
from meobot.db.models.hr import (
    HrRequest,
    HrRequestEvent,
    MemberListContext,
    OrganizationHoliday,
    WorkSchedule,
)
from meobot.db.models.invite import InviteCode
from meobot.db.models.notifications import (
    Announcement,
    AnnouncementAcknowledgement,
    AnnouncementRecipient,
    DeliveryAttempt,
    OutboundMessage,
    TelegramChat,
    TelegramChatAssignment,
)
from meobot.db.models.observed_user import ObservedTelegramUser
from meobot.db.models.order import (
    Order,
    OrderApproval,
    OrderCodeCounter,
    OrderEvent,
    OrderNode,
    OrderSubmission,
    OrderWorkRule,
)
from meobot.db.models.org_unit import (
    OrgUnit,
    OrgUnitMember,
    UnitDuration,
    UnitPlatform,
    UnitVideoKind,
)
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrContentFormat,
    PrContentItem,
    PrContentPillar,
    PrContentTarget,
    PrPlatform,
    PrTask,
    PrTaskAssignment,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_authorization import (
    PrUserCapability,
    PrUserCapabilityChannel,
    PrUserCapabilityContentType,
)
from meobot.db.models.pr_channel_connection import (
    PrChannelConnection,
    PrChannelOAuthState,
)
from meobot.db.models.pr_code_counter import PrCodeCounter
from meobot.db.models.pr_content_asset import PrContentDerivative, PrContentDestination
from meobot.db.models.pr_content_comment import PrContentComment
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_content_work import (
    PrContentWorkProjection,
    PrContentWorkRule,
)
from meobot.db.models.pr_performance import (
    PrPerformancePolicy,
    PrPerformanceResult,
    PrPerformanceReview,
    PrPerformanceTargetOverride,
    PrWorkScoreAllocation,
    PrWorkScoringRule,
)
from meobot.db.models.pr_platform_policy import (
    PrAiReviewRunPolicyPack,
    PrPlatformPolicyPack,
    PrPlatformPolicyRule,
    PrPlatformPolicySnapshot,
    PrPlatformPolicySource,
)
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import (
    PrAction,
    PrChannelMetricSnapshot,
    PrIssue,
    PrPostMetricSnapshot,
    PrPublication,
    PrReportArtifact,
    PrReportingPeriod,
    PrReportRun,
    PrWeeklyManualInput,
)
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.pr_work_quota import (
    PrWorkPlan,
    PrWorkQuota,
    PrWorkQuotaAllocation,
)
from meobot.db.models.pr_work_recurring import (
    PrWorkRecurringOccurrence,
    PrWorkRecurringTemplate,
    PrWorkRecurringTemplateContributor,
)
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.quota import DailyAiUsage, QuotaRequest, UserQuotaOverride
from meobot.db.models.reminder import Reminder, ReminderOccurrence
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.script_approval import ScriptApproval
from meobot.db.models.script_review import ScriptReview
from meobot.db.models.script_type import ScriptType, ScriptTypeVersion
from meobot.db.models.sheet_profile import SheetProfile
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.task import Task
from meobot.db.models.user import User
from meobot.db.models.user_avatar import UserAvatar
from meobot.db.models.user_notification import UserNotification
from meobot.db.models.web_session import WebSession, WebSessionKind

__all__ = [
    "ActorProfileRow",
    "Announcement",
    "AnnouncementAcknowledgement",
    "AnnouncementRecipient",
    "AuditLog",
    "ConfirmationRequestRow",
    "ConversationMessage",
    "ConversationState",
    "ConversationSummary",
    "ConversationThread",
    "CreatedSpreadsheet",
    "DailyAiUsage",
    "DeferredGuestMessage",
    "DeliveryAttempt",
    "DriveFolder",
    "GroupMemberResponsePolicy",
    "HrRequest",
    "HrRequestEvent",
    "InviteCode",
    "MemberListContext",
    "MessageDispatch",
    "MessageDispatchDraft",
    "MessageDispatchDraftRecipient",
    "MessageDispatchPart",
    "MessageDispatchRecipient",
    "MessageDispatchRecipientPart",
    "ObservedTelegramUser",
    "Order",
    "OrderApproval",
    "OrderCodeCounter",
    "OrderEvent",
    "OrderNode",
    "OrderSubmission",
    "OrderWorkRule",
    "OrgUnit",
    "OrgUnitMember",
    "OrganizationHoliday",
    "OutboundMessage",
    "PendingGuestAccessRequest",
    "PrAction",
    "PrAiReview",
    "PrAiReviewRun",
    "PrAiReviewRunPolicyPack",
    "PrApprovalEvent",
    "PrBrand",
    "PrChannel",
    "PrChannelAssignment",
    "PrChannelConnection",
    "PrChannelMetricSnapshot",
    "PrChannelOAuthState",
    "PrCodeCounter",
    "PrContentComment",
    "PrContentDerivative",
    "PrContentDestination",
    "PrContentFormat",
    "PrContentItem",
    "PrContentPillar",
    "PrContentResource",
    "PrContentTarget",
    "PrContentTransitionEvent",
    "PrContentVersion",
    "PrContentWorkProjection",
    "PrContentWorkRule",
    "PrIssue",
    "PrPerformancePolicy",
    "PrPerformanceResult",
    "PrPerformanceReview",
    "PrPerformanceTargetOverride",
    "PrPlatform",
    "PrPlatformPolicyPack",
    "PrPlatformPolicyRule",
    "PrPlatformPolicySnapshot",
    "PrPlatformPolicySource",
    "PrPostMetricSnapshot",
    "PrProductionSubmission",
    "PrPublication",
    "PrReportArtifact",
    "PrReportRun",
    "PrReportingPeriod",
    "PrTask",
    "PrTaskAssignment",
    "PrUserCapability",
    "PrUserCapabilityChannel",
    "PrUserCapabilityContentType",
    "PrWeeklyManualInput",
    "PrWorkContribution",
    "PrWorkEvidence",
    "PrWorkHistory",
    "PrWorkItem",
    "PrWorkPlan",
    "PrWorkQuota",
    "PrWorkQuotaAllocation",
    "PrWorkRecurringOccurrence",
    "PrWorkRecurringTemplate",
    "PrWorkRecurringTemplateContributor",
    "PrWorkResult",
    "PrWorkScoreAllocation",
    "PrWorkScoringRule",
    "PrWorkType",
    "QuotaRequest",
    "Reminder",
    "ReminderOccurrence",
    "Script",
    "ScriptApproval",
    "ScriptReview",
    "ScriptType",
    "ScriptTypeVersion",
    "ScriptVersion",
    "SheetProfile",
    "SheetTemplate",
    "SystemSetting",
    "Task",
    "TelegramChat",
    "TelegramChatAssignment",
    "UnitDuration",
    "UnitPlatform",
    "UnitVideoKind",
    "User",
    "UserAvatar",
    "UserNotification",
    "UserQuotaOverride",
    "WebSession",
    "WebSessionKind",
    "WorkSchedule",
]

# The ``tasks`` projection is written from a flush hook, so that every write
# path - web, Telegram, Celery - keeps it in step without knowing it exists.
# Installed here because every process that writes a source row has imported
# this package by then. Imported last: the hook module imports models.
from meobot.application.tasks.sync import install_task_sync

install_task_sync()
