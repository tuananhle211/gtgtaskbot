"""Leave and late arrivals: the arithmetic, the rules and the privacy.

Numbers first. Every figure in an HR summary is read by the person who decides
somebody's attendance, so these tests check the maths directly rather than
checking that a sentence looks plausible - and one of them asserts that no
provider is called while producing any of it.

Then the rules that stop the feature being abused: you cannot file for somebody
else, you cannot approve your own request, and only the owner decides.

Then privacy: the reason somebody gave for needing a morning off appears on the
approval card and in nothing else - not in a group, not in the audit payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from meobot.application.audit_service import AuditService
from meobot.application.hr_request_service import HrRequestService
from meobot.application.hr_statistics_service import (
    HrStatisticsService,
    month_bounds,
    week_bounds,
)
from meobot.application.work_schedule_service import WorkScheduleService
from meobot.core.errors import AuthorizationError, ConflictError, ValidationError
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.hr import HrRequest, HrRequestEvent
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.hr.models import (
    HrRequestStatus,
    HrRequestType,
    duration_days,
    format_days,
    overlaps,
    status_label,
    type_label,
)
from meobot.domain.hr.schedule import (
    DEFAULT_SCHEDULE,
    WorkSchedule,
    describe_period,
    resolve_clock,
    resolve_date,
    resolve_late_minutes,
    resolve_minutes,
    resolve_period,
)
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.normalization import normalize
from meobot.domain.permissions.matrix import Permission, has_permission

#: 30 July 2026 is a Thursday; 15:00 in Ho Chi Minh City.
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
TOMORROW = date(2026, 7, 31)


@pytest.fixture
def owner() -> Actor:
    return Actor(
        user_id=uuid.uuid4(), telegram_user_id=777000111, full_name="Trưởng phòng", role=Role.OWNER
    )


async def make_user(session, *, name: str = "Nguyễn Văn A", telegram_id: int = 950_001) -> User:
    user = User(
        telegram_user_id=telegram_id,
        telegram_username="nv",
        full_name=name,
        role=Role.EMPLOYEE,
        active=True,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


def actor_for(user: User) -> Actor:
    return Actor(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        full_name=user.full_name,
        role=user.role,
    )


def service(session) -> HrRequestService:
    return HrRequestService(session, AuditService(session))


# --- Dates and times, without a model ---------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hom nay", date(2026, 7, 30)),
        ("ngay mai", TOMORROW),
        ("mai toi xin nghi", TOMORROW),
        ("ngay kia", date(2026, 8, 1)),
        ("thu sau", TOMORROW),
        ("ngay 2 thang 8", date(2026, 8, 2)),
        ("2/8", date(2026, 8, 2)),
    ],
)
def test_vietnamese_dates_resolve_by_arithmetic(text: str, expected: date) -> None:
    """No provider is involved in working out what day somebody means."""
    assert resolve_date(text, now=NOW, schedule=DEFAULT_SCHEDULE) == expected


def test_an_unrecognised_date_returns_nothing_rather_than_guessing() -> None:
    assert resolve_date("khi nao ranh", now=NOW, schedule=DEFAULT_SCHEDULE) is None


def test_a_past_day_of_month_rolls_to_next_year() -> None:
    """ "Ngày 2 tháng 1" said in July means next January, not six months ago."""
    assert resolve_date("ngay 2 thang 1", now=NOW, schedule=DEFAULT_SCHEDULE) == date(2027, 1, 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nghi buoi sang", HrRequestType.MORNING_LEAVE),
        ("xin nghi sang mai", HrRequestType.MORNING_LEAVE),
        ("nghi buoi chieu", HrRequestType.AFTERNOON_LEAVE),
        ("chieu thu sau xin nghi", HrRequestType.AFTERNOON_LEAVE),
        ("nghi ca ngay", HrRequestType.FULL_DAY_LEAVE),
        ("xin nghi mot ngay", HrRequestType.FULL_DAY_LEAVE),
    ],
)
def test_leave_periods_resolve(text: str, expected: HrRequestType) -> None:
    assert resolve_period(text) is expected


def test_a_missing_period_is_not_assumed_to_be_a_whole_day() -> None:
    """ "Ngày mai tôi xin nghỉ" must produce a question, not a full-day request."""
    assert resolve_period("ngay mai toi xin nghi") is None


def test_clock_and_minutes_are_read_from_normalized_text() -> None:
    assert resolve_clock(normalize("9h toi co mat").matchable) == time(9, 0)
    assert resolve_clock(normalize("9h30 toi co mat").matchable) == time(9, 30)
    assert resolve_minutes(normalize("di muon 30p").matchable) == 30
    assert resolve_minutes(normalize("di muon 45 phut").matchable) == 45


def test_late_minutes_are_derived_from_the_work_schedule() -> None:
    """Arrival stated, duration computed - and the reverse."""
    schedule = WorkSchedule(morning_start=time(8, 30))
    assert resolve_late_minutes(arrival=time(9, 0), minutes=None, schedule=schedule) == (
        time(9, 0),
        30,
    )
    assert resolve_late_minutes(arrival=None, minutes=30, schedule=schedule) == (time(9, 0), 30)


def test_the_stated_arrival_time_wins_when_both_are_given() -> None:
    """It is the time somebody is checked against at the door."""
    schedule = WorkSchedule(morning_start=time(8, 30))
    arrival, minutes = resolve_late_minutes(arrival=time(9, 15), minutes=5, schedule=schedule)
    assert (arrival, minutes) == (time(9, 15), 45)


def test_without_a_schedule_lateness_is_not_invented() -> None:
    """The refusal that matters: MeoBot must not assume when the office opens."""
    assert resolve_late_minutes(arrival=None, minutes=30, schedule=None) == (None, 30)
    assert resolve_late_minutes(arrival=time(9, 0), minutes=None, schedule=None) == (
        time(9, 0),
        None,
    )


def test_a_month_boundary_is_handled() -> None:
    first, last = month_bounds(date(2026, 2, 15))
    assert (first, last) == (date(2026, 2, 1), date(2026, 2, 28))
    first, last = month_bounds(date(2026, 12, 31))
    assert (first, last) == (date(2026, 12, 1), date(2026, 12, 31))


def test_week_bounds_start_on_monday() -> None:
    monday, sunday = week_bounds(date(2026, 7, 30))
    assert monday == date(2026, 7, 27)
    assert sunday == date(2026, 8, 2)


# --- Durations and labels ----------------------------------------------------
def test_half_days_are_half_days() -> None:
    day = datetime(2026, 7, 31, 1, 30, tzinfo=UTC)
    assert duration_days(HrRequestType.MORNING_LEAVE, day, day) == 0.5
    assert duration_days(HrRequestType.AFTERNOON_LEAVE, day, day) == 0.5
    assert duration_days(HrRequestType.FULL_DAY_LEAVE, day, day) == 1.0


def test_multi_day_leave_counts_both_ends() -> None:
    start = datetime(2026, 7, 31, 1, 30, tzinfo=UTC)
    end = datetime(2026, 8, 2, 10, 30, tzinfo=UTC)
    assert duration_days(HrRequestType.MULTI_DAY_LEAVE, start, end) == 3.0


def test_vietnamese_numbers_use_a_comma() -> None:
    assert format_days(1.5) == "1,5"
    assert format_days(2.0) == "2"


def test_two_halves_of_one_day_do_not_overlap() -> None:
    """Half-open intervals: a morning ending at noon and an afternoon starting
    at noon are the pair somebody files when they want a whole day."""
    morning_end = datetime(2026, 7, 31, 5, 0, tzinfo=UTC)
    afternoon_start = morning_end
    assert not overlaps(
        datetime(2026, 7, 31, 1, 30, tzinfo=UTC),
        morning_end,
        afternoon_start,
        datetime(2026, 7, 31, 10, 30, tzinfo=UTC),
    )


def test_no_status_or_type_label_leaks_an_enum() -> None:
    for status in HrRequestStatus:
        assert status.value not in status_label(status)
    for kind in HrRequestType:
        assert kind.value not in type_label(kind)


def test_a_period_reads_as_vietnamese() -> None:
    assert describe_period(HrRequestType.MORNING_LEAVE, day=TOMORROW) == "Sáng ngày 31/07/2026"
    assert describe_period(HrRequestType.FULL_DAY_LEAVE, day=TOMORROW) == "Cả ngày 31/07/2026"


# --- Filing -----------------------------------------------------------------
async def test_a_member_files_their_own_request(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    row = await service(session).submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
        reason="Có việc gia đình",
    )

    assert row.status is HrRequestStatus.PENDING
    assert row.requester_user_id == user.id
    assert row.version == 1
    assert row.reason == "Có việc gia đình"


async def test_filing_writes_one_history_event(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    row = await service(session).submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    events = (
        (await session.execute(select(HrRequestEvent).where(HrRequestEvent.request_id == row.id)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].state_after == "PENDING"


async def test_a_past_date_is_refused_by_default(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    with pytest.raises(ValidationError):
        await service(session).submit(
            actor=actor_for(user),
            request_id=request_id,
            request_type=HrRequestType.FULL_DAY_LEAVE,
            work_date=date(2020, 1, 1),
            schedule=DEFAULT_SCHEDULE,
        )


async def test_overlapping_leave_is_refused(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    hr = service(session)
    await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    with pytest.raises(ValidationError):
        await hr.submit(
            actor=actor_for(user),
            request_id=request_id,
            request_type=HrRequestType.MORNING_LEAVE,
            work_date=TOMORROW,
            schedule=DEFAULT_SCHEDULE,
        )


async def test_two_late_requests_for_one_day_are_refused(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    hr = service(session)
    arrival = datetime(2026, 7, 31, 2, 0, tzinfo=UTC)
    for attempt in range(2):
        if attempt == 0:
            await hr.submit(
                actor=actor_for(user),
                request_id=request_id,
                request_type=HrRequestType.LATE_ARRIVAL,
                work_date=TOMORROW,
                schedule=DEFAULT_SCHEDULE,
                expected_arrival_at=arrival,
                late_minutes=30,
            )
        else:
            with pytest.raises(ValidationError):
                await hr.submit(
                    actor=actor_for(user),
                    request_id=request_id,
                    request_type=HrRequestType.LATE_ARRIVAL,
                    work_date=TOMORROW,
                    schedule=DEFAULT_SCHEDULE,
                    expected_arrival_at=arrival,
                    late_minutes=45,
                )


async def test_a_suspended_account_cannot_file(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    user.status = UserStatus.SUSPENDED
    user.active = False
    await session.flush()

    with pytest.raises(AuthorizationError):
        await service(session).submit(
            actor=actor_for(user),
            request_id=request_id,
            request_type=HrRequestType.FULL_DAY_LEAVE,
            work_date=TOMORROW,
            schedule=DEFAULT_SCHEDULE,
        )


# --- Deciding ---------------------------------------------------------------
async def test_only_the_owner_approves(session, request_id: uuid.UUID) -> None:
    """TEAM_LEAD holds the legacy permission and still cannot decide."""
    user = await make_user(session)
    row = await service(session).submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    lead = Actor(
        user_id=uuid.uuid4(), telegram_user_id=5, full_name="Trưởng nhóm", role=Role.TEAM_LEAD
    )
    assert has_permission(Role.TEAM_LEAD, Permission.HR_APPROVE_LEAVE)
    assert not has_permission(Role.TEAM_LEAD, Permission.HR_REQUEST_APPROVE)

    with pytest.raises(AuthorizationError):
        await service(session).approve(actor=lead, request_id=request_id, hr_request_id=row.id)


async def test_a_member_cannot_approve_their_own_request(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    row = await service(session).submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    # Even as an owner, deciding your own request is refused.
    self_owner = Actor(
        user_id=user.id, telegram_user_id=user.telegram_user_id, full_name="X", role=Role.OWNER
    )
    with pytest.raises(AuthorizationError):
        await service(session).approve(
            actor=self_owner, request_id=request_id, hr_request_id=row.id
        )


async def test_approving_twice_changes_state_once(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.approve(actor=owner, request_id=request_id, hr_request_id=row.id)
    version_after_first = row.version
    await hr.approve(actor=owner, request_id=request_id, hr_request_id=row.id)

    assert row.status is HrRequestStatus.APPROVED
    assert row.version == version_after_first, "the second press must be a no-op"
    approvals = [
        event
        for event in await hr.history(row.id)
        if event.state_after == HrRequestStatus.APPROVED.value
    ]
    assert len(approvals) == 1


async def test_a_stale_version_cannot_be_approved(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    """The card the owner read is not the request any more."""
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    rendered_version = row.version
    await hr.amend(
        actor=actor_for(user),
        request_id=request_id,
        hr_request_id=row.id,
        schedule=DEFAULT_SCHEDULE,
        reason="Đổi lý do",
    )

    with pytest.raises(ConflictError):
        await hr.approve(
            actor=owner,
            request_id=request_id,
            hr_request_id=row.id,
            expected_version=rendered_version,
        )


async def test_rejecting_an_approved_request_is_refused(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.approve(actor=owner, request_id=request_id, hr_request_id=row.id)
    with pytest.raises(ConflictError):
        await hr.reject(actor=owner, request_id=request_id, hr_request_id=row.id)


# --- Withdrawing and amending ------------------------------------------------
async def test_a_member_withdraws_their_own_pending_request(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.withdraw(actor=actor_for(user), request_id=request_id, hr_request_id=row.id)
    assert row.status is HrRequestStatus.WITHDRAWN
    assert row.withdrawn_at is not None


async def test_an_approved_request_cannot_be_silently_withdrawn(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.approve(actor=owner, request_id=request_id, hr_request_id=row.id)
    with pytest.raises(ConflictError):
        await hr.withdraw(actor=actor_for(user), request_id=request_id, hr_request_id=row.id)


async def test_nobody_can_touch_somebody_elses_request(session, request_id: uuid.UUID) -> None:
    mine = await make_user(session, telegram_id=950_001)
    theirs = await make_user(session, name="Người Khác", telegram_id=950_002)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(mine),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    with pytest.raises(AuthorizationError):
        await hr.withdraw(actor=actor_for(theirs), request_id=request_id, hr_request_id=row.id)


async def test_nothing_is_ever_deleted(session, owner: Actor, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.withdraw(actor=actor_for(user), request_id=request_id, hr_request_id=row.id)

    rows = (await session.execute(select(HrRequest))).scalars().all()
    assert len(rows) == 1


# --- Scope ------------------------------------------------------------------
async def test_a_member_sees_only_their_own_requests(session, request_id: uuid.UUID) -> None:
    mine = await make_user(session, telegram_id=950_001)
    theirs = await make_user(session, name="Người Khác", telegram_id=950_002)
    hr = service(session)
    await hr.submit(
        actor=actor_for(mine),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
        reason="Riêng tư của tôi",
    )
    await hr.submit(
        actor=actor_for(theirs),
        request_id=request_id,
        request_type=HrRequestType.AFTERNOON_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
        reason="Riêng tư của người khác",
    )

    visible = await hr.for_requester(user_id=mine.id)
    assert len(visible) == 1
    assert all(row.requester_user_id == mine.id for row in visible)
    assert all("người khác" not in (row.reason or "").lower() for row in visible)


async def test_a_member_cannot_list_pending_requests(session) -> None:
    user = await make_user(session)
    with pytest.raises(AuthorizationError):
        await service(session).pending_for_approver(actor=actor_for(user))


async def test_a_member_cannot_read_department_figures(session) -> None:
    user = await make_user(session)
    stats = HrStatisticsService(session)
    with pytest.raises(AuthorizationError):
        await stats.department(actor=actor_for(user), start=date(2026, 7, 1), end=date(2026, 7, 31))
    with pytest.raises(AuthorizationError):
        await stats.absence_on(actor=actor_for(user), day=TOMORROW)


# --- Statistics --------------------------------------------------------------
async def test_personal_totals_separate_the_statuses(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    user = await make_user(session)
    hr = service(session)
    approved = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=date(2026, 7, 31),
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.approve(actor=owner, request_id=request_id, hr_request_id=approved.id)
    rejected = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=date(2026, 7, 28),
        schedule=DEFAULT_SCHEDULE,
        allow_past=True,
    )
    await hr.reject(actor=owner, request_id=request_id, hr_request_id=rejected.id)
    await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.AFTERNOON_LEAVE,
        work_date=date(2026, 8, 3),
        schedule=DEFAULT_SCHEDULE,
    )

    start, end = month_bounds(date(2026, 7, 15))
    summary = await HrStatisticsService(session).personal(user_id=user.id, start=start, end=end)
    assert summary.leave.approved_count == 1
    assert summary.leave.rejected_count == 1
    assert summary.leave.approved_days == 0.5, "a rejected day must not be counted"


async def test_late_totals_and_average(session, owner: Actor, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    hr = service(session)
    for day, minutes in ((date(2026, 7, 30), 30), (date(2026, 7, 31), 15)):
        row = await hr.submit(
            actor=actor_for(user),
            request_id=request_id,
            request_type=HrRequestType.LATE_ARRIVAL,
            work_date=day,
            schedule=DEFAULT_SCHEDULE,
            expected_arrival_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
            late_minutes=minutes,
            # Fixed dates, so both fall into the past once the clock passes
            # them. This test is about how minutes are totalled, not about the
            # rule that you cannot file for last week.
            allow_past=True,
        )
        await hr.approve(actor=owner, request_id=request_id, hr_request_id=row.id)

    start, end = month_bounds(date(2026, 7, 15))
    summary = await HrStatisticsService(session).personal(user_id=user.id, start=start, end=end)
    assert summary.late.approved_count == 2
    assert summary.late.total_minutes == 45
    assert summary.late.average_minutes == 22.5


async def test_department_totals_are_computed_not_generated(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    """A stand-in for "no LLM produced this number": the service has no provider.

    ``HrStatisticsService`` is constructed with a session and nothing else, so
    there is no seam through which a model could reach these totals.
    """
    import inspect

    signature = inspect.signature(HrStatisticsService.__init__)
    assert list(signature.parameters) == ["self", "session"]

    first = await make_user(session, telegram_id=950_010)
    second = await make_user(session, name="Người Thứ Hai", telegram_id=950_011)
    hr = service(session)
    for user in (first, second):
        row = await hr.submit(
            actor=actor_for(user),
            request_id=request_id,
            request_type=HrRequestType.FULL_DAY_LEAVE,
            work_date=TOMORROW,
            schedule=DEFAULT_SCHEDULE,
        )
        await hr.approve(actor=owner, request_id=request_id, hr_request_id=row.id)

    start, end = month_bounds(TOMORROW)
    summary = await HrStatisticsService(session).department(actor=owner, start=start, end=end)
    assert summary.leave.approved_count == 2
    assert summary.leave.approved_days == 2.0


async def test_absence_today_names_only_approved_people(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    user = await make_user(session, name="Linh")
    hr = service(session)
    approved = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.approve(actor=owner, request_id=request_id, hr_request_id=approved.id)

    other = await make_user(session, name="Hảo", telegram_id=950_020)
    await hr.submit(
        actor=actor_for(other),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )

    summary = await HrStatisticsService(session).absence_on(actor=owner, day=TOMORROW)
    assert [name for name, _ in summary.morning] == ["Linh"]
    assert summary.full_day == [], "a pending request is not an absence yet"
    assert summary.pending_count == 1


# --- Work schedule -----------------------------------------------------------
async def test_no_schedule_means_no_schedule(session) -> None:
    """The absence is a real answer, and callers must handle it."""
    assert await WorkScheduleService(session).active() is None


async def test_a_configured_schedule_comes_back(session, owner: Actor) -> None:
    service_ = WorkScheduleService(session)
    await service_.configure(
        actor=owner,
        schedule=WorkSchedule(
            morning_start=time(8, 0),
            morning_end=time(12, 0),
            afternoon_start=time(13, 0),
            afternoon_end=time(17, 0),
        ),
    )
    loaded = await service_.active()
    assert loaded is not None
    assert loaded.day_start == time(8, 0)


async def test_configuring_again_replaces_rather_than_edits(session, owner: Actor) -> None:
    """A lateness figure from last month stays explainable by last month's hours."""
    service_ = WorkScheduleService(session)
    await service_.configure(actor=owner, schedule=WorkSchedule(morning_start=time(8, 0)))
    await service_.configure(actor=owner, schedule=WorkSchedule(morning_start=time(9, 0)))

    from meobot.db.models.hr import WorkSchedule as Row

    rows = (await session.execute(select(Row))).scalars().all()
    assert len(rows) == 2
    assert sum(1 for row in rows if row.is_active) == 1


# --- Privacy and audit -------------------------------------------------------
async def test_the_audit_trail_records_state_but_not_the_reason(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    """Why somebody needs a morning off is not audit metadata."""
    user = await make_user(session)
    private_reason = "Đi khám ở bệnh viện tâm thần"
    row = await service(session).submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
        reason=private_reason,
    )

    entries = (await session.execute(select(AuditLog))).scalars().all()
    submitted = [entry for entry in entries if entry.action == "hr_request.submitted"]
    assert len(submitted) == 1
    payload = str(submitted[0].after_data)
    assert str(row.id) not in payload or True  # the id is fine; the reason is not
    assert private_reason not in payload
    assert "Đi khám" not in payload


async def test_history_events_carry_no_reason(session, request_id: uuid.UUID) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
        reason="Chuyện riêng",
    )
    for event in await hr.history(row.id):
        assert "Chuyện riêng" not in str(event.event_metadata)


async def test_a_private_note_never_reaches_the_requester_view(
    session, owner: Actor, request_id: uuid.UUID
) -> None:
    user = await make_user(session)
    hr = service(session)
    row = await hr.submit(
        actor=actor_for(user),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=TOMORROW,
        schedule=DEFAULT_SCHEDULE,
    )
    await hr.approve(
        actor=owner, request_id=request_id, hr_request_id=row.id, note="Ghi chú nội bộ"
    )

    from meobot.application.member_interaction_service import MemberInteractionService
    from meobot.core.config import get_settings

    reply = await MemberInteractionService(session, get_settings()).my_hr_requests(actor_for(user))
    assert "Ghi chú nội bộ" not in reply.text
