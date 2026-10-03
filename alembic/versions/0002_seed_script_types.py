"""Seed the five initial script types with version 1 of their rubrics.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-28

The rubrics are deliberately simple - they are a starting point for the team to
refine. What matters structurally is that they are versioned: editing a rubric
later must create version 2 rather than rewrite this data, so past AI reviews
stay explainable.

Ids are derived with ``uuid5`` from a fixed namespace, which makes the seed
idempotent across environments and lets the downgrade target exactly these rows.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Stable namespace so seeded ids are identical on every deployment.
SEED_NAMESPACE = uuid.UUID("6f1d9a3e-2c4b-4f5a-9d61-2f0b7c8a1e40")


def _criterion(code: str, name: str, weight: int, guidance: str) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "weight": weight,
        "description": None,
        "guidance": guidance,
    }


SEED_DATA: tuple[dict[str, Any], ...] = (
    {
        "code": "doctor_education",
        "name": "Bác sĩ chia sẻ kiến thức",
        "description": "Nội dung giáo dục sức khoẻ do bác sĩ trình bày.",
        "configuration": {"target_duration_seconds": 90, "tone": "chuyên môn, gần gũi"},
        "rubric": {
            "passing_score": 75,
            "notes": "Ưu tiên độ chính xác y khoa hơn mức độ giật tít.",
            "criteria": [
                _criterion("medical_accuracy", "Độ chính xác y khoa", 30, "Không phóng đại hiệu quả điều trị."),
                _criterion("clarity", "Dễ hiểu với người không chuyên", 20, "Giải thích thuật ngữ khi dùng."),
                _criterion("hook_strength", "Sức hút 3 giây đầu", 20, "Nêu vấn đề người xem đang gặp."),
                _criterion("credibility", "Tính tin cậy", 15, "Có dẫn nguồn hoặc kinh nghiệm lâm sàng."),
                _criterion("call_to_action", "Kêu gọi hành động", 15, "CTA an toàn, không hứa hẹn kết quả."),
            ],
        },
    },
    {
        "code": "emotional_story",
        "name": "Câu chuyện cảm xúc",
        "description": "Kể chuyện thật hoặc dựa trên chuyện thật, khai thác cảm xúc.",
        "configuration": {"target_duration_seconds": 120, "tone": "tâm tình"},
        "rubric": {
            "passing_score": 70,
            "criteria": [
                _criterion("emotional_arc", "Diễn biến cảm xúc", 30, "Có cao trào và giải toả rõ ràng."),
                _criterion("relatability", "Tính đồng cảm", 25, "Người xem thấy mình trong câu chuyện."),
                _criterion("hook_strength", "Sức hút mở đầu", 20, "Mở bằng chi tiết gây tò mò."),
                _criterion("pacing", "Nhịp kể", 15, "Không lê thê ở đoạn giữa."),
                _criterion("ending_impact", "Sức nặng kết thúc", 10, "Kết để lại một câu hỏi hoặc thông điệp."),
            ],
        },
    },
    {
        "code": "short_drama",
        "name": "Phim ngắn / tiểu phẩm",
        "description": "Kịch bản có nhân vật, xung đột và lời thoại.",
        "configuration": {"target_duration_seconds": 180, "tone": "kịch tính"},
        "rubric": {
            "passing_score": 70,
            "criteria": [
                _criterion("plot_clarity", "Mạch truyện rõ ràng", 25, "Người xem theo được không cần tua lại."),
                _criterion("character_conflict", "Xung đột nhân vật", 25, "Động cơ của mỗi nhân vật phải rõ."),
                _criterion("hook_strength", "Sức hút mở đầu", 20, "Đặt tình huống ngay câu thoại đầu."),
                _criterion("dialogue_quality", "Chất lượng thoại", 20, "Thoại tự nhiên, không giải thích lộ liễu."),
                _criterion("cliffhanger", "Kết mở / gây tò mò", 10, "Tạo lý do xem phần sau."),
            ],
        },
    },
    {
        "code": "viral_discussion",
        "name": "Chủ đề tranh luận",
        "description": "Nội dung khơi gợi bình luận và chia sẻ.",
        "configuration": {"target_duration_seconds": 60, "tone": "thẳng thắn"},
        "rubric": {
            "passing_score": 70,
            "notes": "Tiêu chí an toàn nội dung không được đánh đổi lấy lượt tương tác.",
            "criteria": [
                _criterion("hook_strength", "Sức hút mở đầu", 25, "Nêu quan điểm gây chú ý ngay."),
                _criterion("controversy_balance", "Cân bằng tranh luận", 25, "Trình bày ít nhất hai góc nhìn."),
                _criterion("comment_bait", "Khả năng tạo bình luận", 20, "Kết bằng câu hỏi mở."),
                _criterion("clarity", "Rõ ràng", 15, "Một video một luận điểm."),
                _criterion("safety", "An toàn nội dung", 15, "Không công kích cá nhân, không sai sự thật."),
            ],
        },
    },
    {
        "code": "service_ad",
        "name": "Quảng cáo dịch vụ",
        "description": "Nội dung giới thiệu dịch vụ, có mục tiêu chuyển đổi.",
        "configuration": {"target_duration_seconds": 45, "tone": "tin cậy"},
        "rubric": {
            "passing_score": 75,
            "criteria": [
                _criterion("offer_clarity", "Rõ ràng về dịch vụ", 30, "Nói rõ dịch vụ, cho ai, giá trị gì."),
                _criterion("trust_signal", "Tín hiệu tin cậy", 25, "Chứng nhận, review thật, số liệu kiểm chứng được."),
                _criterion("hook_strength", "Sức hút mở đầu", 20, "Chạm vào vấn đề của khách hàng."),
                _criterion("call_to_action", "Kêu gọi hành động", 15, "CTA cụ thể, một bước duy nhất."),
                _criterion("compliance", "Tuân thủ quảng cáo", 10, "Không cam kết kết quả y khoa."),
            ],
        },
    },
)

script_types_table = sa.table(
    "script_types",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("active", sa.Boolean()),
    sa.column("current_version", sa.Integer()),
)

#: JSON payloads are bound as text and cast in SQL rather than passed as dicts
#: through a JSONB column. Dict binds cannot be rendered by
#: ``alembic upgrade head --sql`` (offline mode), and being able to review the
#: exact SQL before touching production data is worth the explicit CAST.
INSERT_VERSION_SQL = sa.text(
    """
    INSERT INTO script_type_versions
        (id, script_type_id, version, configuration, review_rubric, prompt_template)
    VALUES
        (:id, :script_type_id, :version,
         CAST(:configuration AS JSONB), CAST(:review_rubric AS JSONB), :prompt_template)
    """
)


def _script_type_id(code: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"script_type:{code}")


def _version_id(code: str, version: int) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"script_type_version:{code}:{version}")


def upgrade() -> None:
    op.bulk_insert(
        script_types_table,
        [
            {
                "id": _script_type_id(item["code"]),
                "code": item["code"],
                "name": item["name"],
                "description": item["description"],
                "active": True,
                "current_version": 1,
            }
            for item in SEED_DATA
        ],
    )
    for item in SEED_DATA:
        op.execute(
            INSERT_VERSION_SQL.bindparams(
                sa.bindparam("id", _version_id(item["code"], 1), type_=sa.Uuid()),
                sa.bindparam(
                    "script_type_id", _script_type_id(item["code"]), type_=sa.Uuid()
                ),
                sa.bindparam("version", 1, type_=sa.Integer()),
                sa.bindparam(
                    "configuration",
                    json.dumps(item["configuration"], ensure_ascii=False),
                    type_=sa.Text(),
                ),
                sa.bindparam(
                    "review_rubric",
                    json.dumps(item["rubric"], ensure_ascii=False),
                    type_=sa.Text(),
                ),
                sa.bindparam("prompt_template", None, type_=sa.Text()),
            )
        )


def downgrade() -> None:
    codes = [item["code"] for item in SEED_DATA]
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM script_type_versions WHERE script_type_id IN "
            "(SELECT id FROM script_types WHERE code = ANY(:codes))"
        ),
        {"codes": codes},
    )
    connection.execute(
        sa.text("DELETE FROM script_types WHERE code = ANY(:codes)"),
        {"codes": codes},
    )
