# TasksBot

Phiên bản hiện tại: **0.6.0a3.post1**.

Hệ thống quản lý nội dung và sản xuất video cho phòng PR, kèm bot Telegram
và giao diện web, cùng mang tên TasksBot (trước đây là MeoBot / MeoChat).

## Kiến trúc

- Backend: FastAPI / SQLAlchemy / PostgreSQL / Redis / Celery
- Frontend: Next.js
- Bot: aiogram

## Cơ sở dữ liệu

Schema được quản lý bằng Alembic. Alembic head hiện tại là **`0053`**.
Có **53 migration** sẵn trong `alembic/versions/`. Deploy không tự chạy
migration: chạy `alembic upgrade head` bằng tay sau khi `pg_dump`, theo
`docs/handover/11_DEPLOYMENT_AND_OPERATIONS.md`.

## Worker và hàng đợi

Worker Celery chạy bốn hàng đợi: `q_default,q_integrations,q_reports,q_notifications`.

- `q_default`: việc trên cơ sở dữ liệu (chiếu KPI, việc lặp, nhắc việc).
- `q_integrations`: gọi dịch vụ ngoài (AI review, đồng bộ số liệu kênh).
- `q_reports`: báo cáo.
- `q_notifications`: gửi tin Telegram từ outbox.

Tin Telegram đi qua outbox và được giao **ít nhất một lần**: một tin có thể
được gửi lại nếu lần giao trước không xác nhận được, nên mọi tin đều có khoá
idempotency.

## Các module chính

- Content Workflow
- AI Review
- Work Management
- KPI
- Performance
- Permissions
- Telegram Bot

## Tài liệu

Bắt đầu từ `docs/handover/00_README.md`.

- Kiến trúc: `docs/handover/02_SYSTEM_ARCHITECTURE.md`
- Domain: `docs/handover/03_DOMAIN_MODEL.md`
- Cài đặt local: `docs/handover/09_LOCAL_DEVELOPMENT_SETUP.md`
- Kiểm thử: `docs/handover/10_TESTING_AND_QUALITY_GATES.md`
- Triển khai: `docs/handover/11_DEPLOYMENT_AND_OPERATIONS.md`
- Nợ kỹ thuật: `docs/handover/13_KNOWN_ISSUES_AND_TECH_DEBT.md`




Câu lệnh alembic upgrade head
cd /root/gtgtask

# 1. Backup DB trước (khuyên làm)
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > ~/dev-before-migrate-$(date +%F-%H%M).dump

# 2. Nâng DB lên migration mới nhất (0052)
docker compose exec -T api alembic upgrade head

# 3. Kiểm tra: phải ra 0052 (head)
docker compose exec -T api alembic current

# 4. Khởi động lại các service dùng DB
docker compose restart api worker bot beat