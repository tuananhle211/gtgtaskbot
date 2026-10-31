# Doanh thu Phòng Ads từ CRM (aicrmtech.vn) — plan

Chốt với người dùng ngày 29/10/2026. Migration `0083`.

## Mục tiêu

Thay phần "Nhập doanh thu" tay của Trưởng phòng bằng **thực thu** lấy từ CRM:

- **Số tiền = thực thu**: các khoản thanh toán (`/raw-data/order-payments`), không phải giá trị đơn hàng (đơn có thể còn nợ).
- **Ngày = ngày thanh toán** (`payment_date`): API lọc theo đúng trường này. Đơn tạo tháng 9 nhưng trả tiền tháng 10 tính vào tháng 10.
- **Hoàn tiền** (`payment_type` 7) CRM ghi số âm, nên cộng thẳng là ra thực thu đã trừ hoàn.
- Không tính **"Điểm"** (`payment_type` 3), vì đó không phải tiền thật. Các hình thức còn lại đều tính: tiền mặt, thẻ, chuyển khoản, trả góp, nhà xe thu hộ, hoàn tiền.

## Một nhân viên, nhiều tài khoản CRM

Mỗi id Marketing trên CRM là một **tài khoản CRM**, lưu ở bảng `ads_crm_accounts`. Mỗi tài khoản có:

- **Người APM** (`user_id`). Một người có thể giữ nhiều tài khoản; doanh thu của người đó là tổng các tài khoản.
- Hoặc **nguồn ◆** (`source`: Hotline, HN chuyển HCM, Google, Tiktok, Seeding, Chưa gán Marketing). Dùng cho các id là kênh, không phải người, ví dụ "Hotline FB".
- **Khu vực** HN / HCM, tự nhận từ chữ đầu tên tài khoản: `HN…` là HN; `SG…` hoặc `HCM…` là HCM; không có tiền tố thì để trống, chỉnh tay được. Khu vực phân biệt hai đội Marketing:
  - tài khoản **HCM** → cột **"Doanh số SG"** của người đó;
  - tài khoản **HN** → cột **"Doanh số HN"**, tính vào KPI của người đó nhưng không tính vào doanh thu cơ sở HCM (đúng nghĩa đang có).
  - Tài khoản chưa có khu vực được tính vào SG, và màn hình cảnh báo để chỉnh lại.
- Tài khoản không gắn ai và không gắn nguồn: **không tính**. Màn hình vẫn hiện tổng "chưa gán" để Trưởng phòng thấy.

Mapping ban đầu lấy từ file "Gắn mã" (Google Sheet người dùng gửi ngày 29/10). Màn "Tài khoản CRM" có nút **Nhập từ file Excel**, đọc các cột *CRM ID*, *Mã nhân viên APM*, *Hoặc tính vào nguồn ◆* (và *Khu vực* nếu có).

## Đồng bộ

- Tác vụ `ads.sync_crm_revenue`, chạy **mỗi giờ** (phút 10, từ 07:10 đến 23:10 giờ VN):
  - đọc danh sách user marketing (`/meta/users?department_key=marketing&include_inactive=1`);
  - đọc các khoản thanh toán từ **mùng 1 tháng này đến hôm nay**. Từ mùng 1 đến mùng 5, đọc từ **mùng 1 tháng trước**, vì CRM còn sửa sổ tháng cũ.
- Mỗi lần đồng bộ **thay toàn bộ** các khoản thanh toán trong khoảng đó, nên khoản bị xoá hoặc sửa trên CRM cũng được cập nhật. Chỉ lưu các trường cần cho báo cáo (`ads_crm_payments`): id, ngày, số tiền, hình thức, id marketing, chi nhánh, id đơn, khách mới/cũ, nguồn khách. **Không lưu tên, số điện thoại của khách.**
- Một id xuất hiện trong thanh toán mà không có trong danh sách marketing thì vẫn được tạo thành tài khoản CRM (đánh dấu "ngoài danh sách").
- "Kéo lại khoảng ngày" (`since` / `until`) chạy được từ màn Tác vụ định kỳ hoặc từ màn Tài khoản CRM.
- Key chỉ gửi qua header `x-api-key` và chỉ từ backend. Key đặt trong env `CRM_API_KEY`, địa chỉ trong `CRM_API_BASE_URL` (mặc định `https://aicrmtech.vn/api/internal`). Thiếu key thì tác vụ báo "chưa cấu hình" và không gọi CRM.
- CRM giới hạn 60 request/phút. Một tháng khoảng 1.400 khoản, tức 3 trang 500 dòng, nên không chạm giới hạn. Nếu CRM trả 429 thì đợi rồi hỏi lại.

## Gộp vào doanh thu đang có

Mọi báo cáo (Dashboard Ads, Tổng team, KPI, Báo cáo tháng/ngày) đều đọc doanh thu qua `AdsRevenueService.rows()`, nên CRM chỉ cần cắm vào đúng chỗ này:

- Trong **khoảng ngày CRM đã đồng bộ**, mỗi người hoặc nguồn ◆ có ít nhất một tài khoản CRM lấy số từ CRM: SG = tổng tài khoản HCM, HN = tổng tài khoản HN. Số nhập tay của dòng đó trong khoảng này bị bỏ qua.
- Người hoặc nguồn chưa gắn tài khoản CRM, hoặc ngày ngoài khoảng đồng bộ, vẫn dùng số nhập tay như cũ.
- Trang "Nhập doanh thu": dòng lấy từ CRM hiện số CRM, khoá ô và gắn nhãn "CRM". Gửi lưu một ô CRM sẽ bị từ chối (422 `ads_revenue_from_crm`).

## Bốn luồng khách và bộ lọc (30/10)

Mỗi khoản thu có hai miền:
- **Đội MKT**: miền của tài khoản CRM (HN / HCM; chưa có miền thì tính là HCM);
- **Cơ sở làm**: chi nhánh của khoản thanh toán (HN1, HN2 → HN; SG → HCM). Chi nhánh trên khoản thanh toán và trên đơn hàng chỉ lệch giữa HN1 và HN2, nên cùng miền.

Ghép lại ra 4 luồng: **SG** (MKT HCM, làm HCM), **HN** (MKT HN, làm HN), **HN chuyển SG** (MKT HN, làm HCM), **SG chuyển HN** (MKT HCM, làm HN). "Doanh số SG / HN" của một người là theo cơ sở làm.

- **Không còn bộ lọc Miền** (bỏ ngày 30/10). Dưới ô "Doanh thu tổng" có hai ô cùng tổng: **Doanh thu Marketing** (HN = HN + HN chuyển SG; SG = SG + SG chuyển HN) và **Doanh thu Cơ sở** (HN = HN + SG chuyển HN; SG = SG + HN chuyển SG).
- **Hiệu suất theo miền** = chi phí quảng cáo của các tài khoản thuộc BM miền đó (BM Hà Nội / BM Sài Gòn; hoá đơn, chưa có thì chi tiêu) ÷ Doanh thu Marketing miền đó không VAT.
- **BM, Pháp nhân, Cơ sở** (thuộc tính tài khoản quảng cáo) chỉ lọc chi phí, hoá đơn, SĐT; doanh thu giữ nguyên.

## Báo cáo ngày: mã khách để đối soát (31/10, migration `0086`)

- Mỗi lần đồng bộ lưu thêm **mã khách** (`customer.account_code`, ví dụ KH000123) của từng khoản thu. Chỉ mã, vẫn không lưu tên hay SĐT. Khoản thu đồng bộ trước đó chưa có mã cho đến lần đồng bộ lại ngày đó.
- Trong "Báo cáo ngày", bấm số doanh thu của một ngày → cửa sổ liệt kê từng mã khách và số tiền khách đã thanh toán trong ngày (gộp theo mã; hoàn tiền là số âm; không tính điểm), dòng "Nhập tay" nếu ngày đó có số nhập tay, và tổng bằng đúng doanh thu của ngày.
- Lọc giống bảng: thành viên chỉ thấy khách của mình; chọn BM thì chỉ còn doanh thu Marketing của miền đó.

## Quyền

- Màn "Tài khoản CRM", sửa mapping, nhập file, bấm đồng bộ: Trưởng phòng ADS, OWNER, ADMIN (`edit_revenue`).
- Người khác không thấy màn này. Nhân viên vẫn chỉ thấy doanh thu của mình trên dashboard như hiện nay.

## Chưa làm (đợt sau)

- Khách mới / cũ (`is_upsale`), khách đến cửa (lịch hẹn), bill trung bình: dữ liệu đã lưu một phần, các thẻ "Chưa có dữ liệu" sẽ lấp sau.
- Marketing trên khoản thu là người **đang** phụ trách khách lúc gọi API, chưa chắc là người bán. Đã đề nghị bên CRM thêm marketing của đơn.
