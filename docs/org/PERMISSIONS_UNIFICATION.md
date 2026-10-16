# Gom phân quyền về một mô hình (0066)

**Trạng thái: ĐÃ CODE (16/10/2026), chưa commit.** Tiếp nối
[ORG_AND_PERMISSIONS_PLAN.md](ORG_AND_PERMISSIONS_PLAN.md) (0061) và module Tài nguyên (0065).

## Vì sao

Trước 0066 app có ba cách nói "ai được làm gì":

| Module | Cách cũ |
|---|---|
| Order (ORD) | grants phòng ban (0061) **+** "cột Admin" trong Thiết lập ORD |
| PR | vai trò hệ thống (`TEAM_LEAD`/`EMPLOYEE` → capability) **+** quyền duyệt cấp thêm theo kênh/loại nội dung, chỉ OWNER cấp được |
| Tài nguyên | grants phòng ban (0065) |

Ba màn hình, ba resolver, và trưởng phòng PR không tự xếp quyền cho người của mình được.

## Mô hình mới: ba lớp, một resolver

1. **Vai trò hệ thống**: OWNER và ADMIN có mọi quyền ở mọi module. Cột Admin ORD bị bỏ (dữ liệu
   `settings.permissions.ADMIN` còn trong DB nhưng không đọc nữa).
2. **Phòng ban** (`department_permissions`): module × quyền × chức vụ → Không / Của mình / Toàn bộ.
   Ba module: `ORDER`, `PR`, `RESOURCES`. Registry: `domain/org/modules.py` (`MODULES`).
3. **Cá nhân** (`user_permissions`, bảng mới): một người, một quyền, một phạm vi, ai cấp, đến ngày
   nào, thu hồi lúc nào. Không xoá dòng.

Quyền hiệu lực = hợp của ba lớp, tính một lần mỗi request:
`application/org/access.py` → `Access` (`load_access`, `AccessCache`). Mọi module đọc từ đó:

- ORD: `UnitMembership.grants` (seat + cá nhân) → `AdsPermissions.from_grants`; ADMIN → `full()`.
- Tài nguyên: `resource_access()` đọc `membership.access`.
- PR: `PrCapabilityService.held()` cho 18 capability không có phạm vi; 3 cổng duyệt
  (`GRANT_BACKED`) vẫn ở `pr_user_capabilities` với phạm vi kênh / loại nội dung.

## Quyền chung `MANAGE_PERMISSIONS`

Mọi module có thêm ô "Cấp / thu hồi quyền cho người khác":

- `ALL`: cấp cho bất kỳ ai dưới OWNER/ADMIN;
- `OWN`: cấp cho **nhân viên** (không phải trưởng phòng) của phòng mình làm trưởng phòng.

Quy tắc chống leo thang (`Access.manages`, `PersonalPermissionService._grantable`):

- không cấp cho chính mình, không cho OWNER/ADMIN;
- không cấp được `MANAGE_PERMISSIONS` qua lớp cá nhân;
- người cấp dưới OWNER/ADMIN chỉ cấp quyền mình đang có, phạm vi không rộng hơn mình;
- cổng duyệt PR cấp ở màn PR (`/api/pr/capabilities/grant`), cùng câu hỏi `manages`.

Mặc định: Trưởng phòng PR Thương Hiệu có `MANAGE_PERMISSIONS: OWN` trong module PR.

## PR: không ai mất quyền

Migration 0066 cấp cho phòng có chức năng PR: HEAD = đúng tập capability `TEAM_LEAD` từng có qua
ma trận vai trò, MEMBER = tập của `EMPLOYEE`; phòng Ban Giám đốc = tập `EMPLOYEE` cho cả hai
chức vụ. Test `test_unified_permissions.test_01` giữ hai tập này khớp `role_capabilities`.
Grant cũ `requires_role_baseline=true` giờ hỏi: OWNER/ADMIN hoặc đang là trưởng phòng PR
(`PrCapabilityService.legacy_baseline`). Một `TEAM_LEAD` được xếp vào PR tự thành trưởng phòng
(`departments.place`, `tests/unit/streams.tag_pr`).

## API và màn hình

- `GET /api/permissions/people/{id}`: mọi module, mỗi quyền phạm vi đang có + nguồn
  (`DEPARTMENT` seat / `PERSONAL`), `can_manage` và `grantable` cho người xem.
- `POST /api/permissions/people/{id}/grants`, `DELETE .../grants/{grant_id}`.
- `GET/PUT /api/departments/{id}/grants?module=` cho cả ba module; catalog `modules[]` có
  `personal_only` cho cổng duyệt PR.
- `/api/pr/roles` thêm `positions` (quyền theo chức vụ PR).
- Màn: Quản trị › Phòng ban vẽ ba bảng quyền; `/admin/people/[id]` "Quyền" của một người (link
  từ Phòng ban và Tài khoản › Thành viên); Thiết lập ORD không còn cột Admin; tab "Vai trò &
  quyền" của PR hiện quyền theo chức vụ.

## Test giữ xanh

`tests/unit/test_unified_permissions.py`, `test_pr_scoped_approval_grants.py`,
`test_pr_bulk_approval.py`, `test_pr_membership_admin.py`, `test_departments.py`,
`test_resources.py`, `test_ads_permissions.py`; frontend `tests/permissions.test.tsx`,
`units-screens.test.tsx`, `membership.test.tsx`.

## 0067: trưởng phòng PR giữ danh sách thành viên

Hai capability mới trong module PR, cấp mặc định cho Trưởng phòng PR Thương Hiệu:
`PR_MEMBERS_VIEW` (xem tab Thành viên, Vai trò & quyền, quyền hiệu lực của người khác) và
`PR_MEMBERS_MANAGE` (thêm tài khoản mới - chỉ vai trò Nhân viên - hoặc đưa tài khoản có sẵn vào
PR; người mới tự được xếp vào phòng PR). Phần hệ thống (đổi vai trò, vô hiệu hoá, loại khỏi PR)
vẫn của OWNER/ADMIN.

## 0067 (tiếp): bot Telegram cho trưởng phòng PR

Bot phục vụ PR, nên Trưởng phòng PR Thương Hiệu có **toàn bộ bot** như Chủ sở hữu (chat AI,
tool, menu đầy đủ trong chat riêng). Mỗi update, `ActorMiddleware` nạp quyền thống nhất vào
`Actor` (`application/org/bot_access.with_unified_access`): `full_bot` = OWNER hoặc trưởng phòng
PR; `extra_permissions` = permission nền của các capability PR người đó giữ (qua phòng ban hoặc cấp
riêng), trừ nhóm quyền hệ thống (`user.manage`…). Cổng tool (`ToolRegistry.visible_to`,
`PolicyEngine`, `CapabilityService`) đọc `actor_has_permission` = ma trận vai trò ∪
`extra_permissions`. Tool cấp/thu hồi quyền duyệt hạ gác xuống `script.review` và để
`PrCapabilityService.manages` quyết định. Quản trị viên vẫn dùng bot cơ bản như trước.
