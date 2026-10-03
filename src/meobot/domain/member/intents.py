"""What a Member's message means, decided without a model.

**Why patterns and not the LLM.** Three reasons, and each one on its own would
be enough:

* **Quota.** The boundary between "operational action" and "generative
  conversation" has to be decided *before* a chat slot is reserved. Asking the
  model whether its own call should be billed is circular.
* **Availability.** Accepting a task, reporting progress and filing a leave
  request must keep working when the provider is down. A pattern match cannot
  have an outage.
* **Determinism.** The same sentence must route the same way every time, and be
  explainable afterwards from the code rather than from a prompt.

The LLM still has a job - open-ended conversation, writing, analysis - it just
does not decide *whether* it is being asked for that.

**Matching is on the normalized form.** See
:mod:`~meobot.domain.member.normalization`: accent-free, lower case,
abbreviations expanded, URLs untouched. So "Hôm nay tôi có việc gì?" and "viec
hnay cua toi" reach the same intent.

**Order matters.** More specific intents are tested first, because "tôi làm
xong việc rồi" is a completion and not a request to view work, and "viết giúp
tôi 5 bình luận" is generative even though it contains "bình luận".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from meobot.domain.member.normalization import NormalizedText, normalize


class MemberIntent(StrEnum):
    """One recognised thing a Member can be asking for.

    ``GENERATIVE`` is the deliberate catch-all: anything not matched by a
    specific operational pattern is treated as ordinary conversation, which is
    the only branch that costs a chat slot.
    """

    # --- Navigation -------------------------------------------------------
    HOME = "home"
    HELP = "help"
    CANCEL = "cancel"

    # --- Work (module not built yet; intents exist so the answer is honest)
    VIEW_WORK = "view_work"
    ACCEPT_WORK = "accept_work"
    UPDATE_PROGRESS = "update_progress"
    COMPLETE_WORK = "complete_work"
    SUBMIT_PROOF = "submit_proof"
    REPORT_ISSUE = "report_issue"
    VIEW_CHANNELS = "view_channels"
    VIEW_SCHEDULE_GAPS = "view_schedule_gaps"
    VIEW_PERSONAL_REPORT = "view_personal_report"

    # --- AI allowance -----------------------------------------------------
    VIEW_AI_ALLOWANCE = "view_ai_allowance"

    # --- HR ---------------------------------------------------------------
    REQUEST_LEAVE = "request_leave"
    REQUEST_LATE = "request_late"
    VIEW_MY_HR_REQUESTS = "view_my_hr_requests"
    CANCEL_HR_REQUEST = "cancel_hr_request"
    VIEW_MY_HR_STATS = "view_my_hr_stats"

    # --- HR, management side ---------------------------------------------
    VIEW_PENDING_HR = "view_pending_hr"
    VIEW_WHO_IS_OFF = "view_who_is_off"
    VIEW_HR_REPORT = "view_hr_report"

    # --- Cross-chat notification (0.6.0a1) --------------------------------
    MAKE_ANNOUNCEMENT = "make_announcement"
    #: "Nhắn riêng cho Linh." Recognised so it cannot be answered by the model,
    #: which had no way to know MeoBot cannot yet address one person by name.
    SEND_PRIVATE_MESSAGE = "send_private_message"
    REGISTER_CHAT = "register_chat"
    VIEW_REGISTERED_CHATS = "view_registered_chats"
    VIEW_DELIVERY_STATUS = "view_delivery_status"
    RETRY_DELIVERY = "retry_delivery"
    WHO_HAS_NOT_READ = "who_has_not_read"
    REMIND_UNREAD = "remind_unread"

    # --- Reminders (0.6.0a2) ----------------------------------------------
    CREATE_REMINDER = "create_reminder"
    VIEW_REMINDERS = "view_reminders"
    PAUSE_REMINDER = "pause_reminder"
    RESUME_REMINDER = "resume_reminder"
    CANCEL_REMINDER = "cancel_reminder"
    EDIT_REMINDER = "edit_reminder"
    NEXT_REMINDER = "next_reminder"

    # --- Group management (0.6.0a2) ---------------------------------------
    ASSIGN_GROUP_MANAGER = "assign_group_manager"
    REMOVE_GROUP_MANAGER = "remove_group_manager"
    VIEW_GROUP_MANAGERS = "view_group_managers"

    # --- Multi-group dispatch (0.6.0a3) -----------------------------------
    #: "Gửi thông báo này vào các group content", "Gửi cho tất cả group",
    #: "Gửi cho group Test và group Saykeng". Recognised separately from
    #: MAKE_ANNOUNCEMENT because the destination is a *set*, and the single
    #: destination path had nowhere to put one.
    SEND_MULTI_ANNOUNCEMENT = "send_multi_announcement"
    #: "Cho chị chọn group", "MeoBot gửi được vào những nhóm nào?" - the
    #: registry, offered as something to pick from rather than only to read.
    CHOOSE_DESTINATIONS = "choose_destinations"

    # --- Everything else --------------------------------------------------
    GENERATIVE = "generative"


#: Intents that are operational: deterministic, free, and available even when
#: the provider is unreachable. Everything not in here costs a chat slot.
OPERATIONAL_INTENTS: frozenset[MemberIntent] = frozenset(
    intent for intent in MemberIntent if intent is not MemberIntent.GENERATIVE
)


@dataclass(frozen=True, slots=True)
class IntentRule:
    """One pattern that recognises an intent.

    Args:
        intent: What a match means.
        any_of: Phrases where any single one is enough.
        all_of: Phrases that must *all* be present.
        none_of: Phrases that disqualify the match - how "viết giúp tôi 5 bình
            luận" avoids being read as a progress update.
    """

    intent: MemberIntent
    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = field(default=())
    #: Require a digit somewhere in the message. This is what separates "tôi
    #: làm được 3/5" (a progress report) from "bạn làm được gì?" (a question
    #: about capabilities) - both contain "làm được", and only one has a number.
    requires_number: bool = False

    def matches(self, text: NormalizedText) -> bool:
        if self.none_of and text.contains_any(*self.none_of):
            return False
        if self.requires_number and not any(char.isdigit() for char in text.matchable):
            return False
        if self.all_of and not text.contains(*self.all_of):
            return False
        if self.any_of and not text.contains_any(*self.any_of):
            return False
        return bool(self.any_of or self.all_of)


#: Words that mean "please write / think / analyse for me". Their presence makes
#: a message generative even when it also mentions work vocabulary.
GENERATIVE_MARKERS: tuple[str, ...] = (
    "viet giup",
    "viet ho",
    "viet lai",
    "soan giup",
    "soan ho",
    "goi y",
    "y tuong",
    "brainstorm",
    "phan tich",
    "tu van",
    "dat lai",
    "sua noi dung",
    "sua lai cau",
    "dien dat lai",
    "tom tat giup",
)

#: Ordered: the first rule that matches wins.
RULES: tuple[IntentRule, ...] = (
    # --- Reminders (first: "nhắc" is a common word and these are the most
    #     specific readings of it) -----------------------------------------
    IntentRule(
        # Must precede CREATE_REMINDER: "nhắc những người chưa đọc" is a
        # broadcast to other people, not a reminder for the speaker.
        MemberIntent.REMIND_UNREAD,
        any_of=(
            "nhac nhung nguoi chua doc",
            "nhac nguoi chua doc",
            "nhac nhung ai chua doc",
            "nhac ai chua doc",
            "nhac nhung nguoi chua xac nhan",
        ),
    ),
    IntentRule(
        MemberIntent.WHO_HAS_NOT_READ,
        any_of=(
            "ai chua doc",
            "ai chua xac nhan",
            "nguoi chua xac nhan",
            "nguoi chua doc",
            "bao nhieu nguoi da doc",
            "may nguoi da doc",
        ),
    ),
    IntentRule(
        MemberIntent.NEXT_REMINDER,
        any_of=("lan nhac tiep theo", "nhac tiep theo la khi nao", "khi nao nhac"),
    ),
    IntentRule(
        MemberIntent.VIEW_REMINDERS,
        any_of=(
            "lich nhac cua toi",
            "cac lich nhac",
            "danh sach lich nhac",
            "nhung lich nhac nao",
            "co lich nhac nao",
            "xem lich nhac",
        ),
    ),
    IntentRule(
        MemberIntent.PAUSE_REMINDER,
        all_of=("lich nhac",),
        any_of=("tam dung", "tam ngung", "tam tat"),
    ),
    IntentRule(
        MemberIntent.RESUME_REMINDER,
        all_of=("lich nhac",),
        any_of=("bat lai", "mo lai", "tiep tuc"),
    ),
    IntentRule(
        MemberIntent.CANCEL_REMINDER,
        all_of=("lich nhac",),
        any_of=("huy", "xoa", "bo lich"),
    ),
    IntentRule(
        MemberIntent.EDIT_REMINDER,
        all_of=("lich",),
        any_of=("doi lich", "sua lich", "chuyen lich", "doi gio"),
    ),
    IntentRule(
        # "Nhắc chị đi ngủ sau 5 phút nữa" arrives with a pronoun-preference
        # clause in front of it often enough to be worth its own pattern: the
        # clause is an instruction about address, not a reason to treat the
        # whole message as chat.
        MemberIntent.CREATE_REMINDER,
        all_of=("xung",),
        any_of=("nhac chi", "nhac toi", "nhac em", "nhac anh", "nhac minh"),
    ),
    IntentRule(
        MemberIntent.CREATE_REMINDER,
        any_of=(
            "nhac toi",
            "nhac chi",
            "nhac anh",
            "nhac em",
            "nhac minh",
            "nhac ban",
            "nhac tui",
            "nhac to",
            "nhac cho toi",
            "nhac cho minh",
            "dat lich nhac",
            "tao lich nhac",
            "len lich nhac",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Group manager assignment (0.6.0a2) -------------------------------
    IntentRule(
        MemberIntent.VIEW_GROUP_MANAGERS,
        any_of=("ai dang quan ly group", "group nao do", "ai quan ly group"),
    ),
    IntentRule(
        MemberIntent.REMOVE_GROUP_MANAGER,
        all_of=("quan ly group",),
        any_of=("bo quyen", "thu hoi", "go quyen", "huy quyen"),
    ),
    IntentRule(
        MemberIntent.ASSIGN_GROUP_MANAGER,
        any_of=("gan quyen quan ly group", "quan ly group", "cho truong nhom"),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Cross-chat notification (before everything: these are specific) --
    #: Before the group rules: "nhắn riêng cho Linh" names a person, and
    #: resolving it as a group would produce a refusal about the wrong thing.
    IntentRule(
        MemberIntent.SEND_PRIVATE_MESSAGE,
        any_of=("nhan rieng cho", "gui rieng cho", "nhan tin rieng cho", "nhan rieng toi"),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.REGISTER_CHAT,
        any_of=(
            "dang ky day la group",
            "day la group",
            "dung group nay",
            "dang ky group nay",
        ),
    ),
    #: Sending to *several* groups at once, added in 0.6.0a3. Before the
    #: multi-destination path existed, "gửi cho các group content" either
    #: resolved to one group or went round the conversation again asking which
    #: one was meant - the reported loop. These have to be tested before the
    #: single-destination MAKE_ANNOUNCEMENT rules below, because every one of
    #: them also matches "gửi ... vào group".
    #: Registration is matched above this point, so a sentence that reaches
    #: here and mentions "đã đăng ký" is describing a *set of destinations*,
    #: not asking to register anything.
    *(
        IntentRule(
            MemberIntent.SEND_MULTI_ANNOUNCEMENT,
            all_of=(verb, marker),
            none_of=(
                *GENERATIVE_MARKERS,
                # Questions *about* the registry contain the same words as an
                # instruction to send to it. "MeoBot đang gửi được vào những
                # nhóm nào?" is somebody asking what MeoBot can reach, and
                # answering it by starting a draft would be a non-sequitur.
                "danh sach",
                "xem cac group",
                "xem group",
                "cho toi xem",
                "cho chi xem",
                "co nhung",
                "nhom nao",
                "group nao",
            ),
        )
        # "dang" is deliberately absent as a bare verb: accent-free it is
        # also "đăng ký" (register) and "đang" (currently), so "cho tôi xem
        # các group đã đăng ký" would read as a request to send to them.
        for verb in ("gui", "nhan", "dang noi dung", "dang bai", "dang tin", "thong bao", "chuyen")
        for marker in (
            # Everything.
            "tat ca",
            "toan bo",
            # Explicitly plural.
            "cac group",
            "cac nhom",
            "cac team",
            "nhung group",
            "nhung nhom",
            "moi group",
            "moi nhom",
            # Counted off a list that was just shown.
            "hai group",
            "ba group",
            "bon group",
            "nam group",
            "hai nhom",
            "ba nhom",
            # Two named destinations: the conjunction is what proves it is a
            # set. "gửi cho group Test và group Saykeng".
            "va group",
            "voi group",
            "va nhom",
            "voi nhom",
            # The one somebody just added.
            "group vua dang ky",
            "group vua tao",
            "nhom vua dang ky",
        )
    ),
    IntentRule(
        MemberIntent.CHOOSE_DESTINATIONS,
        any_of=(
            "cho toi chon group",
            "cho chi chon group",
            "cho em chon group",
            "cho minh chon group",
            "chon group nhan",
            "chon noi nhan",
            "cho toi chon nhom",
            "cho chi chon nhom",
        ),
    ),
    IntentRule(
        MemberIntent.VIEW_REGISTERED_CHATS,
        any_of=(
            "cac group da dang ky",
            "group da dang ky",
            "danh sach group",
            "danh sach nhom",
            "danh sach cac group",
            "co nhung group nao",
            "co nhung nhom nao",
            "xem cac group",
            "xem group",
            "group nao dang hoat dong",
            "group nao dang loi",
            "nhom nao dang loi",
            "gui duoc vao nhung nhom nao",
            "gui duoc vao nhung group nao",
            "gui vao duoc nhung group nao",
            "nhung group nao",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.RETRY_DELIVERY,
        any_of=("gui lai thong bao", "thu gui lai", "gui lai tin"),
    ),
    IntentRule(
        MemberIntent.VIEW_DELIVERY_STATUS,
        any_of=(
            "tin nao dang gui loi",
            "tin nao chua gui duoc",
            "tinh trang gui",
            "gui loi",
        ),
    ),
    IntentRule(
        MemberIntent.MAKE_ANNOUNCEMENT,
        any_of=(
            "thong bao cho toan phong",
            "thong bao toan phong",
            "gui thong bao",
            "thong bao cho ca phong",
            "nhac team",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    #: Sending to a *named* destination, added in 0.6.0a2.1. "Gửi Chào buổi
    #: sáng vào group Test" was classified GENERATIVE and answered by the model,
    #: which explained how to set up a bot with BotFather - for a bot that was
    #: already running and already in that group. Recognising it here is what
    #: makes the request reach a handler that can actually resolve the group,
    #: preview the message and write an outbox row.
    #:
    #: Generated per verb because a rule cannot express "(any verb) AND (any
    #: destination phrase)": ``all_of`` is a conjunction and ``any_of`` a
    #: disjunction, and this needs one of each.
    *(
        IntentRule(
            MemberIntent.MAKE_ANNOUNCEMENT,
            all_of=(verb,),
            any_of=(
                "vao group",
                "vao nhom",
                "vao team",
                "cho group",
                "cho nhom",
                "cho team",
                "toi group",
                "toi nhom",
                "len group",
                "den group",
            ),
            none_of=GENERATIVE_MARKERS,
        )
        for verb in ("gui", "nhan", "thong bao", "dang")
    ),
    #: "Thông báo cho Test: Chào buổi sáng." - the destination is named without
    #: the word "group", and the colon is what proves it was an address rather
    #: than a question. The ``none_of`` entries keep "thông báo cho tôi biết..."
    #: - a request *for* information - out of the send path.
    IntentRule(
        MemberIntent.MAKE_ANNOUNCEMENT,
        all_of=("thong bao cho",),
        none_of=(
            *GENERATIVE_MARKERS,
            "thong bao cho toi",
            "thong bao cho minh",
            "thong bao cho em biet",
            "thong bao cho anh biet",
        ),
    ),
    # --- Navigation -------------------------------------------------------
    IntentRule(
        MemberIntent.CANCEL,
        any_of=("huy thao tac", "thoi khong lam nua", "bo qua thao tac", "dung lai"),
    ),
    IntentRule(
        MemberIntent.HELP,
        # Deliberately *not* "MeoBot làm được gì?". That question already has a
        # better answer: the live capability report, filtered by what this
        # person may actually run. A static help card would be a downgrade.
        any_of=("giup toi", "huong dan su dung", "tro giup", "chi toi cach"),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.HOME,
        any_of=(
            "bat dau",
            "meobot oi",
            "toi can lam gi",
            "mo menu",
            "menu",
            "trang chu",
            "toi phai lam gi",
        ),
    ),
    # --- AI allowance -----------------------------------------------------
    IntentRule(
        MemberIntent.VIEW_AI_ALLOWANCE,
        any_of=("bao nhieu luot", "may cau ai", "con bao nhieu luot", "luot tro chuyen", "quota"),
    ),
    IntentRule(
        MemberIntent.VIEW_AI_ALLOWANCE,
        all_of=("luot",),
        any_of=("con", "xem", "bao nhieu"),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- HR: statistics ---------------------------------------------------
    IntentRule(
        MemberIntent.VIEW_MY_HR_STATS,
        any_of=(
            "thang nay toi nghi",
            "toi da nghi bao nhieu",
            "toi di muon may lan",
            "toi da di muon",
            "thong ke cua toi",
            "thong ke nghi phep cua toi",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- HR: management ---------------------------------------------------
    IntentRule(
        MemberIntent.VIEW_WHO_IS_OFF,
        any_of=("ai nghi", "co ai xin nghi", "ai di muon", "ai vang"),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.VIEW_PENDING_HR,
        any_of=(
            "don dang cho duyet",
            "don cho duyet",
            "yeu cau nao dang cho duyet",
            "ai dang co don chua xu ly",
            "don chua xu ly",
        ),
    ),
    IntentRule(
        MemberIntent.VIEW_HR_REPORT,
        any_of=(
            "bao cao nhan su",
            "thong ke nghi phep thang",
            "tong hop di muon",
            "bao cao nghi phep",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- HR: cancel / withdraw -------------------------------------------
    IntentRule(
        MemberIntent.CANCEL_HR_REQUEST,
        any_of=("huy don", "rut yeu cau", "rut don", "toi khong di muon nua", "huy yeu cau"),
    ),
    IntentRule(
        MemberIntent.CANCEL_HR_REQUEST,
        all_of=("khong nghi nua",),
    ),
    # --- HR: my requests --------------------------------------------------
    IntentRule(
        MemberIntent.VIEW_MY_HR_REQUESTS,
        any_of=(
            "don nghi cua toi",
            "yeu cau cua toi",
            "lich su nghi phep",
            "don cua toi",
            "da duoc duyet chua",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- HR: late ---------------------------------------------------------
    IntentRule(
        MemberIntent.REQUEST_LATE,
        any_of=("di muon", "den muon", "toi muon gio", "xin di tre", "den tre"),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- HR: leave --------------------------------------------------------
    IntentRule(
        MemberIntent.REQUEST_LEAVE,
        any_of=("xin nghi", "cho toi nghi", "toi nghi", "nghi phep", "xin phep nghi"),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Work: completion (before "view", so "xong" wins) -----------------
    IntentRule(
        MemberIntent.COMPLETE_WORK,
        any_of=("hoan thanh roi", "lam hoan thanh roi", "viec nay hoan thanh", "da hoan thanh"),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Work: proof ------------------------------------------------------
    IntentRule(
        MemberIntent.SUBMIT_PROOF,
        any_of=(
            "gui anh bang chung",
            "day la link bai",
            "nop ket qua",
            "gui bang chung",
            "nop bai",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Work: issues -----------------------------------------------------
    IntentRule(
        MemberIntent.REPORT_ISSUE,
        any_of=(
            "cho admin group duyet",
            "admin group chua duyet",
            "cho admin duyet",
            "admin chua duyet",
            "cho duyet",
            "bi tu choi",
            "bi go",
            "lien ket khong truy cap duoc",
            "khong dang duoc",
            "group khong cho dang",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Work: channels and schedule -------------------------------------
    IntentRule(
        MemberIntent.VIEW_CHANNELS,
        any_of=("kenh nao do toi", "page cua toi", "kenh toi dang lam", "toi phu trach kenh"),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.VIEW_SCHEDULE_GAPS,
        any_of=("thieu bai", "kenh nao chua dang", "du lich chua", "con thieu bai o dau"),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.VIEW_PERSONAL_REPORT,
        any_of=(
            "ket qua cua toi",
            "tuan nay toi lam the nao",
            "ty le hoan thanh cua toi",
            "bao cao ca nhan",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Work: progress ---------------------------------------------------
    IntentRule(
        MemberIntent.UPDATE_PROGRESS,
        any_of=("toi dang lam", "con thieu", "duoc khoang"),
        none_of=GENERATIVE_MARKERS,
    ),
    IntentRule(
        MemberIntent.UPDATE_PROGRESS,
        any_of=("lam duoc", "da lam", "toi lam"),
        none_of=GENERATIVE_MARKERS,
        requires_number=True,
    ),
    # --- Work: accept -----------------------------------------------------
    IntentRule(
        MemberIntent.ACCEPT_WORK,
        any_of=("toi nhan viec", "nhan viec nay", "de toi lam", "toi bat dau lam"),
        none_of=GENERATIVE_MARKERS,
    ),
    # --- Work: view (last of the work rules, it is the broadest) ----------
    IntentRule(
        MemberIntent.VIEW_WORK,
        any_of=(
            "viec cua toi",
            "viec hom nay",
            "hom nay toi co viec gi",
            "viec qua han",
            "viec chua lam",
            "xem viec",
            "toi con viec nao",
        ),
        none_of=GENERATIVE_MARKERS,
    ),
)


@dataclass(frozen=True, slots=True)
class IntentMatch:
    """What a message was understood to mean."""

    intent: MemberIntent
    text: NormalizedText

    @property
    def is_operational(self) -> bool:
        """True when handling this costs the Member no chat slot."""
        return self.intent in OPERATIONAL_INTENTS


def classify(text: str, *, normalization_enabled: bool = True) -> IntentMatch:
    """Decide what a Member message is asking for.

    Falls back to :attr:`MemberIntent.GENERATIVE`, which is the only outcome
    that reserves a chat slot - so an unrecognised message is treated as
    conversation rather than being refused.
    """
    normalized = normalize(text, enabled=normalization_enabled)
    if not normalized.matchable:
        return IntentMatch(intent=MemberIntent.GENERATIVE, text=normalized)

    if any(marker in normalized.matchable for marker in GENERATIVE_MARKERS):
        return IntentMatch(intent=MemberIntent.GENERATIVE, text=normalized)

    for rule in RULES:
        if rule.matches(normalized):
            return IntentMatch(intent=rule.intent, text=normalized)
    return IntentMatch(intent=MemberIntent.GENERATIVE, text=normalized)


def is_operational(text: str, *, normalization_enabled: bool = True) -> bool:
    """The quota boundary, in one call.

    Used by the access gate *before* a chat slot is reserved, which is the whole
    reason this module does not involve a model.
    """
    return classify(text, normalization_enabled=normalization_enabled).is_operational


#: A request can be both: "Tôi đã đăng bài rồi, viết giúp tôi 5 comment." The
#: operational half is handled and confirmed first, and the generative half is
#: offered separately so the Member chooses whether to spend a slot on it.
_SPLIT = re.compile(r"[.,;]|\bva\b|\broi\b")


def split_mixed(text: str, *, normalization_enabled: bool = True) -> tuple[str, str] | None:
    """Split "operational bit, then please write me something" in two.

    Returns ``(operational_part, generative_part)`` using the *original*
    wording of each half, or ``None`` when the message is not mixed.
    """
    match = classify(text, normalization_enabled=normalization_enabled)
    if match.intent is not MemberIntent.GENERATIVE:
        return None

    pieces = [piece.strip() for piece in re.split(r"[.,;]", text) if piece.strip()]
    if len(pieces) < 2:
        return None

    operational = [
        piece
        for piece in pieces
        if classify(piece, normalization_enabled=normalization_enabled).is_operational
    ]
    generative = [
        piece
        for piece in pieces
        if not classify(piece, normalization_enabled=normalization_enabled).is_operational
    ]
    if not operational or not generative:
        return None
    return " ".join(operational), " ".join(generative)
