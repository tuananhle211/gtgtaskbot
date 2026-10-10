"""Demo data for the Ads unit on a LOCAL database. Never run against production.

Adds Ads people (head, orderers, a Leader and staff per function) and about
thirty orders spread over every stage and node status, driven through the real
order engine so events, approvals, inbox rows and KPI are all consistent.

Run inside the api container (it has the env and the code):

    docker compose exec -T api python - < scripts/seed_ads_demo.py

Re-running is safe: people are matched by ``telegram_username`` (``demo_*``)
and orders are only seeded once (marker: the demo reference link).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from meobot.application.orders.command_service import CreateOrderCommand, NodePlan
from meobot.application.orders.services import build_order_services
from meobot.core.config import get_settings
from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember, UnitVideoKind
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.models import (
    OrderNodeType,
    OrderScriptSource,
    OrderVideoType,
    last_production_node,
    needs_design_link,
)
from meobot.domain.units.models import UnitMemberRole

MARKER = "https://example.com/demo-ref"

# (username, full name, unit role, is_lead, member code)
PEOPLE: list[tuple[str, str, UnitMemberRole, bool, str | None]] = [
    ("demo_head", "Trần Minh Trang", UnitMemberRole.HEAD, False, "TRANG"),
    ("demo_lananh", "Nguyễn Lan Anh", UnitMemberRole.ORDERER, False, "LANANH"),
    ("demo_bao", "Phạm Quốc Bảo", UnitMemberRole.ORDERER, False, "BAO"),
    ("demo_thuha", "Lê Thu Hà", UnitMemberRole.ORDERER, False, "THUHA"),
    ("demo_bt_lead", "Hiền Lương", UnitMemberRole.BIEN_TAP, True, None),
    ("demo_bt_mai", "Ngọc Mai", UnitMemberRole.BIEN_TAP, False, None),
    ("demo_bt_huy", "Đức Huy", UnitMemberRole.BIEN_TAP, False, None),
    ("demo_tk_lead", "Thảo Vy", UnitMemberRole.THIET_KE, True, None),
    ("demo_tk_khoi", "Minh Khôi", UnitMemberRole.THIET_KE, False, None),
    ("demo_tk_han", "Gia Hân", UnitMemberRole.THIET_KE, False, None),
    ("demo_dung_lead", "Hoàng Nam", UnitMemberRole.DUNG, True, None),
    ("demo_dung_dat", "Tiến Đạt", UnitMemberRole.DUNG, False, None),
    ("demo_dung_linh", "Phương Linh", UnitMemberRole.DUNG, False, None),
    ("demo_dung_khanh", "Bảo Khánh", UnitMemberRole.DUNG, False, None),
]

LEADS = {
    OrderNodeType.BIEN_TAP: "demo_bt_lead",
    OrderNodeType.THIET_KE: "demo_tk_lead",
    OrderNodeType.DUNG: "demo_dung_lead",
}
STAFF = {
    OrderNodeType.BIEN_TAP: ["demo_bt_mai", "demo_bt_huy"],
    OrderNodeType.THIET_KE: ["demo_tk_khoi", "demo_tk_han"],
    OrderNodeType.DUNG: ["demo_dung_dat", "demo_dung_linh", "demo_dung_khanh"],
}

TITLES = [
    "Serum B5 - hook 3 giây đầu",
    "Kem chống nắng mùa hè - UGC review",
    "Viên uống collagen - trước/sau 30 ngày",
    "Sữa rửa mặt dịu nhẹ - so sánh pH",
    "Combo trị mụn - bác sĩ tư vấn",
    "Toner hoa cúc - routine buổi sáng",
    "Mặt nạ ngủ - unbox quà tặng",
    "Dầu gội thảo dược - 7 ngày trải nghiệm",
    "Vitamin C - phản hồi khách hàng",
    "Kem dưỡng ẩm - thử thách 24h",
    "Sale 10.10 - teaser 15s",
    "Livestream recap - highlight",
    "Tẩy da chết - myth vs fact",
    "Son dưỡng - 5 cách dùng",
    "Xịt khoáng - văn phòng máy lạnh",
]

# (video type, where to stop, priority, age in days)
# stops: PENDING, RETURNED, CANCELLED_EARLY, or (node, state) with state in
# UNASSIGNED / ASSIGNED / ACCEPTED / RETURNED / REVIEW; then FINAL_REVIEW,
# FINAL_RETURNED, DONE, CANCELLED_LATE. There is no link step: the last
# production node hands the product link in, and its completion opens the
# final review (after the script lead's video review where that applies).
SCENARIOS: list[tuple[str, Any, bool, int]] = [
    ("BTD", "PENDING", False, 0),
    ("D", "PENDING", True, 1),
    ("TD", "PENDING", False, 9),
    ("BTD", "RETURNED", False, 2),
    ("D", "CANCELLED_EARLY", False, 3),
    ("BTD", ("BIEN_TAP", "UNASSIGNED"), False, 1),
    ("BTD", ("BIEN_TAP", "ASSIGNED"), True, 2),
    ("BTD", ("BIEN_TAP", "ACCEPTED"), False, 8),
    ("TD", ("THIET_KE", "UNASSIGNED"), False, 1),
    ("BTD", ("THIET_KE", "ASSIGNED"), False, 3),
    ("TD", ("THIET_KE", "ACCEPTED"), True, 4),
    ("D", ("DUNG", "UNASSIGNED"), False, 0),
    ("TD", ("DUNG", "ASSIGNED"), False, 2),
    ("BTD", ("DUNG", "ACCEPTED"), False, 10),
    ("D", ("DUNG", "REVIEW"), True, 3),
    ("BTD", ("DUNG", "REVIEW"), False, 5),
    ("TD", ("DUNG", "RETURNED"), False, 6),
    ("D", ("DUNG", "ASSIGNED"), False, 4),
    ("BTD", ("DUNG", "ACCEPTED"), True, 5),
    ("TD", "FINAL_REVIEW", False, 5),
    ("BTD", "FINAL_REVIEW", False, 7),
    ("D", "FINAL_RETURNED", False, 6),
    ("D", "DONE", False, 6),
    ("TD", "DONE", False, 9),
    ("BTD", "DONE", True, 12),
    ("BTD", "DONE", False, 15),
    ("TD", "CANCELLED_LATE", False, 8),
    ("D", "DONE", False, 20),
    ("BTD", ("BIEN_TAP", "ACCEPTED"), False, 0),
    ("TD", ("THIET_KE", "ASSIGNED"), False, 11),
    # The flexible process (0044): any non-empty subset of B / T / D.
    ("BD", "PENDING", False, 0),
    ("B", ("BIEN_TAP", "ACCEPTED"), False, 1),
    ("T", "FINAL_REVIEW", False, 2),
    ("BT", ("THIET_KE", "ACCEPTED"), False, 3),
    ("BD", "DONE", False, 4),
]


def actor_of(user: User) -> Actor:
    return Actor(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        telegram_username=user.telegram_username,
        full_name=user.full_name,
        role=user.role,
    )


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    async with db.session_factory() as session:
        unit = await session.scalar(select(OrgUnit).where(OrgUnit.code == "ADS"))
        assert unit is not None, "run migration 0042 first"

        people: dict[str, User] = {}
        for username, name, role, is_lead, code in PEOPLE:
            user = await session.scalar(select(User).where(User.telegram_username == username))
            if user is None:
                user = User(
                    telegram_username=username,
                    full_name=name,
                    role=Role.TEAM_LEAD
                    if is_lead or role is UnitMemberRole.HEAD
                    else Role.EMPLOYEE,
                    active=True,
                )
                session.add(user)
                await session.flush()
            tag = await session.scalar(
                select(OrgUnitMember).where(
                    OrgUnitMember.unit_id == unit.id, OrgUnitMember.user_id == user.id
                )
            )
            if tag is None:
                session.add(
                    OrgUnitMember(
                        unit_id=unit.id,
                        user_id=user.id,
                        role=role,
                        is_lead=is_lead,
                        member_code=code,
                    )
                )
            people[username] = user
        await session.commit()
        print(f"people: {len(people)} Ads members ready")

        # Resumable: scenarios already seeded (counted by the marker) are skipped.
        done = len(
            (await session.scalars(select(Order.id).where(Order.reference_link == MARKER))).all()
        )
        if done >= len(SCENARIOS):
            print("orders: demo orders already there, skipping")
            return

        # 0044 seeds the unit's video kinds; the form requires one once any exists.
        kinds = (
            await session.scalars(
                select(UnitVideoKind.id)
                .where(UnitVideoKind.unit_id == unit.id, UnitVideoKind.active.is_(True))
                .order_by(UnitVideoKind.sort_order)
            )
        ).all()

        actors = {username: actor_of(user) for username, user in people.items()}
        services = build_order_services(session, settings)
        cmd = services.commands
        head = actors["demo_head"]
        orderers = ["demo_lananh", "demo_bao", "demo_thuha"]

        async def fresh(order_id: uuid.UUID) -> tuple[Order, dict[OrderNodeType, OrderNode]]:
            # populate_existing reloads rows without expiring other objects
            # (an expired attribute would lazy-load outside the async context).
            order = (
                await session.scalars(
                    select(Order)
                    .where(Order.id == order_id)
                    .execution_options(populate_existing=True)
                )
            ).one()
            nodes = (
                await session.scalars(
                    select(OrderNode)
                    .where(OrderNode.order_id == order_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
            return order, {node.node_type: node for node in nodes}

        def rid() -> uuid.UUID:
            return uuid.uuid4()

        for index, (vt, stop, priority, age) in enumerate(SCENARIOS):
            if index < done:
                continue
            owner = orderers[index % len(orderers)]
            video_type = OrderVideoType(vt)
            title = TITLES[index % len(TITLES)]
            order = await cmd.create(
                actor=actors[owner],
                request_id=rid(),
                command=CreateOrderCommand(
                    title=f"{title} ({vt})",
                    video_type=video_type,
                    order_content=(
                        "Mục tiêu: tăng CTR quảng cáo.\nHook: câu hỏi gây tò mò.\n"
                        "Thông điệp chính: an toàn cho da nhạy cảm.\nCTA: mua ngay -20%."
                    ),
                    script_source=OrderScriptSource.AI if index % 2 else None,
                    design_link="https://example.com/design/" + str(index)
                    if needs_design_link(video_type)
                    else None,
                    reference_link=MARKER,
                    source_link="https://example.com/source/" + str(index),
                    video_kind_id=kinds[index % len(kinds)] if kinds else None,
                    desired_deadline_at=utcnow() + timedelta(days=3 + index % 5),
                ),
            )
            oid = order.id
            if priority:
                o, _ = await fresh(oid)
                await cmd.set_priority(
                    actor=head,
                    request_id=rid(),
                    order_id=oid,
                    expected_version=o.version,
                    is_priority=True,
                )

            async def run(oid: uuid.UUID, stop: Any, index: int, owner: str) -> None:
                o, _ = await fresh(oid)
                if stop == "PENDING":
                    return
                if stop == "RETURNED":
                    await cmd.return_order(
                        actor=head,
                        request_id=rid(),
                        order_id=oid,
                        expected_version=o.version,
                        reason="Thiếu insight khách hàng, bổ sung giúp chị nhé.",
                    )
                    return
                if stop == "CANCELLED_EARLY":
                    await cmd.cancel(
                        actor=head,
                        request_id=rid(),
                        order_id=oid,
                        expected_version=o.version,
                        reason="Trùng ý tưởng với order tuần trước.",
                    )
                    return
                await cmd.approve_order(
                    actor=head, request_id=rid(), order_id=oid, expected_version=o.version
                )
                for node_type in (
                    OrderNodeType.BIEN_TAP,
                    OrderNodeType.THIET_KE,
                    OrderNodeType.DUNG,
                ):
                    o, nodes = await fresh(oid)
                    node = nodes[node_type]
                    if node.status.value == "BO_QUA":
                        continue
                    here = isinstance(stop, tuple) and stop[0] == node_type.value
                    if here and stop[1] == "UNASSIGNED":
                        return
                    lead = actors[LEADS[node_type]]
                    worker_name = STAFF[node_type][index % len(STAFF[node_type])]
                    worker = actors[worker_name]
                    await cmd.assign(
                        actor=lead,
                        request_id=rid(),
                        order_id=oid,
                        node_id=node.id,
                        expected_version=o.version,
                        assignee_user_id=worker.user_id,
                        plan=NodePlan(
                            tokens=1 + index % 3, deadline_at=utcnow() + timedelta(days=2)
                        ),
                    )
                    if here and stop[1] == "ASSIGNED":
                        return
                    o, _ = await fresh(oid)
                    await cmd.accept(
                        actor=worker,
                        request_id=rid(),
                        order_id=oid,
                        node_id=node.id,
                        expected_version=o.version,
                    )
                    if here and stop[1] == "ACCEPTED":
                        return
                    o, _ = await fresh(oid)
                    # The last node's hand-in is the product (link required).
                    last = node_type is last_production_node(o.video_type)
                    await cmd.submit_work(
                        actor=worker,
                        request_id=rid(),
                        order_id=oid,
                        node_id=node.id,
                        expected_version=o.version,
                        link=f"https://drive.example.com/final/{index}.mp4"
                        if last
                        else f"https://drive.example.com/{node_type.value.lower()}/{index}",
                        script_text="Kịch bản: 0-3s hook, 3-15s vấn đề, 15-30s giải pháp."
                        if node_type is OrderNodeType.BIEN_TAP
                        else None,
                        note="Em gửi bản V1 ạ.",
                    )
                    o, nodes = await fresh(oid)
                    if nodes[node_type].status.value == "CHO_DUYET":
                        if here and stop[1] == "REVIEW":
                            return
                        if here and stop[1] == "RETURNED":
                            await cmd.return_node(
                                actor=lead,
                                request_id=rid(),
                                order_id=oid,
                                node_id=node.id,
                                expected_version=o.version,
                                note="Nhịp cắt chậm, thêm sub và nhạc nền giúp anh.",
                                plan=NodePlan(tokens=1),
                            )
                            return
                        await cmd.approve_node(
                            actor=lead,
                            request_id=rid(),
                            order_id=oid,
                            node_id=node.id,
                            expected_version=o.version,
                        )
                o, _ = await fresh(oid)
                if o.stage.value == "DUYET_VIDEO_BT":
                    # The unit has the script lead's video review on.
                    await cmd.approve_video(
                        actor=actors[LEADS[OrderNodeType.BIEN_TAP]],
                        request_id=rid(),
                        order_id=oid,
                        expected_version=o.version,
                    )
                if stop == "FINAL_REVIEW":
                    return
                o, _ = await fresh(oid)
                # The final review is the orderer's own.
                if stop == "FINAL_RETURNED":
                    await cmd.return_final(
                        actor=actors[owner],
                        request_id=rid(),
                        order_id=oid,
                        expected_version=o.version,
                        note="Logo cuối video bị mờ, xuất lại giúp chị.",
                    )
                    return
                if stop == "CANCELLED_LATE":
                    await cmd.cancel(
                        actor=head,
                        request_id=rid(),
                        order_id=oid,
                        expected_version=o.version,
                        reason="Sản phẩm ngừng chạy quảng cáo.",
                    )
                    return
                await cmd.approve_final(
                    actor=actors[owner],
                    request_id=rid(),
                    order_id=oid,
                    expected_version=o.version,
                    product_link=f"https://drive.example.com/final/{index}.mp4",
                )

            await run(oid, stop, index, owner)
            if age:
                past = utcnow() - timedelta(days=age, hours=index % 7)
                await session.execute(
                    update(Order).where(Order.id == oid).values(created_at=past, submitted_at=past)
                )
            await session.commit()
            o, _ = await fresh(oid)
            print(f"{o.code:<24} {o.stage.value:<16} {'★' if o.is_priority else ''}")
    await db.engine.dispose()


asyncio.run(main())
