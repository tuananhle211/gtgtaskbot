# Đội agent trong APM – kế hoạch đợt 1 (chat chữ)

Trạng thái: **đợt 1 đã code (05/11/2026), chưa commit** - chủ sở hữu chốt: đường hầm SSH (chưa dùng tên miền), bảng quyền mục 4, tắt `terminal/file/code_execution` cho agent dùng qua APM (chưa làm phía VPS), lịch sử không tự xoá · Lập 05/11/2026 · Người đọc: chủ sở hữu, người code APM.

## 0. Đã làm (đợt 1)

- **VPS**: relay `/opt/apexmed/agent-relay.py` (chỉ thư viện chuẩn, root sở hữu), chạy dưới user `hermes` bằng systemd user `apm-agent-relay`, nghe `127.0.0.1:8650`; token ở `/home/hermes/.config/apm-agent-relay/token` (600). Đã thử: token sai 401, đủ 15 agent, chữ về từng mảnh.
- **Backend**: migration **0091** (3 bảng + seed 15 agent theo mã phòng), module quyền `AGENTS` (`CHAT`, `MANAGE` owner_only), `application/agents/{access,service,turns}.py`, `integrations/hermes/relay.py`, router `/api/agents`, cờ `/api/units/me.agents`. Câu trả lời chạy thành **tác vụ nền** trong API (đóng tab không mất câu trả lời; "Dừng" huỷ), lưu bằng transaction riêng.
- **Frontend**: `/agents` (3 cột kiểu Telegram, điện thoại từng cột), `/admin/agents` (OWNER), mục menu "Đội agent", markdown an toàn tự viết (`components/agent-markdown.tsx`).
- **Đã kiểm thực tế**: qua Next (cổng 8811) câu trả lời về 86 mảnh trong 9 giây - Next **không** gom luồng (spike mục 6.1 xong).
- Test: `tests/unit/test_agent_chat.py` (12), `tests/integration/test_agent_chat_migration.py` (4, gồm so model ↔ migration), `frontend/tests/agents-chat.test.tsx` (6).

**Prod (05/11)**: khoá `secrets/agent_tunnel_key` đã sinh trên prod; user `apmtunnel` trên VPS agent chỉ mở được `127.0.0.1:8650`; container `agent-tunnel` (docker-compose, profile `tunnel`) đã thử chạy tạm trên prod: API prod gọi relay `{"ok": true}`. Còn: hai dòng `HERMES_RELAY_URL=http://agent-tunnel:8650` / `HERMES_RELAY_TOKEN` trong `.env` prod (chủ sở hữu tự thêm), rồi commit + deploy.

**Bật trên một máy local**: giữ đường hầm `ssh -N -L 8650:127.0.0.1:8650 secondbrain` trên máy chạy docker, thêm vào `.env`: `HERMES_RELAY_URL=http://host.docker.internal:8650` và `HERMES_RELAY_TOKEN=<token relay>`, rồi `docker compose up -d api` và `alembic upgrade head`.

## 1. Mục tiêu

Nhân viên Apexmed chat với các agent Hermes (Chủ tịch, CEO, CMO, Marketing Ads, Pháp chế…) **ngay trong APM**, giống chat Telegram: chọn agent, gõ câu hỏi, chữ hiện dần, lịch sử được lưu, ai được chat agent nào theo phòng ban.

**Đợt 1 chỉ có chữ.** Ngoài phạm vi:
- Đợt 2 – ảnh: gửi ảnh thiết kế cho agent, nhận ảnh agent trả. **Chỉ ảnh thiết kế, không bao giờ ảnh khách hàng.**
- Đợt 3 – quản trị: trạng thái agent, duyệt lệnh nguy hiểm trên web, Kanban, lịch chạy, chi phí theo agent.

## 2. Hiện trạng phía Hermes (đã kiểm tra 05/11/2026)

- VPS `43.240.117.204` (alias `secondbrain`), Hermes v0.22.7, user `hermes`, 15 agent (profile):
  `chutich ceo cco cfo cmo mktads mktnghiencuu mktnoidung mktthietke mktdung telesale cskh chatpage nhansu phapche`.
- Một gateway chạy nền (systemd user `hermes-gateway`) phục vụ cả 15 agent. **API server bật**, chỉ nghe `127.0.0.1:8642`.
- Mỗi agent một đường riêng `/p/<agent>/v1/...` và **một key riêng** (`API_SERVER_KEY` trong `.env` của agent). Key agent này gọi agent kia → 401.
- Các endpoint dùng ở đợt 1:
  - `POST /p/<agent>/v1/chat/completions` – `stream: true` trả SSE (`chat.completion.chunk`, sự kiện `hermes.tool.progress` khi agent đang dùng công cụ, dòng `: keepalive` mỗi 10 giây).
  - Header `X-Hermes-Session-Id` – giữ mạch hội thoại phía Hermes; `X-Hermes-Session-Key` – phạm vi trí nhớ dài hạn.
  - `Idempotency-Key` – gửi lại không chạy hai lần.
- Model qua 9router (tài khoản thuê bao Claude + Codex). Đã thử chat thật với `phapche`: trả lời đúng vai.

## 3. Kiến trúc

```
Trình duyệt ──► Next (rewrite /api/agents/*) ──► API APM (FastAPI)
                                                   │ kiểm quyền, lưu hội thoại + tin nhắn, audit
                                                   ▼
                                     Cổng agent trên VPS (HTTPS, chỉ IP APM, 1 token)
                                                   │ gắn đúng API_SERVER_KEY của agent
                                                   ▼
                                     Hermes 127.0.0.1:8642 /p/<agent>/v1
```

**Trình duyệt không bao giờ gọi thẳng Hermes.** APM không giữ 15 key của Hermes.

### 3.1 Cổng agent (relay) trên VPS – đề xuất
Một dịch vụ nhỏ (Python, chạy dưới user `hermes`, systemd) đọc `API_SERVER_KEY` của từng profile lúc khởi động, nhận `POST /agents/<slug>/chat` từ APM và chuyển tiếp sang `/p/<slug>/v1/chat/completions` kèm đúng key, **truyền nguyên luồng SSE**.
- Ra Internet qua nginx đã có: **HTTPS** (cần một tên miền con, vd `agents.<tên-miền>`, chứng chỉ Let's Encrypt), **chỉ cho IP APM prod `103.75.184.169`** (+ IP dev nếu cần), header `Authorization: Bearer <RELAY_TOKEN>`.
- Chỉ mở đúng các đường đợt 1 cần (chat, hủy). Không mở `/api/sessions` xoá, không mở cấu hình.
- Key Hermes **không rời VPS**. APM chỉ giữ `HERMES_RELAY_URL` + `HERMES_RELAY_TOKEN` (biến môi trường, như `CRM_API_KEY`).
- Máy dev: `ssh -L 8650:127.0.0.1:8650 secondbrain`, `HERMES_RELAY_URL=http://host.docker.internal:8650`.

Phương án thay thế: đường hầm SSH thường trực từ máy prod (không cần tên miền, nhưng thêm một thứ phải canh trên prod).

## 4. Phân quyền

Module mới trong `domain/org/modules.py`: **`AGENTS` "Đội agent"**.

| Quyền | Phạm vi | Nghĩa |
|---|---|---|
| `CHAT` | NONE / ALL | **Chat với mọi agent** (bất kể danh sách). Cấp theo phòng ban hoặc cá nhân. |
| `MANAGE` | ALL, `owner_only` | Sửa danh sách agent, ai chat được agent nào. |

**Agent nào thì do danh sách trên chính agent đó quyết định**: mỗi agent có `everyone`, `allowed_department_ids`, `allowed_user_ids`. Ai thuộc một phòng trong danh sách (ghế đang mở), có tên trong danh sách, hoặc agent mở cho mọi người, thì thấy agent đó. OWNER / ADMIN thấy hết; chỉ OWNER quản lý.

> Khi code (05/11) đổi nhẹ so với bản duyệt: `CHAT` nghĩa là "mọi agent" thay vì "được vào màn", để không phải cấu hình hai nơi - danh sách trên agent đã đủ để mở cho từng phòng / từng người.

Mặc định gợi ý (sửa được trên màn quản trị):
| Agent | Phòng ban được chat |
|---|---|
| chutich, ceo | Ban Giám đốc |
| cfo | Ban Giám đốc, Tài chính - Kế toán |
| cmo, mktnghiencuu | Ban Giám đốc, Phòng Ads, Biên tập, Thiết kế, Dựng, PR |
| mktads | Ban Giám đốc, Phòng Ads |
| mktnoidung | Ban Giám đốc, Biên tập, PR |
| mktthietke | Ban Giám đốc, Thiết kế |
| mktdung | Ban Giám đốc, Dựng |
| cco, telesale, cskh, chatpage | Ban Giám đốc, phòng Kinh doanh / CSKH (khi có trong APM) |
| nhansu | Ban Giám đốc, phòng Nhân sự (khi có) |
| phapche | mọi phòng ban có `CHAT` (ai cũng có thể cần hỏi pháp chế) |

Mỗi người chỉ thấy **hội thoại của chính mình**. Đợt 1 không có màn xem hội thoại người khác.

## 5. Dữ liệu (migration 0091)

`agent_profiles` – danh bạ agent trong APM
`id` · `slug` (unique, = tên profile Hermes) · `name` · `title` · `description` · `badge` (chữ viết tắt hiển thị) · `sort` · `active` · `allowed_department_ids` JSON · `allowed_user_ids` JSON · timestamps. Seed 15 agent.

`agent_conversations`
`id` · `agent_id` FK · `user_id` FK · `title` (tự đặt từ câu đầu, sửa được) · `hermes_session_id` (unique, `apm-<uuid>`) · `last_message_at` · `archived_at` · timestamps. Index (`user_id`, `agent_id`, `last_message_at`).

`agent_messages`
`id` · `conversation_id` FK CASCADE · `role` USER / ASSISTANT · `content` Text · `status` DONE / STREAMING / FAILED / CANCELLED · `error_code` · `tools_used` JSON (tên công cụ từ `hermes.tool.progress`, để hiển thị) · `duration_ms` · `created_at`.

Không lưu key, không lưu token. Nội dung tin nhắn là dữ liệu nội bộ: không chép sang Telegram, không đưa vào log.

## 6. API (`api/routers/agents.py`, rewrite `/api/agents/:path*` trong `next.config.mjs`)

| Method | Đường | Việc |
|---|---|---|
| GET | `/api/agents` | Agent người này chat được (+ `configured` = relay đã cấu hình) |
| GET | `/api/agents/{slug}/conversations` | Hội thoại của mình với agent đó |
| POST | `/api/agents/{slug}/conversations` | Tạo hội thoại mới |
| GET | `/api/agents/conversations/{id}/messages` | Tin nhắn (phân trang ngược) |
| POST | `/api/agents/conversations/{id}/messages` | Gửi tin → **SSE**: `delta`, `tool`, `done`, `error` |
| POST | `/api/agents/conversations/{id}/stop` | Dừng câu trả lời đang chạy |
| PATCH / DELETE | `/api/agents/conversations/{id}` | Đổi tên / lưu trữ |
| GET / PUT | `/api/agents/admin` | (MANAGE) danh bạ + danh sách quyền từng agent |

Lỗi: 403 `agents_forbidden`, 404 `agent_not_found` / `conversation_not_found` (hội thoại người khác cũng 404), 409 `agent_busy` (đang trả lời câu trước), 422 `agent_message_invalid` (rỗng / > 8.000 ký tự), 429 `agent_rate_limited`, 503 `agents_not_configured` / `agent_unreachable`.

### 6.1 Gửi tin – luồng xử lý
1. Kiểm quyền + hội thoại thuộc người gọi + không có câu trả lời đang chạy.
2. Lưu tin USER, tạo tin ASSISTANT `STREAMING`.
3. Gọi relay, `stream: true`, header `X-Hermes-Session-Id = hermes_session_id`, `X-Hermes-Session-Key = apm:<user_id>:<slug>`, `Idempotency-Key = <id tin USER>`.
   Câu đầu của mỗi hội thoại kèm một dòng ngữ cảnh: *"Người hỏi: <tên>, <phòng ban – chức vụ>, qua APM."* để agent biết đang nói với ai.
4. Chuyển tiếp từng mảnh chữ cho trình duyệt; `hermes.tool.progress` → sự kiện `tool` ("Đang tra cứu…").
5. Xong → lưu `content`, `DONE`. Lỗi / mất kết nối / dừng → lưu phần đã có, `FAILED` / `CANCELLED`.

Giới hạn: 20 tin / 5 phút / người; 1 câu đang chạy / hội thoại; câu trả lời tối đa 5 phút.

**Cần thử ngay đầu đợt (spike):** Next rewrite có truyền SSE từng mảnh hay gom lại (`proxyTimeout` 120 s hiện tại sẽ cắt câu trả lời dài). Nếu gom → trình duyệt gọi một route stream riêng không qua rewrite, hoặc chuyển sang `/v1/runs` + `GET /v1/runs/{id}/events`.

## 7. Giao diện (`/agents`, tải skill `apexmed-ui` trước khi làm)

- Nav mới **"Đội agent"** (cho người có `CHAT` hoặc OWNER/ADMIN).
- Ba cột kiểu Telegram (điện thoại: từng cột một):
  1. **Danh sách agent** theo nhóm (Ban Giám đốc / Marketing / Kinh doanh – CSKH / Hỗ trợ), huy hiệu chữ viết tắt, chức danh, dòng tin cuối.
  2. **Hội thoại** với agent đang chọn: "+ Cuộc trò chuyện mới", đổi tên, lưu trữ.
  3. **Khung chat**: bong bóng trái/phải, markdown (bảng, danh sách, code), chữ hiện dần, dòng "Đang tra cứu…", nút **Dừng**, **Sao chép**, gửi bằng Enter (Shift+Enter xuống dòng), giờ `dd/mm HH:mm`.
- Dòng nhắc cố định dưới ô nhập: *"Không gửi thông tin cá nhân, ảnh, hồ sơ của khách hàng."*
- Quản trị: tab "Đội agent" trong **Quản trị** (OWNER): bật/tắt agent, chọn phòng ban / người được chat.

## 8. An toàn

- Nội dung chat đi tới nhà cung cấp model (Anthropic / OpenAI qua 9router, tài khoản thuê bao). **Không gửi dữ liệu khách hàng** – nhắc trên màn hình và trong SOUL của agent.
- Agent có công cụ `terminal` / `file` mà chạy qua API thì không có ai bấm duyệt lệnh nguy hiểm tại chỗ (Hermes tự từ chối sau 5 phút). **Khuyến nghị tắt `terminal`, `file`, `code_execution` cho mọi agent dùng qua APM** – cũng là điều kiện để phân vùng Second Brain thành chặn thật.
- Audit: `agent.conversation.created`, `agent.message.sent` (không ghi nội dung), `agent.access.updated`.
- Relay: chỉ IP APM, HTTPS, token xoay được; log relay không ghi nội dung.

## 9. Kiểm thử

- Backend `tests/unit/test_agent_chat.py`: quyền (phòng ban / cá nhân / OWNER / không có CHAT), hội thoại người khác = 404, giới hạn tốc độ, một câu chạy một lúc, đọc SSE giả (mảnh chữ, `hermes.tool.progress`, keepalive, lỗi giữa chừng → lưu phần đã có), relay chưa cấu hình → 503.
- `tests/integration/test_agent_chat_migration.py`: 0091 lên / xuống, seed 15 agent.
- Frontend `tests/agents-chat.test.tsx`: danh sách theo quyền, gửi + chữ hiện dần, Dừng, lỗi, nav, dòng nhắc dữ liệu khách; cập nhật các test quét mã nguồn (nav, confirm).

## 10. Thứ tự làm

1. Spike: SSE qua Next rewrite + relay thử trên VPS (đường hầm SSH từ máy dev).
2. Relay trên VPS (systemd, nginx HTTPS + IP allowlist) – cần tên miền.
3. Backend: migration 0091, module quyền, service, router, test.
4. Frontend: `/agents`, quản trị, test.
5. Dev → `dev` VPS → prod (thêm `HERMES_RELAY_URL/TOKEN` vào `.env` prod, mở IP prod trên relay).

## 11. Cần chủ sở hữu chốt

1. **Tên miền con** cho cổng agent (vd `agents.apexmed.vn`) – hay dùng đường hầm SSH.
2. **Bảng "ai chat agent nào"** ở mục 4 – sửa gì trước khi seed.
3. **Tắt `terminal` / `file` / `code_execution`** cho các agent dùng qua APM (khuyến nghị: có).
4. **Giữ lịch sử bao lâu** (đề xuất: không tự xoá, người dùng tự lưu trữ; OWNER xoá được).
