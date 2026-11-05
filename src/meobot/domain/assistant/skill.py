"""The report assistant's skill ("Trợ lý AI", 2026-11-04): what the bosses
ask, and what the system's words mean.

Two kinds of question reach the assistant (the list the bosses were given is
``docs/ai-assistant/BOSS_QUESTIONS.md``):

* **figures** - "doanh thu hôm nay", "order trễ hạn", "điểm phòng Dựng tháng
  10": a report tool answers (:mod:`meobot.domain.assistant.reports`), never
  the model;
* **how the system works** - "doanh thu thực thu là gì", "Ads / Doanh thu
  tính thế nào", "ai duyệt order": one of the :data:`TERMS` below answers, in
  the assistant's voice, word for word - also without a model, so a provider
  down or a model that "knows better" changes nothing.

Every definition here is a statement about the code and names nothing it does
not do; each was checked against the module that computes it (the comment over
each group says which). A question no term covers goes to the model with
:func:`skill_block` in its prompt: the routing guide, the terms its words
touch, and the rule that a figure never comes from the chat.

Pure, no I/O, accent-folded like the report words.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.domain.assistant.reports import (
    REPORT_AREAS,
    ReportTool,
    asks_for_figures,
    mentions,
)
from meobot.domain.member.normalization import strip_accents as fold


@dataclass(frozen=True, slots=True)
class Term:
    """One thing the bosses ask about, and the answer."""

    name: str
    #: Accent-folded phrases that name it (``=word`` = a whole word), the
    #: term's own name included. The longest phrase found in a question picks
    #: the term.
    phrases: tuple[str, ...]
    answer: str
    #: The web screen to see it on.
    link: str | None = None
    #: The report that gives its figures, offered after the answer.
    report: ReportTool | None = None


TERMS: tuple[Term, ...] = (
    # --- Ads revenue: application/ads/team_service.py (Figures), domain/ads/models.py,
    #     domain/ads/crm.py, application/ads/regions.py --------------------------------
    Term(
        "Doanh thu tổng (thực thu)",
        ("doanh thu tong", "thuc thu", "doanh thu xet kpi", "doanh thu", "doanh so"),
        "Doanh thu tổng = thực thu: tổng tiền khách đã thanh toán, lấy từ CRM "
        "aicrmtech.vn theo ngày thanh toán, đã gồm VAT - không phải giá trị đơn hàng. "
        "Thanh toán bằng điểm không tính, tiền hoàn bị trừ ra. CRM đồng bộ mỗi giờ từ "
        "07:10 đến 23:10 nên số trong ngày còn tăng. Đây cũng là doanh thu xét KPI.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Doanh thu hoa hồng",
        ("dt hoa hong", "doanh thu hoa hong", "hoa hong"),
        "Doanh thu hoa hồng = Doanh thu tổng ÷ 1,08 (bỏ 8% VAT, áp dụng từ "
        "01/07/2026; trước đó bằng doanh thu tổng). Đây là mẫu số của tỉ lệ "
        "Ads / Doanh thu.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Ads / Doanh thu",
        ("ads / doanh thu", "ads/doanh thu", "ads / dt", "ads/dt", "ty le ads", "ti le ads"),
        "Ads / Doanh thu = tiền lập hoá đơn quảng cáo (khi chưa có hoá đơn thì lấy "
        "chi tiêu) ÷ Doanh thu hoa hồng. Ngưỡng: đến 40% là tốt, 40-60% cần theo dõi, "
        "trên 60% cần xử lý ngay. Ads / DT thực = tiền lập hoá đơn ÷ Doanh thu tổng "
        "(gồm VAT).",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Chi tiêu và lập hoá đơn",
        ("chi tieu", "lap hoa don", "hoa don", "chi phi ads", "chi phi quang cao"),
        "Chi tiêu = số tiền Meta báo đã tiêu cho quảng cáo theo ngày, đồng bộ từ các "
        "BM. Lập hoá đơn = số tiền Meta thực trừ theo hoá đơn (không cộng VAT hay phí "
        "thẻ). Hai số lệch nhau vì Meta gom chi tiêu rồi mới xuất hoá đơn. Google Ads "
        "và TikTok Ads do marketer nhập tay theo ngày.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Doanh thu Marketing và doanh thu Cơ sở",
        (
            "doanh thu marketing",
            "dt marketing",
            "doanh thu co so",
            "dt co so",
            "hn chuyen sg",
            "sg chuyen hn",
        ),
        "Mỗi khoản thu có hai vùng: đội marketing mang khách về (theo tài khoản CRM "
        "HN / SG) và cơ sở phục vụ khách. Doanh thu Marketing HN = khách HN làm ở HN + "
        "HN chuyển SG; Marketing SG = SG + SG chuyển HN. Doanh thu Cơ sở HN = HN + SG "
        "chuyển HN; Cơ sở SG = SG + HN chuyển SG. Hai cách chia có cùng tổng.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Hiệu suất theo cơ sở",
        ("hieu suat theo co so", "hieu suat co so", "hieu suat ads"),
        "Hiệu suất của một cơ sở = chi tiêu của các tài khoản quảng cáo thuộc cơ sở "
        "đó (BM Hà Nội / BM Sài Gòn) ÷ doanh thu Marketing của cơ sở đó (gồm VAT). "
        "Càng thấp càng tốt.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "BM",
        ("business manager", "bm ha noi", "bm sai gon", "=bm"),
        "BM = Business Manager của Facebook, nơi chứa các tài khoản quảng cáo; công ty "
        "có BM Hà Nội và BM Sài Gòn. Chọn một BM trên dashboard thì doanh thu và KPI "
        "chỉ tính doanh thu Marketing của vùng đó; doanh thu Cơ sở vẫn để nguyên.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Số điện thoại (SĐT)",
        ("chi phi / sdt", "chi phi tren sdt", "so dien thoai", "=sdt"),
        "SĐT = số điện thoại khách để lại từ quảng cáo Facebook, do Meta báo về. "
        "Chi phí / SĐT = chi tiêu ÷ số SĐT.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    Term(
        "Hotline và marketer đã nghỉ",
        ("hotline", "marketer da nghi"),
        "Doanh thu của tài khoản CRM Hotline (FB / TikTok / Google) và của marketer "
        "đã nghỉ vẫn cộng vào tổng team; trên dashboard chúng nằm riêng sau danh sách "
        "marketer, không chiếm chỗ trong xếp hạng.",
        "/ads",
        ReportTool.ADS_REVENUE,
    ),
    # --- KPI + budgets: application/ads/budget_service.py, domain/ads/budget.py ------------
    Term(
        "KPI doanh thu",
        ("=kpi",),
        "Mỗi marketer và cả team có KPI doanh thu theo tháng, nhập riêng cho BM Hà Nội "
        "và BM Sài Gòn (Phòng Ads > KPI). % KPI = doanh thu tổng đã đạt ÷ KPI; "
        '"tháng đã qua" là phần trăm thời gian của tháng, để so với tiến độ. Hỏi '
        "chung không nêu BM thì em cộng cả hai BM.",
        "/ads/kpi",
        ReportTool.ADS_KPI_BUDGET,
    ),
    Term(
        "Ngân sách tuần",
        ("ngan sach", "budget"),
        "Ngân sách quảng cáo của từng marketer chia 4 tuần (ngày 1-7, 8-14, 15-21, 22 "
        "đến cuối tháng), theo BM. Trạng thái: Vượt (đã chi quá ngân sách, kể cả giữa "
        "tuần), Đạt (tuần đã xong, chi từ 90% trở lên), Chưa đến ngưỡng (tuần đã xong, "
        "dưới 90%), Đang chạy, Chưa tới. Chi tiêu tính mọi nền tảng trên các tài khoản "
        "người đó giữ.",
        "/ads/budget",
        ReportTool.ADS_KPI_BUDGET,
    ),
    # --- Orders: domain/orders/pipeline.py, domain/board/models.py (TASK_STATES) -----------
    Term(
        "Order (task ORD)",
        ("task ord", "luong ord", "=order"),
        "Order = yêu cầu sản xuất nội dung gửi luồng ORD. Mỗi order có mã 5 ký tự "
        "(vd K7Q2M), chọn quy trình gồm các bước Biên kịch > Design > Dựng (tick bước "
        "nào làm bước đó), deadline mong muốn và gợi ý mã task. Order đi: duyệt order → "
        "từng bước Trưởng phòng giao việc, người làm nhận việc và nộp bài → người order "
        "duyệt final.",
        "/tasks",
        ReportTool.ORDERS,
    ),
    Term(
        "Trạng thái order",
        (
            "trang thai order",
            "trang thai task ord",
            "cho duyet",
            "cho phan cong",
            "da giao",
            "dang sua",
            "bi tra lai",
        ),
        "Chờ duyệt: chờ Trưởng phòng duyệt order, hoặc duyệt bài nộp / mã task. Order "
        "bị trả lại: chờ người order sửa rồi gửi lại. Chờ phân công: chờ Trưởng phòng "
        "của bước giao người làm. Đã giao, chưa nhận: người làm chưa bấm Nhận việc. "
        "Đang làm. Đang sửa: bị trả về làm lại. Hoàn thành. Đã huỷ.",
        "/tasks",
        ReportTool.ORDERS,
    ),
    Term(
        "Trễ hạn",
        ("tre han", "qua han", "tre deadline"),
        "Trễ hạn = bước đang làm đã qua deadline (deadline sửa khi đang sửa) mà chưa "
        "nộp. Deadline mong muốn của order do người order đặt; deadline từng bước do "
        "Trưởng phòng bước đó đặt khi giao việc. Bước xong sau deadline bị ghi là trễ "
        "trong báo cáo điểm.",
        "/tasks",
        ReportTool.ORDERS,
    ),
    Term(
        "Ai duyệt order",
        ("ai duyet", "nguoi duyet", "duyet order", "duyet final"),
        "Order gửi tới người duyệt được chọn trên form, không chọn thì tới Trưởng phòng "
        "của người order. Trưởng phòng tự tạo order thì không cần duyệt. Order chỉ có "
        "bước Design thì Trưởng phòng Design nhận việc là coi như duyệt. Bài nộp của "
        "từng bước do Trưởng phòng bước đó duyệt (khi bật duyệt, hoặc khi mã task nộp "
        "khác gợi ý); sản phẩm cuối do chính người order duyệt final.",
        "/tasks",
        ReportTool.ORDERS,
    ),
    Term(
        "Huỷ và trả order",
        ("huy order", "tra order", "tra lai order"),
        "Chỉ người order huỷ order của mình (chủ sở hữu và Quản trị viên huỷ được mọi "
        'order chưa xong). Trưởng phòng của bước đang làm không huỷ mà bấm "Trả order" '
        "kèm lý do; người order sửa rồi gửi lại, order quay về đúng bước đang dở, không "
        "phải duyệt lại. Điểm đã ghi trước khi huỷ vẫn giữ.",
        "/tasks",
        ReportTool.ORDERS,
    ),
    Term(
        "Order sinh nhật",
        ("sinh nhat",),
        "Order sinh nhật chỉ có bước Design, không cần duyệt order và không duyệt "
        "final; người làm được tính mã T1. Xem ở Bảng order > Order sinh nhật.",
        "/tasks/birthday",
        ReportTool.ORDERS,
    ),
    # --- Points: domain/orders/task_codes.py, domain/performance/quota.py,
    #     application/performance/coefficients.py, domain/performance/work_points.py ---------
    Term(
        "Điểm hiệu suất",
        ("diem hieu suat", "tinh diem", "ma task", "=diem"),
        "Mỗi bước sản xuất chấm điểm theo mã task: Biên kịch N1-N5, Design T1-T5, Dựng "
        "D1-D7 (N0 / T0 / D0 = việc khác, tự nhập điểm). Điểm một dòng = điểm của mã x "
        "% hình thức x số lượng. Điểm được ghi khi bước hoàn thành, vào tháng hoàn thành "
        "lần đầu, rồi nhân hệ số điểm của người đó. Mã nộp khác gợi ý thì Trưởng phòng "
        "duyệt trước khi tính.",
        "/performance/company",
        ReportTool.PERFORMANCE,
    ),
    Term(
        "Hình thức (% điểm)",
        ("hinh thuc", "adapt", "resize", "animated", "after effects", "dien hoat"),
        "Hình thức nhân với điểm của mã. Mặc định: Làm mới 100%, Adapt / sửa từ mẫu "
        "50% và Resize 20% (Design, Dựng), video có diễn hoạt 125% và After Effects "
        "150% (Dựng). Danh mục mã và hình thức do chủ sở hữu thiết lập.",
        "/tasks/task-codes",
        ReportTool.PERFORMANCE,
    ),
    Term(
        "Định mức",
        ("dinh muc", "chi tieu diem"),
        "Định mức tháng của một người = số ngày công chuẩn (trừ Chủ nhật, thứ Bảy tính "
        "nửa ngày) x 60 điểm/giờ x 8 giờ x 85% - mặc định 408 điểm một ngày, chủ sở "
        "hữu chỉnh ở Hiệu suất > Hệ số & định mức. Báo cáo so điểm đã đạt với định mức "
        "cả tháng và với phần định mức tới hôm nay.",
        "/performance/coefficients",
        ReportTool.PERFORMANCE,
    ),
    Term(
        "Hệ số điểm",
        ("he so diem", "he so"),
        "Hệ số nhân mọi điểm của một người trong tháng (mặc định 1, từ 0,01 đến 10), "
        "chủ sở hữu đặt và áp dụng từ một tháng trở đi. Mọi báo cáo điểm chỉ hiện điểm "
        "đã nhân hệ số.",
        "/performance/coefficients",
        ReportTool.PERFORMANCE,
    ),
    Term(
        "Điểm PR và Seeding",
        ("diem pr", "diem seeding", "seeding"),
        "Người PR chọn mã task trên bài; điểm được cộng khi bài hoàn thành (từ Sẵn sàng "
        "đăng trở đi). Mỗi bài đăng có link được 10 điểm. Seeding: mỗi số điện thoại "
        "được Trưởng phòng PR duyệt được 200 điểm; việc khác tự khai điểm, chờ duyệt.",
        "/performance/pr-claims",
        ReportTool.PR,
    ),
    # --- PR board: domain/board/models.py PHASE_LABELS ------------------------------------
    Term(
        "Task PR",
        ("task pr", "pha pr", "bai dang", "bai da dang"),
        "Task PR đi qua các pha: Order (ý tưởng, brief, kịch bản) → Duyệt → Sản xuất → "
        "Duyệt cuối → Hoàn thành (sẵn sàng đăng, đã đăng, đo lường). Bài đã đăng tính "
        "theo bài có link còn hiệu lực.",
        "/tasks?unit=PR",
        ReportTool.PR,
    ),
    # --- Resources: domain/resources/models.py (ALERT_LABELS, RED_ALERTS), requests.py -----
    Term(
        "Tài nguyên",
        ("trang thai tai nguyen", "tai nguyen", "=via", "=tkqc", "=page"),
        "Tài nguyên = kho Via, Page, Nhóm, TKQC, BM, tài khoản AI, CapCut... do Phòng "
        "Tài nguyên quản lý; mỗi cái có mã, người giữ, trạng thái, ngày bàn giao và "
        "ngày rà soát.",
        "/resources",
        ReportTool.RESOURCES,
    ),
    Term(
        "Cảnh báo tài nguyên",
        ("canh bao tai nguyen", "canh bao", "muc do"),
        "Cảnh báo: người giữ đã nghỉ việc, tài khoản người giữ bị vô hiệu, người giữ "
        "chuyển ban, quá hạn rà soát (mặc định 30 ngày), trạng thái cần chú ý, tài "
        "nguyên đã mất. Mức đỏ = người giữ nghỉ / bị vô hiệu / chuyển ban, hoặc đã mất.",
        "/resources/reports",
        ReportTool.RESOURCES,
    ),
    Term(
        "Đề xuất tài nguyên",
        ("de xuat mua tai nguyen", "de xuat tai nguyen", "de xuat mua", "de xuat"),
        "Ai cũng đề xuất mua tài nguyên được: Tạo đề xuất → Trưởng phòng duyệt → Duyệt "
        "chi → Mua & nhập kho (Phòng Tài nguyên) → người đề xuất xác nhận đã nhận.",
        "/resources/requests",
        ReportTool.RESOURCES,
    ),
    # --- The assistant and permissions: domain/assistant/reports.py, application/org -------
    Term(
        "Trợ lý AI",
        ("tro ly", "=bot", "hoi gi", "hoi duoc gi"),
        "Em chỉ đọc số từ các báo cáo của hệ thống, không ước đoán. Quyền hỏi em do chủ "
        "sở hữu tích riêng từng người; người được tích xem số toàn công ty. Sếp hỏi theo "
        "mảng và thời gian, vd: doanh thu hôm nay / tuần này / tháng 10, KPI, ngân sách "
        "tuần, order trễ hạn, điểm hiệu suất phòng Dựng, tình hình PR, tài nguyên.",
        None,
        ReportTool.COMPANY,
    ),
    Term(
        "Phân quyền",
        ("phan quyen", "=quyen", "ai xem duoc"),
        "Quyền có 3 lớp: vai trò (Chủ sở hữu, Quản trị viên có mọi quyền), phòng ban "
        "(theo vị trí Trưởng phòng / Nhân viên, chỉnh ở Quản trị > Phòng ban) và quyền "
        "cấp riêng từng người (chỉ chủ sở hữu, Quản trị > Quản lý tài khoản > Quyền). "
        "Trưởng phòng xem điểm của người báo cáo trực tiếp cho mình.",
        "/admin/departments",
        None,
    ),
)

#: Words that ask what something is or how it works ("là gì", "tính thế nào")
#: rather than for its figure.
_EXPLAIN_WORDS: tuple[str, ...] = (
    "la gi",
    "nghia la",
    "y nghia",
    "the nao la",
    "tinh the nao",
    "tinh nhu the nao",
    "tinh nhu nao",
    "tinh sao",
    "cach tinh",
    "cong thuc",
    "lam sao",
    "lam the nao",
    "khac gi",
    "khac nhau",
    "xem o dau",
    "nam o dau",
    "o dau ra",
    "quy trinh",
    "chia the nao",
    "chia nhu the nao",
    "hoat dong",
    "van hanh",
    "giai thich",
    "ai duyet",
    "ai duoc",
    "ai co quyen",
    "ai xem duoc",
    "hoi duoc gi",
    "lay tu dau",
    "lay o dau",
    "tinh vao",
    "vao dau",
    "di qua",
)


def asks_to_explain(message: str) -> bool:
    """A question about what a word means or how the system works - not one
    asking for its figure ("doanh thu thực thu là gì" yes, "doanh thu hôm nay
    là bao nhiêu" no)."""
    if asks_for_figures(message):
        return False
    folded = fold(message)
    if "khac" in folded and ("the nao" in folded or "nhu nao" in folded):
        return True  # "A khác B thế nào"
    return any(word in folded for word in _EXPLAIN_WORDS)


def term_for(message: str) -> Term | None:
    """The term the question is about: the one whose longest phrase it holds."""
    best: tuple[int, Term] | None = None
    for term in TERMS:
        for phrase in _phrases(term):
            if mentions((phrase,), message):
                size = len(phrase.lstrip("="))
                if best is None or size > best[0]:
                    best = (size, term)
    return best[1] if best is not None else None


def _phrases(term: Term) -> tuple[str, ...]:
    """The term's phrases and its own name ("KPI doanh thu là gì?")."""
    return (fold(term.name), *term.phrases)


def explain(term: Term, base_url: str = "") -> str:
    """The answer to a "what is / how" question, in the assistant's voice."""
    parts = [f"Dạ, **{term.name}**: {term.answer}"]
    if term.report is not None:
        parts.append(f"Sếp muốn xem số thì hỏi em: {_ASK_FOR[term.report]}.")
    if term.link:
        base = base_url.strip().rstrip("/")
        parts.append(f"Xem trên web: {base}{term.link}" if base else f"Xem trên web: {term.link}")
    return "\n".join(parts)


#: What to ask for each report's figures, shown under an explanation.
_ASK_FOR: dict[ReportTool, str] = {
    ReportTool.COMPANY: '"tổng hợp tình hình công ty tháng này"',
    ReportTool.ADS_REVENUE: (
        '"doanh thu hôm nay", "doanh thu tuần này", "doanh thu tháng 10" hoặc '
        '"doanh thu của <họ tên> hôm nay"'
    ),
    ReportTool.ADS_KPI_BUDGET: '"KPI tháng này", "KPI của <tên>" hoặc "ngân sách tuần"',
    ReportTool.PERFORMANCE: '"điểm hiệu suất tháng này" hoặc "điểm phòng <tên phòng>"',
    ReportTool.ORDERS: '"order trễ hạn" hoặc "có bao nhiêu order chờ duyệt"',
    ReportTool.RESOURCES: '"tình hình tài nguyên" hoặc "cảnh báo tài nguyên"',
    ReportTool.PR: '"tình hình PR tháng này"',
}


#: The routing guide in the prompt: which report answers what, how to fill
#: the period, and the rules the answers keep.
_GUIDE = """\
Sếp hỏi SỐ LIỆU -> gọi đúng MỘT công cụ báo cáo, không tự viết con số:
{areas}
- Thời gian: "hôm nay / hôm qua / 3/11" -> day; "tuần này / tuần trước / 7 ngày qua /
  từ 1/11 đến 3/11" -> day + day_to (chỉ report.ads_revenue); "tháng này / tháng 10"
  -> month; không nói -> tháng này.
- Hỏi số của MỘT người ("doanh thu của Đào Đình Dương", "KPI của <tên>", "điểm của
  <tên>") -> điền person bằng đúng họ tên trong câu hỏi.
- Câu nối tiếp chỉ đổi thời gian ("hôm nay thôi", "còn tháng 9?") -> hỏi lại đúng
  báo cáo vừa xem.
- report.company chỉ khi sếp hỏi tổng hợp / tình hình chung, không khi câu hỏi nêu
  một mảng.
Sếp hỏi CÁCH HỆ THỐNG HOẠT ĐỘNG -> trả lời theo [THUẬT NGỮ] dưới đây, đúng nghĩa
trong hệ thống này, không theo nghĩa chung ngoài đời; không có trong đó thì nói em
chưa có thông tin, mời sếp xem trên web hoặc hỏi người phụ trách.
Không có báo cáo cho điều sếp hỏi (vd doanh thu từng khách, lương, chấm công) -> nói
rõ em chưa có báo cáo đó, không ước đoán."""


def skill_block(message: str) -> str:
    """The ``[ASSISTANT SKILL]`` section for one turn: the routing guide, the
    terms this message touches (whole answers) and the names of the others -
    small enough to sit beside the rest of the prompt."""
    areas = "\n".join(f"- {tool.value}: {label}" for tool, label in REPORT_AREAS.items())
    lines = [_GUIDE.format(areas=areas), "", "[THUẬT NGỮ]"]
    picked = _touched(message)
    lines.extend(f"- {term.name}: {term.answer}" for term in picked)
    others = [term.name for term in TERMS if term not in picked]
    if others:
        lines.append("Các thuật ngữ khác em giải thích được: " + ", ".join(others) + ".")
    return "\n".join(lines)


def _touched(message: str, limit: int = 3) -> list[Term]:
    """The terms whose phrases the message holds, longest phrase first."""
    found: list[tuple[int, int, Term]] = []
    for index, term in enumerate(TERMS):
        sizes = [
            len(phrase.lstrip("=")) for phrase in _phrases(term) if mentions((phrase,), message)
        ]
        if sizes:
            found.append((-max(sizes), index, term))
    return [term for _size, _index, term in sorted(found)[:limit]]


__all__ = ["TERMS", "Term", "asks_to_explain", "explain", "skill_block", "term_for"]
