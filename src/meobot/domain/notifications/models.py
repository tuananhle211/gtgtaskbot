"""Vocabulary for sending something to somebody who is not in this chat.

The whole release turns on one distinction: **who is allowed to see what**. A
leave request and a leave *date* are the same event and belong in different
places - the reason goes privately to the person deciding, the date may go to
an attendance group, and neither may go anywhere else.

:class:`PrivacyClassification` is how that is enforced. Every outbound message
carries one, every destination has a ceiling, and
:mod:`~meobot.domain.notifications.routing` refuses the pair before an outbox
row exists. It is a check in application code, not a sentence in a prompt,
because a prompt cannot be tested and this can.
"""

from __future__ import annotations

from enum import StrEnum


class ChatPurpose(StrEnum):
    """What a registered Telegram group is *for*.

    Purpose rather than name, because a name is a label somebody types and a
    purpose is what routing keys off. "Gửi vào group Content" resolves through
    the alias; "thông báo cho toàn phòng" resolves through the purpose.
    """

    DEPARTMENT_ANNOUNCEMENTS = "DEPARTMENT_ANNOUNCEMENTS"
    CONTENT_TEAM = "CONTENT_TEAM"
    PRODUCTION_TEAM = "PRODUCTION_TEAM"
    SEEDING_TEAM = "SEEDING_TEAM"
    REPORTING = "REPORTING"
    ATTENDANCE = "ATTENDANCE"
    MANAGEMENT = "MANAGEMENT"
    GENERAL = "GENERAL"


PURPOSE_LABELS: dict[ChatPurpose, str] = {
    ChatPurpose.DEPARTMENT_ANNOUNCEMENTS: "Group thông báo toàn phòng",
    ChatPurpose.CONTENT_TEAM: "Team Nội dung",
    ChatPurpose.PRODUCTION_TEAM: "Team Sản xuất",
    ChatPurpose.SEEDING_TEAM: "Team Seeding",
    ChatPurpose.REPORTING: "Group Báo cáo",
    ChatPurpose.ATTENDANCE: "Group Chấm công",
    ChatPurpose.MANAGEMENT: "Group Quản lý",
    ChatPurpose.GENERAL: "Group chung",
}

#: What a group is *for*, as a sentence rather than a name. Shown on the
#: registration preview, where "Tên sử dụng" and "Mục đích" are two different
#: questions and answering both with "Group chung" told the owner nothing.
PURPOSE_DESCRIPTIONS: dict[ChatPurpose, str] = {
    ChatPurpose.DEPARTMENT_ANNOUNCEMENTS: "Thông báo cho toàn phòng",
    ChatPurpose.CONTENT_TEAM: "Trao đổi của team Nội dung",
    ChatPurpose.PRODUCTION_TEAM: "Trao đổi của team Sản xuất",
    ChatPurpose.SEEDING_TEAM: "Trao đổi của team Seeding",
    ChatPurpose.REPORTING: "Nhận báo cáo",
    ChatPurpose.ATTENDANCE: "Theo dõi chấm công",
    ChatPurpose.MANAGEMENT: "Trao đổi quản lý",
    ChatPurpose.GENERAL: "Nhận thông báo nội bộ",
}

#: Team purposes a Trưởng nhóm may be allowed to address. Department-wide
#: announcements are deliberately absent: a team lead speaks for their team.
TEAM_PURPOSES: frozenset[ChatPurpose] = frozenset(
    {ChatPurpose.CONTENT_TEAM, ChatPurpose.PRODUCTION_TEAM, ChatPurpose.SEEDING_TEAM}
)


class PrivacyClassification(StrEnum):
    """How far a piece of information is allowed to travel.

    Ordered from most to least shareable. The numeric :attr:`rank` is what the
    routing rules compare, so adding a level later does not mean rewriting
    every comparison.
    """

    PUBLIC_OPERATIONAL = "PUBLIC_OPERATIONAL"
    TEAM_OPERATIONAL = "TEAM_OPERATIONAL"
    MANAGEMENT_ONLY = "MANAGEMENT_ONLY"
    PERSONAL_PRIVATE = "PERSONAL_PRIVATE"
    SECRET = "SECRET"  # noqa: S105 - a privacy level, not a credential

    @property
    def rank(self) -> int:
        return _PRIVACY_RANK[self]

    @property
    def may_reach_a_group(self) -> bool:
        """Only the two operational levels may ever be posted in a group.

        ``MANAGEMENT_ONLY`` is excluded on purpose: a management group is still
        a group, and "detailed HR request" is a private conversation between
        two people even when both are managers.
        """
        return self in {
            PrivacyClassification.PUBLIC_OPERATIONAL,
            PrivacyClassification.TEAM_OPERATIONAL,
        }


_PRIVACY_RANK: dict[PrivacyClassification, int] = {
    PrivacyClassification.PUBLIC_OPERATIONAL: 10,
    PrivacyClassification.TEAM_OPERATIONAL: 20,
    PrivacyClassification.MANAGEMENT_ONLY: 30,
    PrivacyClassification.PERSONAL_PRIVATE: 40,
    PrivacyClassification.SECRET: 50,
}


class RecipientType(StrEnum):
    """Where an outbound message is going."""

    #: One person's private chat with the bot.
    USER_PRIVATE = "USER_PRIVATE"
    #: A registered Telegram group.
    REGISTERED_CHAT = "REGISTERED_CHAT"


class OutboxStatus(StrEnum):
    """Lifecycle of one durable outbound message."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DELIVERED = "DELIVERED"
    RETRY_WAIT = "RETRY_WAIT"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    CANCELLED = "CANCELLED"

    @property
    def is_settled(self) -> bool:
        """True once no worker will pick this up again."""
        return self in {
            OutboxStatus.DELIVERED,
            OutboxStatus.PERMANENT_FAILURE,
            OutboxStatus.CANCELLED,
        }

    @property
    def is_claimable(self) -> bool:
        """True when a worker may take it."""
        return self in {OutboxStatus.PENDING, OutboxStatus.RETRY_WAIT}


STATUS_LABELS: dict[OutboxStatus, str] = {
    OutboxStatus.PENDING: "Đang chờ gửi",
    OutboxStatus.PROCESSING: "Đang gửi",
    OutboxStatus.DELIVERED: "Đã gửi",
    OutboxStatus.RETRY_WAIT: "Sẽ thử gửi lại",
    OutboxStatus.PERMANENT_FAILURE: "Không thể gửi",
    OutboxStatus.CANCELLED: "Đã huỷ",
}


class DeliveryOutcome(StrEnum):
    """What one attempt achieved."""

    DELIVERED = "DELIVERED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    RECIPIENT_UNAVAILABLE = "RECIPIENT_UNAVAILABLE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


class FailureCategory(StrEnum):
    """Why an attempt did not land, in terms the application can act on.

    Deliberately coarse. The provider's own error text is logged and never
    stored here: it can echo message content, and nothing downstream needs more
    detail than "retry", "this recipient cannot be reached" or "stop".
    """

    NONE = "NONE"
    NETWORK = "NETWORK"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    #: The person has never opened a chat with the bot, or blocked it.
    PRIVATE_CHAT_UNAVAILABLE = "PRIVATE_CHAT_UNAVAILABLE"
    #: The bot is not in the group any more, or was never added.
    BOT_NOT_IN_CHAT = "BOT_NOT_IN_CHAT"
    #: The bot is present but may not post.
    BOT_CANNOT_SEND = "BOT_CANNOT_SEND"
    CHAT_NOT_FOUND = "CHAT_NOT_FOUND"
    #: A configuration problem: unregistered, disabled, or a privacy violation.
    DESTINATION_REFUSED = "DESTINATION_REFUSED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_retryable(self) -> bool:
        """True for failures that a later attempt could plausibly survive."""
        return self in {
            FailureCategory.NETWORK,
            FailureCategory.RATE_LIMITED,
            FailureCategory.PROVIDER_UNAVAILABLE,
            FailureCategory.UNKNOWN,
        }

    @property
    def is_recipient_problem(self) -> bool:
        """True when retrying cannot help until a human does something."""
        return self in {
            FailureCategory.PRIVATE_CHAT_UNAVAILABLE,
            FailureCategory.BOT_NOT_IN_CHAT,
            FailureCategory.BOT_CANNOT_SEND,
            FailureCategory.CHAT_NOT_FOUND,
        }


class NotificationEvent(StrEnum):
    """The business events this release can route.

    A closed set on purpose. A new kind of cross-chat message is a deliberate
    addition here plus a template, not something a caller can invent with a
    free-form string.
    """

    ANNOUNCEMENT_PUBLISHED = "announcement_published"
    HR_REQUEST_SUBMITTED = "hr_request_submitted"
    HR_REQUEST_APPROVED = "hr_request_approved"
    HR_REQUEST_REJECTED = "hr_request_rejected"
    HR_MORE_INFO_REQUESTED = "hr_more_info_requested"
    HR_ATTENDANCE_UPDATE = "hr_attendance_update"
    ANNOUNCEMENT_REMINDER = "announcement_reminder"
    # --- 0.6.0a2 ----------------------------------------------------------
    #: A stranger asking to use MeoBot, sent to the owner. Was a direct send.
    ACCESS_REQUEST_SUBMITTED = "access_request_submitted"
    #: The owner's answer to that request, sent back to the requester.
    ACCESS_REQUEST_DECIDED = "access_request_decided"
    #: A member asking for more chat allowance. Was a direct send.
    QUOTA_REQUEST_SUBMITTED = "quota_request_submitted"
    #: The owner's answer to a quota request.
    QUOTA_REQUEST_DECIDED = "quota_request_decided"
    #: A Guest's held-back first question, answered after approval.
    GUEST_REPLY = "guest_reply"
    #: One firing of a user-defined reminder.
    REMINDER_DUE = "reminder_due"
    #: "This message could not be delivered", to whoever asked for it.
    DELIVERY_FAILED_ALERT = "delivery_failed_alert"
    #: A destination that had gone unreachable and now works again.
    DESTINATION_RECOVERED = "destination_recovered"
    #: A registered destination that stopped accepting messages.
    DESTINATION_UNHEALTHY = "destination_unhealthy"
    # --- 0.6.0a2.1 --------------------------------------------------------
    # --- PR workflow, Step 1F.2.3b ----------------------------------------
    #: A Head approval landed and the piece now needs somebody to produce it.
    #: Sent to whoever is responsible for the content - the same relation
    #: ``MY_CONTENT`` uses - because "đã duyệt" with nobody told is how a piece
    #: sits at ``APPROVED`` for a week.
    PR_CONTENT_APPROVED = "pr_content_approved"
    #: Somebody was given a production to do. Sent to them, not to the manager
    #: who decided it: the person who acted already knows.
    PR_PRODUCTION_ASSIGNED = "pr_production_assigned"
    #: A workflow decision was taken back. The correction half of the two above -
    #: whoever was told to go and act must be told not to.
    PR_WORKFLOW_UNDONE = "pr_workflow_undone"
    # --- Ads order engine --------------------------------------------------
    #: One event per hand-off in the order pipeline. Each goes to the person
    #: who has to act next, never to the person who acted. The inbox row is
    #: always written; Telegram only when the unit opted in.
    ORDER_SUBMITTED = "order_submitted"
    ORDER_APPROVED = "order_approved"
    ORDER_RETURNED = "order_returned"
    ORDER_NODE_TURN = "order_node_turn"
    ORDER_NODE_ASSIGNED = "order_node_assigned"
    ORDER_NODE_ACCEPTED = "order_node_accepted"
    ORDER_SUBMISSION_READY = "order_submission_ready"
    ORDER_NODE_RETURNED = "order_node_returned"
    ORDER_FINAL_RETURNED = "order_final_returned"
    ORDER_COMPLETED = "order_completed"
    # --- Account (0046) -------------------------------------------------------
    #: A temporary web password, sent to its owner's private chat only.
    ACCOUNT_TEMPORARY_PASSWORD = "account_temporary_password"  # noqa: S105
    # --- PR workflow, Step 1F.2.3d ----------------------------------------
    #: A Team Lead approved and the piece moved on to ``HEAD_REVIEW``. Sent to
    #: the responsible person as **status**, not as a task - they have nothing to
    #: do, and knowing their script cleared the first gate is the difference
    #: between waiting and wondering.
    #:
    #: Deliberately **not** sent to the Heads. Everybody holding
    #: ``PR_HEAD_REVIEW`` would be a broadcast to a queue that already exists:
    #: ``MY_ACTIONS`` shows every item standing at a gate they hold, which is a
    #: better review queue than a notification per item per reviewer.
    PR_CONTENT_TEAM_LEAD_APPROVED = "pr_content_team_lead_approved"
    #: An internal review sent the cut back. To the producer, who is the only
    #: person who can act on it, and the one message in this family that is
    #: genuinely urgent - the work has stopped until they pick it up.
    PR_PRODUCTION_REVISION_REQUIRED = "pr_production_revision_required"
    #: An internal review passed. To the producer, whose work was accepted, and
    #: to the responsible person, whose piece is now ready for what comes next -
    #: two different pieces of news, so two messages rather than one addressed
    #: vaguely. Skipped for whichever of them pressed the button.
    PR_INTERNAL_REVIEW_APPROVED = "pr_internal_review_approved"
    # --- The Work Ledger, M1 ----------------------------------------------
    #: Somebody was put on a job. To them, not to the manager who decided it -
    #: the person who acted already knows.
    PR_WORK_ASSIGNED = "pr_work_assigned"
    #: A proposal was taken on, or was not. To the person who proposed it, who
    #: is otherwise left watching a row that never moves.
    PR_WORK_PROPOSAL_DECIDED = "pr_work_proposal_decided"
    #: A contributor says a job is finished. To whoever assigned it, because
    #: **they cannot validate it themselves if they worked on it** and somebody
    #: has to know it is waiting.
    PR_WORK_AWAITING_VALIDATION = "pr_work_awaiting_validation"
    #: Validated. To each contributor, because this is the moment their work
    #: became counted work.
    PR_WORK_APPROVED = "pr_work_approved"
    # --- KPI self-service ---------------------------------------------------
    #: An employee submitted their KPI proposal. To the people who may approve
    #: one - the plan configurers - so a proposal is not discovered by scrolling.
    PR_KPI_PLAN_SUBMITTED = "pr_kpi_plan_submitted"
    #: The proposal was approved and is in force. To its subject.
    PR_KPI_PLAN_APPROVED = "pr_kpi_plan_approved"
    #: The proposal was sent back with a note. To its subject.
    PR_KPI_PLAN_RETURNED = "pr_kpi_plan_returned"
    #: "Đã gửi", told to the sender once Telegram accepted the announcement.
    #: The source chat only ever gets "đã được xếp hàng gửi"; this is what
    #: makes the difference between the two visible rather than semantic.
    ANNOUNCEMENT_DELIVERED = "announcement_delivered"
    # --- 0.6.0a3 ----------------------------------------------------------
    #: One part of one multi-group announcement, aimed at one group. A short
    #: announcement to three groups is three of these; a long one to three
    #: groups is three times its part count, and every one settles on its own.
    DISPATCH_PART_PUBLISHED = "dispatch_part_published"
    #: "2/3 đã gửi", told to the sender once every destination has settled.
    DISPATCH_SUMMARY = "dispatch_summary"


class DestinationHealth(StrEnum):
    """What a proactive probe learned about a registered destination.

    Learned from ``getChat``/``getChatMember``, which read state rather than
    producing a message. Probing by *sending* something would put a test
    message in a real group full of real colleagues, so it is never done.
    """

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    CANNOT_SEND = "CANNOT_SEND"
    BOT_REMOVED = "BOT_REMOVED"
    NOT_FOUND = "NOT_FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"

    @property
    def is_healthy(self) -> bool:
        return self is DestinationHealth.HEALTHY

    @property
    def is_definite(self) -> bool:
        """True when the probe learned something about the *destination*.

        ``PROVIDER_ERROR`` is excluded: Telegram being briefly unreachable says
        nothing about whether a group still exists, and treating it as evidence
        would disable healthy destinations during any outage.
        """
        return self in {
            DestinationHealth.HEALTHY,
            DestinationHealth.CANNOT_SEND,
            DestinationHealth.BOT_REMOVED,
            DestinationHealth.NOT_FOUND,
        }


HEALTH_LABELS: dict[DestinationHealth, str] = {
    DestinationHealth.UNKNOWN: "Chưa kiểm tra",
    DestinationHealth.HEALTHY: "Hoạt động bình thường",
    DestinationHealth.CANNOT_SEND: "Không có quyền gửi",
    DestinationHealth.BOT_REMOVED: "TasksBot đã bị xoá khỏi group",
    DestinationHealth.NOT_FOUND: "Không còn tìm thấy group",
    DestinationHealth.PROVIDER_ERROR: "Telegram tạm thời không phản hồi",
}


class AssignmentRole(StrEnum):
    """What one person is to one registered group."""

    #: May broadcast into it and see who has read what.
    MANAGER = "MANAGER"
    #: Counted in the expected audience for a team announcement.
    MEMBER = "MEMBER"


ASSIGNMENT_ROLE_LABELS: dict[AssignmentRole, str] = {
    AssignmentRole.MANAGER: "Trưởng nhóm quản lý",
    AssignmentRole.MEMBER: "Thành viên của group",
}


def health_label(health: DestinationHealth) -> str:
    """Vietnamese name of a destination's health. A raw value never reaches a user."""
    return HEALTH_LABELS[health]


def assignment_role_label(role: AssignmentRole) -> str:
    """Vietnamese name of a group assignment role."""
    return ASSIGNMENT_ROLE_LABELS[role]


class AnnouncementStatus(StrEnum):
    """Lifecycle of an owner-authored announcement."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


def purpose_label(purpose: ChatPurpose) -> str:
    """Vietnamese name of a group purpose."""
    return PURPOSE_LABELS[purpose]


def purpose_description(purpose: ChatPurpose) -> str:
    """What a group registered for this purpose will be used for."""
    return PURPOSE_DESCRIPTIONS[purpose]


def status_label(status: OutboxStatus) -> str:
    """Vietnamese name of a delivery status. A raw value never reaches a user."""
    return STATUS_LABELS[status]
