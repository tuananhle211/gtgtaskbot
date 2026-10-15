"""The rules an order's script is checked against before anybody approves it.

"Khung nhập kịch bản" on the order form is read by the AI when the order is
placed (and when a returned order is sent again). Only the numbered forbidden
items count as errors; the brand section is context. The text is the unit's
(``UnitSettings.script_review_rules``), so the department edits it on the
admin page without a release; this is what it starts from.
"""

from __future__ import annotations

DEFAULT_SCRIPT_RULES = """\
1. Chủ thể thương hiệu

• Phòng khám Thẩm mỹ APEXMED là chủ thể chính, không phải Bác sĩ Vũ Trọng Tiến.
• Cách gọi đội ngũ bắt buộc: "Đội ngũ Bác sĩ Vũ Trọng Tiến – Thẩm mỹ Apexmed" \
(hoặc "Đội ngũ bác sĩ Thẩm mỹ Apexmed" nếu không cần nêu tên).

2. 8 mục cấm (chỉ đây mới tính là lỗi khi duyệt content)

1. Từ y tế: "điều trị", "chữa bệnh", "đặc trị", "chuyên trị", "tiệt trừ"
2. Khẳng định tuyệt đối: "cam kết", "an toàn tuyệt đối", "đảm bảo", "số 1", "hàng đầu"
3. Danh nghĩa cơ sở y tế khác: "Viện 103", "Viện Bỏng Quốc Gia"...
4. Claim quá đà: "không đau", "không nghỉ dưỡng", "bảo hành trọn đời", "không chảy máu"
5. Số liệu khách hàng cụ thể (vd "100.000 khách hàng")
6. Cấm "trực tiếp Bác sĩ Tiến làm"/mọi biến thể khẳng định 1 bác sĩ cụ thể trực tiếp \
thực hiện thủ thuật
7. Từ khóa riêng công ty: "Harmonic"
8. Viêm nhiễm: được NÊU tình trạng, cấm khẳng định XỬ LÝ/HẾT/GIẢI QUYẾT được
"""  # noqa: RUF001 - the brand's own en dash

#: The rules text an administrator may store.
MAX_RULES_LENGTH = 8000

#: The hashtags and the standard footer a post ends with. Not a rule (a
#: script without them is not refused): the AI adds them to the corrected
#: version it suggests when the script lacks them.
DEFAULT_SCRIPT_FOOTER = """\
#Apexmed #EkipBacSiTien

-----------❖❖❖---------
🏠 PHÒNG KHÁM THẨM MỸ APEXMED
📍 Cơ sở 1: 50 Hoàng Ngân, P. Thanh Xuân, TP. Hà Nội
📍 Cơ sở 2: 189 Hoàng Cầu, P. Đống Đa, TP. Hà Nội
☎️ Hotline: 0766.1515.66
"""

MAX_FOOTER_LENGTH = 2000

__all__ = ["DEFAULT_SCRIPT_FOOTER", "DEFAULT_SCRIPT_RULES", "MAX_FOOTER_LENGTH", "MAX_RULES_LENGTH"]
