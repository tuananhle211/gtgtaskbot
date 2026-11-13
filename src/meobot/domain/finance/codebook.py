"""The budget codes ("Mã ngân sách") and their approval matrix, from the Sổ tay
(Google Sheet "Sổ tay", tabs "Mã ngân sách" + "Phân quyền", read 13/11/2026).

One line per code: ``code | name | kind | purchase proposer | purchase approvals
(Trưởng phòng, GĐ khối, Phòng Tài chính, PTGĐ/CEO as 1/0) | purchase executor
| payment proposer | payment approvals (Trưởng phòng, GĐ khối) | the accountant
| Kế toán trưởng, PTGĐ/CEO | Quỹ cơ sở, Thủ quỹ VP | documents (8 x 1/0:
Giấy đề nghị mua sắm, 3 báo giá, Quyết định BGĐ, Hợp đồng, Giấy đề nghị thanh
toán, Biên bản bàn giao / Phiếu xuất kho, Biên bản nghiệm thu, Hoá đơn) |
other papers (note)``. A heading line has only ``code | name``.

The codes the sheet leaves without approvers (CO.05.*, CO.06.03.08, CO.DP)
get no route: one must be set on APM before a request can use them (user
12/11 F4). Only "Phí ngân hàng" (CO.06.06.02) is paid without approval.
"""

# The data lines are the sheet's rows; they stay one per line.
# ruff: noqa: E501

from __future__ import annotations

_CCCD = "Trường hợp ký cá nhân: CCCD và khấu trừ 10% thuế TNCN mức chi > 5 triệu"

ROWS = f"""
CO.03|Chi mua nhập hàng/Giá vốn
CO.03.01|Chi phí vật tư tiêu hao trực tiếp|Biến phí|Cơ sở|1110|Kế toán kho VP|Cơ sở|11|Kế toán kho VP|10|01|10011101|
CO.03.03|Chi phí vật tư tiêu hao phụ|Biến phí|Cơ sở|1110|Kế toán kho VP|Cơ sở|11|Kế toán kho VP|10|01|10011101|
CO.03.02|Giá nhập sản phẩm/giá vốn sản phẩm|Biến phí|Cơ sở|1110|Kế toán kho VP|Cơ sở|11|Kế toán kho VP|10|01|10011101|
CO.01|Chi Nhân sự (không bao gồm lương thưởng khối KD và MKT)
CO.01.01|Lương chính|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.02|Lương thêm giờ|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.03|Hoa hồng|Biến phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.04|Hiệu suất|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.05|BHXH, BHYT, BHTN và KPCĐ|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.06|Thưởng tết, thưởng tháng 13, thưởng trên bảng lương|Biến phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.07|Thưởng nóng, thưởng đặc biệt|Biến phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.08|Phụ cấp, phúc lợi trên bảng lương|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.01.09|Chi phí phúc lợi (suất ăn mua ngoài, quà tặng, du lịch, YEP,...)|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán tiền lương|11|01|11011111|
CO.02|Chi khối KD và MKT
CO.02.01|Chi khối MKT
CO.02.01.01|Chi phí thuê mẫu, KOL, KOC|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00011001|Số tài khoản nhận tiền là KOL, KOC. {_CCCD}
CO.02.01.02|Chi phí quay chụp, makeup|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00011001|
CO.02.01.03|Chi phí mua tài khoản, phần mềm|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00011001|
CO.02.01.04|Chi phí tài nguyên|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00001001|
CO.02.01.05|Chi phí Ads facebook|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00000001|
CO.02.01.06|Chi phí Ads tiktok|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00000001|
CO.02.01.07|Chi phí Ads google|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00000001|
CO.02.01.08|Chi phí Ads zalo|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00000001|
CO.02.01.09|Chi phí Ads các kênh khác|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|00000001|
CO.02.01.10|Chi phí mua group, báo chí|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|11011011|
CO.02.01.11|Chi phí hoa hồng khách giới thiệu|Biến phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|11011011|
CO.02.01.12|Chi phí thuê Agency|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|11011011|{_CCCD}
CO.02.01.13|Chi phí vận hành khác|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán mkt|11|01|11011011|
CO.05|Chi tiền thanh toán phẫu thuật (Thuê ngoài)
CO.05.01|Chi trả tiền công/hoa hồng bác sỹ|Biến phí
CO.05.02|Chi thanh toán tiền thuê phòng mổ|Biến phí
CO.05.03|Chi thanh toán viện phí|Biến phí
CO.05.04|Chi mua dụng cụ, vật tư phẫu thuật|Biến phí
CO.05.05|Chi mua phẫu thuật khác|Biến phí
CO.06|Chi thanh toán chi phí vận hành
CO.06.01|Chi thanh toán chi phí trực tiếp cho bán hàng
CO.06.01.01|Chi thanh toán phí xét nghiệm|Biến phí|Cơ sở|1110|Cơ sở|Cơ sở|11|Kế toán thanh toán VP|10|01|00011011|
CO.06.01.02|Chi thanh toán phí tiếp khách|Định phí|Cơ sở|1100|Cơ sở|Cơ sở|11|Kế toán đối soát|00|10|00001001|
CO.06.01.03|Chi mua hoa quả, bánh, kẹo, trà nước cho khách|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.01.04|Chi mua bữa ăn cho khách|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.01.05|Chi mua hỗ trợ khách hàng đi lại, xe, nhà nghỉ|Biến phí|Cơ sở|1100|Điều xe|Cơ sở|11|Kế toán đối soát|00|10|00001011|
CO.06.01.06|Chi thanh toán dịch vụ gửi xe cho khách|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001011|
CO.06.01.07|Chi thanh toán giặt sấy|Định phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001011|
CO.06.01.08|Chi mua các CP vệ sinh (túi rác, giấy, hoá chất vs, xử lý rác thải, tinh dầu, sáp thơm…)|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.01.09|Chi mua trang trí (hoa, cây cảnh, tranh ảnh,…)|Định phí|Cơ sở|1111|Hành chính mua sắm|Cơ sở|11|Kế toán thanh toán|11|01|00001101|
CO.06.01.10|Chi mua xăng xe, cầu đường, gửi xe…|Định phí|Ban trợ lý|0011|Lái xe|Ban trợ lý|00|Kế toán thanh toán|11|01|00001011|
CO.06.01.11|Chi mua đóng gói hàng, ship hàng cho khách|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.01.12|Chi mua vật tư tiêu hao không qua kho|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.01.13|Chi mua đồ dùng cho khách hàng (cốc giấy, túi giấy …)|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.01.14|Chi trả phí pos|Biến phí||0000|||00|Kế toán tổng hợp|10|00|00011011|
CO.06.01.15|Chi thanh toán tiền chân gỗ|Định phí|Cơ sở|1100|Cơ sở|Cơ sở|11|Kế toán đối soát|00|10|00011011|{_CCCD}
CO.06.01.16|Chi mua quà tặng khách hàng, tổ chức sự kiện|Định phí|Cơ sở|1111|Cơ sở|Cơ sở|11|Kế toán đối soát|11|10|11011101|
CO.06.01.17|Chi mua in tem, mã, hồ sơ khách hàng, sổ khách hàng|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001001|
CO.06.01.18|Chi vận chuyển vật tư, hàng hoá|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001001|
CO.06.01.19|Chi mua khác|Biến phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001001|
CO.06.02|Chi mua hành chính, văn phòng
CO.06.02.01|Chi mua văn phòng phẩm|Định phí|Phòng ban đề xuất|1000|Phòng ban đề xuất|Phòng ban đề xuất|10|Kế toán đối soát|00|10|00001101|
CO.06.02.02|Chi mua đồ thắp hương, teabreak|Định phí|Cơ sở|1000|Cơ sở|Cơ sở|10|Kế toán đối soát|00|10|00001101|
CO.06.02.03|Chi mua nước uống|Định phí|Phòng ban đề xuất|1000|HCMS|Phòng ban đề xuất|10|Kế toán thanh toán|00|10|00001101|
CO.06.02.04|Chi thanh toán tiền chuyển phát nhanh, vận chuyển, đi lại|Định phí|Phòng ban đề xuất|1000|HCMS|Phòng ban đề xuất|10|Kế toán đối soát|00|10|00001011|
CO.06.02.05|Chi thanh toán tiền thay mực, in ấn tài liệu|Định phí|Phòng ban đề xuất|1000|HCMS|Phòng ban đề xuất|10|Kế toán đối soát|00|10|00001011|
CO.06.02.06|Chi mua sửa chữa nhỏ|Định phí|Phòng ban đề xuất|1100|HCMS|Phòng ban đề xuất|11|Kế toán đối soát|00|10|10011011|{_CCCD}
CO.06.02.07|Chi thanh toán tiền công tác phí|Định phí|Phòng ban đề xuất|1100||Phòng ban đề xuất|11|Kế toán đối soát|00|10|10001000|Giấy đi đường, giấy cử đi công tác
CO.06.02.08|Chi tiền cố vấn|Định phí|Phòng ban đề xuất|1111||Phòng ban đề xuất|11|Kế toán đối soát|11|10|10101000|Danh sách ngoại giao, chứng từ phạt
CO.06.02.09|Chi tiền gửi xe nhân viên|Định phí||0000||Phòng ban đề xuất|11|Kế toán đối soát|11|10|00011011|{_CCCD}
CO.06.02.10|Chi thanh toán tiền công tác (vé máy bay, phòng nghỉ, đi lại)|Định phí|Phòng ban đề xuất|1100|Ban thư ký|Phòng ban đề xuất|11|Kế toán thanh toán|00|01|10001001|
CO.06.02.11|Chi phí tiếp khách|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|10011011|
CO.06.02.12|Chi hành chính khác|Định phí|Phòng ban đề xuất|1000|Phòng ban đề xuất|Phòng ban đề xuất|10|Kế toán đối soát|00|10|10001001|
CO.06.03|Chi mua dịch vụ thuê ngoài
CO.06.03.01|Chi thanh toán thẻ hotline, điện thoại|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|11011011|
CO.06.03.02|Chi thanh toán dịch vụ bảo vệ/ lái xe|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|10011001|
CO.06.03.03|Chi thanh toán dịch vụ vệ sinh, giặt sấy khối hệ thống|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|11011011|{_CCCD}
CO.06.03.04|Chi tiền phí hợp tác kinh doanh/chuyên môn|Định phí|Phòng ban đề xuất|1111||Phòng ban đề xuất|11|Kế toán thanh toán|11|01|10011011|{_CCCD}
CO.06.03.05|Chi thanh toán điện nước sinh hoạt|Định phí||0000||Phòng ban đề xuất|11|Kế toán thanh toán|10|01|00011001|
CO.06.03.06|Chi tiền thuê mặt bằng, địa điểm kinh doanh|Định phí||0000|HCMS|HCMS|11|Kế toán thanh toán|11|01|00011001|
CO.06.03.07|Chi thanh toán internet, phần mềm, chữ ký số, nâng cấp tài khoản|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|10|01|00011011|
CO.06.03.08|Chi tiền thuê nhà cho khối văn phòng|Định phí||0000|||00||00|00|00011001|
CO.06.03.09|Chi mua dịch vụ thuê ngoài khác|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|11011011|
CO.06.04|Chi mua liên quan nhân sự, đào tạo, tuyển dụng
CO.06.04.01|Chi thanh toán chi phí tuyển dụng|Định phí|HCNS|1111|HCNS|HCNS|11|Kế toán thanh toán|11|01|00001001|
CO.06.04.02|Chi đào tạo|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|00001001|
CO.06.04.03|Chi thanh toán chi phí liên quan nhân sự (thuê nhà, visa, WPM…)|Định phí|Phòng ban đề xuất|1111|HCMS|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|00001001|
CO.06.06|Chi hoạt động khác
CO.06.06.01|Chi xin giấy phép|Định phí||0000||Phòng ban đề xuất|11|Kế toán thanh toán|11|11|00011011|
CO.06.06.02|Phí ngân hàng|Biến phí||0000|||00||00|01|00000001|
CO.06.06.03|Chi tiền nộp thuế, phí, lệ phí (ngoài thuế GTGT và TNDN)|Định phí||0000||Phòng TCKT|11|Kế toán thuế|11|01|00001001|
CO.06.06.04|Chi tài trợ, từ thiện|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|11011011|
CO.06.06.05|Chi mua khác|Định phí|Phòng ban đề xuất|1111|Phòng ban đề xuất|Phòng ban đề xuất|11|Kế toán thanh toán|11|01|11011101|
CO.07|Chi mua đầu tư, mua sắm, sửa chữa lớn
CO.07.01|Chi đầu tư thi công thiết kế tư vấn, nâng cấp sửa chữa|Đầu tư|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011111|
CO.07.02|Chi mua thiết bị nội ngoại thất, máy móc (>=30 triệu)|Đầu tư|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011101|
CO.07.03|Chi mua đồng phục nhân viên|Định phí|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011101|
CO.07.04|Chi mua khăn, ga, gối, đồng phục cho khách hàng|Biến phí|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011101|
CO.07.05|Chi nhập công cụ dụng cụ nhỏ (giá trị <5 triệu)|Định phí|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011101|
CO.07.06|Chi nhập công cụ dụng cụ nhỏ (giá trị >=5 triệu, <30 triệu)|Định phí|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011101|
CO.07.07|Chi mua chương trình phần mềm, công nghệ thông tin (phần mềm bán hàng, quản lý, kênh truyền, hạ tầng…)|Đầu tư|Phòng ban đề xuất|1111|Hành chính mua sắm|Phòng ban đề xuất|11|Kế toán TSCĐ|11|01|11011101|
CO.DP|Chi phí dự phòng|
CO.04|Thuế GTGT phải nộp|Biến phí|Phòng TCKT|1111||Phòng TCKT|11|Kế toán tổng hợp|11|01|10000010|
CO.08|Thuế TNDN phải nộp|Biến phí|Phòng TCKT|1111||Phòng TCKT|11|Kế toán tổng hợp|11|01|10000010|
CO.09|Chi phí tài chính
CO.09.01|Chi phí lãi vay/Phí home credit|Tài chính|Phòng TCKT|1111||Phòng TCKT|11|Kế toán tổng hợp|11|01|10000010|
CO.09.02|Lãi lỗ từ hoạt động đầu tư/giao dịch chứng khoán kinh doanh|Tài chính|Phòng TCKT|1111||Phòng TCKT|11|Kế toán tổng hợp|11|01|10000010|
CO.09.03|Chiết khấu thanh toán|Tài chính|Phòng TCKT|1111||Phòng TCKT|11|Kế toán tổng hợp|11|01|10000010|
"""

__all__ = ["ROWS"]
