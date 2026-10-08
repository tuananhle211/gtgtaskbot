"""What MeoBot's words mean, written down once.

Step 1F.2.3h. The assistant was answering PR questions out of general world
knowledge: *"sản phẩm phái sinh là gì?"* came back about financial derivatives
and intellectual-property licensing, and *"đã xuất bản rồi có sửa link được
không?"* came back as *"thường thì admin sửa được"* - a plausible sentence about
software in general and a wrong one about this system.

The fix is not a bigger system prompt. It is **one authoritative, versioned
source** for the module's vocabulary and its durable rules, which every turn
that touches MeoBot's domain is grounded in.

Why this module is static, and in ``domain``
---------------------------------------------

Nothing here reads the database, and nothing here is per-user. A definition of
*"sản phẩm phái sinh"* is the same sentence for every actor on every turn, so
rebuilding it per request would be a query per turn for a constant - see
:class:`~meobot.application.meobot_context_service.MeoBotAssistantContextService`,
which holds this beside the *dynamic* half and assembles both.

It is also **not parsed from the documentation**. ``docs/pr/*.md`` explains this
module to people; this module is what the runtime uses, and the two are kept in
step by tests rather than by one reading the other at startup.

What is derived rather than restated
-------------------------------------

:data:`CANONICAL_STAGES` is built from :class:`~meobot.domain.pr.models.PrWorkflowStage`
and :func:`~meobot.domain.pr.labels.stage_label`, so the canonical workflow in
the prompt is the enum's own order and the panel's own Vietnamese - not a
hand-typed list that would drift the first time a stage moved. A test asserts
the rendered order equals the enum's.

The glossary and the historical rules **are** written out, because they are
statements *about* the code rather than values *in* it. They are kept short and
each one names the module that enforces it, so a reader who doubts a line can go
and check it.

Untrusted data is not in here
------------------------------

Everything in this module is authored text under version control. Record content
- titles, comments, notes, labels - never reaches this file; it travels in the
dynamic half, JSON-encoded and explicitly marked as data. See
:mod:`meobot.domain.assistant.work_context`.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.domain.pr.labels import stage_label
from meobot.domain.pr.models import PrWorkflowStage

#: The version of the canonical context, for diagnostics. Bumped when the
#: definitions or the rules below change - not when the *dynamic* context
#: assembled around them changes.
#:
#: Deliberately the step identifier rather than a serial: a log line saying
#: ``context_version=1F.2.3f.3`` points at a design document and a commit, which
#: is what somebody debugging an odd answer actually needs. It is **not** ordered
#: - the step that last changed the rules is not always the newest-looking id -
#: and a serial would have been the thing that lost the pointer.
MEOBOT_DOMAIN_CONTEXT_VERSION = "1F.2.6"


@dataclass(frozen=True, slots=True)
class DomainTerm:
    """One MeoBot word, its internal name, and what it is **not**.

    ``not_this`` is the load-bearing field and the reason this is a dataclass
    rather than a dict of strings. Most of the drift this step exists to stop is
    not the model failing to know what a derivative is - it is the model knowing
    a *different* derivative very well. Naming the wrong reading explicitly is
    what displaces it.
    """

    #: What a person says, in Vietnamese.
    term: str
    #: The internal name, so a reader can connect the two vocabularies.
    code: str
    definition: str
    #: The reading this term must **not** be given inside MeoBot.
    not_this: str = ""

    def render(self) -> str:
        line = f"- {self.term} ({self.code}): {self.definition}"
        if self.not_this:
            line += f" KHÔNG PHẢI: {self.not_this}"
        return line


#: The canonical workflow, from the enum itself. ``CANCELLED`` is listed apart
#: because it is not a step in the line - it is where pre-publication work ends
#: when it is abandoned.
CANONICAL_STAGES: tuple[PrWorkflowStage, ...] = tuple(
    stage for stage in PrWorkflowStage if stage is not PrWorkflowStage.CANCELLED
)


#: The words, and the readings they must displace.
#:
#: Ordered as the work happens rather than alphabetically: somebody reading this
#: block top to bottom is reading the life of a piece of content, which is the
#: order the definitions make sense in.
GLOSSARY: tuple[DomainTerm, ...] = (
    DomainTerm(
        term="Nội dung",
        code="Content",
        definition=(
            "Đơn vị công việc gốc của TasksBot: một bài/kịch bản đi qua toàn bộ quy "
            "trình duyệt và sản xuất. Có mã (vd CNT-2026-000123), tiêu đề, người "
            "phụ trách, bước hiện tại."
        ),
        not_this="một bài đã đăng trên mạng xã hội, hay một file video cụ thể.",
    ),
    DomainTerm(
        term="Loại nội dung",
        code="Content Type",
        definition="Định dạng của nội dung (vd kịch bản video ngắn). Có thể chưa phân loại.",
    ),
    DomainTerm(
        term="Mức độ ưu tiên",
        code="Priority",
        definition=(
            "Bốn mức phân loại hàng đợi: Rất gấp, Gấp, Ưu tiên, Bình thường. Chỉ là "
            "siêu dữ liệu để sắp việc; không đổi bước quy trình."
        ),
    ),
    DomainTerm(
        term="Tài nguyên",
        code="Resource",
        definition=(
            "Tài liệu ĐẦU VÀO đính kèm nội dung để người viết và người duyệt đọc: "
            "brief, ảnh tham khảo, video mẫu, tài liệu khách."
        ),
        not_this="file đã sản xuất, và không phải bằng chứng đã đăng.",
    ),
    DomainTerm(
        term="Người phụ trách",
        code="Responsible / owner_user_id",
        definition=(
            "Người chịu trách nhiệm cho nội dung này (hoặc người đang giữ một task "
            "chưa xong của nó)."
        ),
        not_this="người sản xuất, và không phải người tạo ra nội dung lúc đầu.",
    ),
    DomainTerm(
        term="Người sản xuất",
        code="Producer / producer_user_id",
        definition="Người đang nhận phần sản xuất của nội dung này.",
        not_this="người phụ trách. Hai vai trò này thường là hai người khác nhau.",
    ),
    DomainTerm(
        term="Sản phẩm gốc",
        code="Production Submission",
        definition=(
            "File gốc do người sản xuất nộp để duyệt nội bộ. Bảng ghi thêm (append-"
            "only): mỗi lần nộp là một dòng mới, có số thứ tự, và người duyệt nội bộ "
            "ký vào đúng dòng đó."
        ),
        not_this="bản cắt lại làm sau, và không phải bài đăng.",
    ),
    DomainTerm(
        term="Sản phẩm phái sinh",
        code="Derivative",
        definition=(
            "Bản cắt / remix / đổi định dạng làm lại TỪ CHÍNH nội dung đó, dùng lại "
            "cho kênh hoặc thời điểm khác. Có thể trỏ về sản phẩm gốc mà nó cắt ra "
            "(nguồn), nhưng không bắt buộc. Không tạo nội dung mới, không chạy lại "
            "quy trình duyệt, không đổi bước."
        ),
        not_this=(
            "phái sinh tài chính, phái sinh bản quyền/tác phẩm phái sinh theo luật sở "
            "hữu trí tuệ, hay thuật ngữ quản trị sản phẩm nói chung."
        ),
    ),
    DomainTerm(
        term="Link / đường dẫn sản phẩm",
        code="asset location",
        definition=(
            "Nơi FILE đang nằm: link Drive, đường dẫn NAS, ổ đĩa ánh xạ, UNC. Có thể "
            "là đường dẫn lưu trữ chứ không nhất thiết là link web."
        ),
        not_this="link bài đăng công khai.",
    ),
    DomainTerm(
        term="Link sản phẩm / đích đến",
        code="Destination",
        definition=(
            "Trang đích thương mại mà nội dung dẫn người xem tới: landing page, trang "
            "đặt lịch, trang dịch vụ. Là siêu dữ liệu của chiến dịch."
        ),
        not_this="bằng chứng rằng nội dung đã được đăng.",
    ),
    DomainTerm(
        term="Xuất bản",
        code="Publication",
        definition=(
            "Bản ghi rằng MỘT file cụ thể - một sản phẩm gốc HOẶC một sản phẩm phái "
            "sinh - đã thực sự được đăng lên một kênh, kèm link bài đăng công khai và "
            "thời điểm đăng."
        ),
        not_this="kế hoạch đăng, và không phải link đích đến.",
    ),
    DomainTerm(
        term="Link đăng",
        code="publication url",
        definition="Link http(s) tới bài đăng công khai thật sự.",
        not_this="đường dẫn tới file sản phẩm.",
    ),
    DomainTerm(
        term="Thu hồi bài đăng",
        code="Publication Reversal",
        definition=(
            "Đánh dấu một bản ghi xuất bản là ghi nhầm. Dòng dữ liệu VẪN CÒN với "
            "trạng thái REVERSED; không có gì bị xoá."
        ),
        not_this="xoá lịch sử đăng, và không gỡ bài thật trên nền tảng.",
    ),
    DomainTerm(
        term="Kênh",
        code="Channel",
        definition=(
            "Một tài khoản/trang cụ thể trên một nền tảng mà nội dung được đăng lên. "
            "Có mã (vd CH-0004), tên, nền tảng, có thể có handle và link trang."
        ),
        not_this="một nền tảng nói chung, và không phải một bài đăng.",
    ),
    DomainTerm(
        term="Nền tảng của kênh",
        code="Channel Platform",
        definition=(
            "Mạng/dịch vụ cấp cao nhất mà kênh thuộc về: Facebook, Instagram, TikTok, "
            "YouTube, Website, Khác. Suy ra từ nền tảng đã đăng ký của kênh, không "
            "đoán từ tên hay từ đường link. Kênh đăng ký trên một nền tảng ngoài sáu "
            "loại đó thì hiển thị là 'Chưa xác định' cho tới khi có người sửa lại."
        ),
        not_this=("định dạng nội dung (Shorts, Reels), và không phải loại trang (Page, Group)."),
    ),
    DomainTerm(
        term="Chỉ số kênh",
        code="Channel Metrics",
        definition=(
            "Các số liệu quan sát được về tài khoản/kênh tại MỘT thời điểm ghi nhận cụ "
            "thể: followers, số bài, views/reach/impressions/engagements theo cửa sổ 7 "
            "hoặc 30 ngày. Chỉ số 'hiện tại' của kênh chính là lần ghi nhận gần nhất, "
            "kèm đúng thời điểm ghi nhận đó."
        ),
        not_this=(
            "chỉ số của một bài đăng cụ thể, và không phải số liệu tự động cập nhật "
            "theo thời gian thực."
        ),
    ),
    DomainTerm(
        term="Bản ghi chỉ số",
        code="Metric Snapshot",
        definition=(
            "Một lần đo, chỉ ghi thêm và không sửa/xoá: thời điểm ghi nhận, các số "
            "liệu, nguồn dữ liệu và người ghi nhận. Nguồn API nghĩa là TasksBot lấy tự "
            "động từ nền tảng và KHÔNG có người ghi nhận; nguồn Nhập thủ công nghĩa là "
            "một người đã tự gõ vào và tên người đó được lưu. Nhập sai thì ghi nhận "
            "một bản mới đúng, bản cũ vẫn còn."
        ),
        not_this=(
            "một ô số liệu sửa đè được, và không phải bảo đảm rằng số đó vẫn đúng ở hiện tại."
        ),
    ),
    DomainTerm(
        term="Nguồn dữ liệu của kênh",
        code="Channel Metrics Status",
        definition=(
            "Số liệu của kênh đang đến từ đâu: 'Đã kết nối API' nếu có kết nối còn "
            "hiệu lực, 'Cần xác thực lại' nếu kết nối hỏng, 'Dữ liệu thủ công' nếu "
            "không có kết nối nhưng đã có người nhập tay, 'Chưa kết nối' nếu chưa có "
            "gì. Trạng thái này được suy ra chứ không lưu."
        ),
        not_this="kết quả của lần đồng bộ gần nhất - đó là một trạng thái khác.",
    ),
    DomainTerm(
        term="Kết nối kênh",
        code="Channel Connection",
        definition=(
            "Liên kết giữa một kênh TasksBot và một tài khoản nền tảng thật, tạo ra khi "
            "người quản trị cấp quyền qua OAuth. Mỗi kênh chỉ có tối đa MỘT kết nối "
            "đang hoạt động cho mỗi nền tảng. Trạng thái kết nối nói về QUYỀN TRUY CẬP: "
            "Đã kết nối, Cần xác thực lại, Chưa kết nối, Chờ chọn tài khoản (đã cấp "
            "quyền xong nhưng chưa chọn Trang/tài khoản nào - chưa đồng bộ được)."
        ),
        not_this=("kết quả đồng bộ, và không phải thứ mà mọi kênh YouTube đều mặc định có."),
    ),
    DomainTerm(
        term="Trạng thái đồng bộ",
        code="Sync Status",
        definition=(
            "Lần chạy đồng bộ gần nhất ra sao: Chưa đồng bộ lần nào, Đang đồng bộ, "
            "Đồng bộ thành công, Đồng bộ thất bại. Tách khỏi trạng thái kết nối vì hai "
            "thứ này thường khác nhau: kết nối vẫn tốt mà lần chạy vừa rồi vẫn có thể "
            "thất bại do nền tảng lỗi hoặc quá giới hạn truy vấn."
        ),
        not_this="bằng chứng rằng kết nối đã hỏng.",
    ),
    DomainTerm(
        term="Tự động đồng bộ",
        code="Auto Sync",
        definition=(
            "Khi một kênh có kết nối còn hiệu lực, TasksBot tự lấy số liệu theo lịch "
            "hàng ngày. Chỉ áp dụng cho kênh ĐÃ KẾT NỐI - không phải mọi kênh YouTube, "
            "Facebook hay Instagram."
        ),
        not_this="tính năng bật sẵn cho mọi kênh, và không phải cập nhật theo thời gian thực.",
    ),
    DomainTerm(
        term="Duyệt",
        code="Approval",
        definition=(
            "Quyết định của một người tại một cổng duyệt (Trưởng nhóm, Trưởng phòng, "
            "Duyệt nội bộ), gắn với đúng phiên bản kịch bản hoặc đúng file đã xem."
        ),
        not_this="một câu đồng ý trong bình luận hay trong hội thoại chat.",
    ),
    DomainTerm(
        term="Hoàn tác",
        code="Undo",
        definition=(
            "Lấy lại quyết định gần nhất còn hoàn tác được, dựa trên lịch sử chuyển "
            "bước có cấu trúc. Bản thân việc hoàn tác cũng được ghi lại."
        ),
    ),
    DomainTerm(
        term="Lịch sử",
        code="transition history",
        definition=(
            "Lịch sử chuyển bước có cấu trúc của nội dung: mỗi lần đổi bước là một "
            "dòng, ghi ai chuyển, từ bước nào sang bước nào, và lần hoàn tác nào đã "
            "lấy lại nó."
        ),
        not_this="lịch sử chỉnh sửa bình luận, và không phải nhật ký audit đầy đủ.",
    ),
    DomainTerm(
        term="Bình luận",
        code="Comment",
        definition=(
            "Trao đổi vận hành gắn với nội dung, một tầng trả lời. Xoá bình luận để "
            "lại dấu 'Đã xoá bình luận.' và giữ nguyên các trả lời bên dưới."
        ),
        not_this=(
            "ý kiến duyệt, sự kiện audit, task, phiên bản nội dung, sản phẩm sản xuất "
            "hay bản ghi xuất bản."
        ),
    ),
)


#: Durable rules the assistant must never advise against. Each names where it is
#: enforced, so a doubtful reader can check the code rather than this file.
HISTORICAL_RULES: tuple[str, ...] = (
    "Đường dẫn file sản phẩm (gốc và phái sinh) chấp nhận cả link web lẫn đường dẫn "
    "lưu trữ nội bộ; còn link đăng thì luôn phải là link http(s) công khai.",
    "Khi một file đã từng được một bản ghi xuất bản trỏ tới - KỂ CẢ bản ghi đã bị thu "
    "hồi (REVERSED) - thì đường dẫn, loại sản phẩm và nguồn gốc của file đó bị khoá "
    "với TẤT CẢ mọi người, kể cả người đã thêm nó. Tên và ghi chú vẫn sửa được.",
    "File đã từng được xuất bản thì không ai xoá được.",
    "Thu hồi bài đăng KHÔNG xoá bằng chứng lịch sử: dòng dữ liệu vẫn còn, và vẫn tính "
    "khi hệ thống quyết định có cho xoá vĩnh viễn nội dung hay không.",
    "Nội dung đã từng có bất kỳ bản ghi xuất bản nào thì không xoá vĩnh viễn được.",
    "Thêm sản phẩm phái sinh không chạy lại quy trình: không đổi bước, không tạo phiên "
    "bản mới, không huỷ lượt duyệt nào.",
    "Bình luận không đổi bước, không tạo phiên bản, không thoả mãn và không huỷ lượt duyệt nào.",
    "Ai xem được nội dung thì thêm được sản phẩm phái sinh, bình luận, và GHI NHẬN "
    "BÀI ĐĂNG cho nội dung đó - không cần được phân công kênh, không cần là chủ sở "
    "hữu, người phụ trách hay người sản xuất, và không cần quyền quản trị; nhưng "
    "sửa/xoá bản ghi của người khác thì không.",
    "Ghi nhận bài đăng vẫn phải hợp lệ: nội dung phải đang ở bước cho phép đăng, kênh "
    "phải là kênh có thật, sản phẩm phải thuộc chính nội dung đó và phải chọn đúng "
    "MỘT sản phẩm (bản gốc hoặc phái sinh), link đăng phải là link http(s) công khai.",
    "Thu hồi bài đăng vẫn chỉ dành cho quản trị: xem được nội dung, hay tự tay ghi "
    "nhận bài đăng, đều không cho quyền thu hồi.",
    "Bước ARCHIVED nghĩa là không chuyển bước ra khỏi đó nữa và không xoá được - "
    "KHÔNG có nghĩa là cấm ghi thêm siêu dữ liệu, phái sinh hay bình luận.",
    "TasksBot có trình kết nối tự động cho ĐÚNG BỐN nền tảng: YOUTUBE, FACEBOOK (Trang "
    "Facebook), INSTAGRAM (tài khoản Instagram Professional) và TIKTOK. Website và "
    "Khác không có. "
    "Nhưng có trình kết nối KHÔNG đồng nghĩa với việc kênh đó đang tự đồng bộ: chỉ "
    "những kênh mà người quản trị đã kết nối bằng OAuth mới tự đồng bộ. Kênh chưa kết "
    "nối - kể cả kênh YouTube, Facebook, Instagram hay TikTok - có số liệu hoàn toàn "
    "do người dùng nhập tay. Không được nói hay ngụ ý rằng mọi kênh đều đang tự đồng bộ.",
    "TIKTOK là trình kết nối MỚI và HẸP hơn ba trình kia. Nó dùng TikTok Display API, "
    "chỉ đọc được số liệu cộng dồn trọn đời của tài khoản: Followers, Following, tổng "
    "số video, và tổng lượt thích trọn đời. TikTok Display API KHÔNG cung cấp số liệu "
    "theo cửa sổ thời gian, nên kênh TikTok CHƯA có các chỉ số 7 ngày / 30 ngày - "
    "không có lượt xem 30 ngày, không có tương tác 30 ngày, không có bài đăng 30 ngày, "
    "không có video nổi bật. Ô trống ở những chỗ đó nghĩa là CHƯA ĐO ĐƯỢC, tuyệt đối "
    "không phải bằng 0. Tăng trưởng Followers vẫn tính được vì TasksBot tự so sánh các "
    "lần ghi nhận của chính mình. Nếu được hỏi vì sao thiếu, hãy nói thẳng: cần khảo "
    "sát khả năng thật của TikTok API trên tài khoản production trước khi xây tiếp.",
    "Kênh Facebook chỉ kết nối được với TRANG Facebook (Page). Không hỗ trợ trang cá "
    "nhân, Nhóm, tài khoản quảng cáo. Kênh Instagram chỉ kết nối được với tài khoản "
    "Instagram Professional (Business/Creator) đã liên kết với một Trang Facebook; "
    "tài khoản cá nhân thường không kết nối được.",
    "Khi cấp quyền Meta, một người thường quản lý nhiều Trang. TasksBot KHÔNG tự chọn - "
    "người dùng phải chọn đúng Trang/tài khoản, và TasksBot chỉ lấy số liệu của tài "
    "khoản được chọn.",
    "Muốn biết một kênh có đang tự đồng bộ hay không thì phải xem trạng thái kết nối "
    "của chính kênh đó trong bối cảnh, không suy ra từ việc nó là kênh YouTube.",
    "Nguồn của một bản ghi chỉ số là sự thật về bản ghi đó: nguồn API nghĩa là lấy tự "
    "động từ nền tảng, nguồn Nhập thủ công nghĩa là người gõ tay. Một kênh đã kết nối "
    "API vẫn có thể có bản ghi thủ công mới hơn - khi đó số hiện tại là số nhập tay và "
    "phải nói đúng như vậy.",
    "Kết nối 'Cần xác thực lại' nghĩa là quyền truy cập đã hỏng và số liệu KHÔNG còn "
    "cập nhật nữa, dù các số cũ lấy từ API vẫn còn nguyên. Không được mô tả kênh đó "
    "như đang đồng bộ bình thường.",
    "Đồng bộ thất bại KHÔNG có nghĩa là mất kết nối: nền tảng lỗi hoặc quá giới hạn "
    "truy vấn thì lần chạy hỏng nhưng kết nối vẫn còn, và hệ thống sẽ tự thử lại.",
    "Ngắt kết nối KHÔNG xoá số liệu: mọi bản ghi chỉ số đã lấy được, cả API lẫn thủ "
    "công, đều giữ nguyên.",
    "Chỉ người có quyền quản trị kênh mới kết nối, ngắt kết nối hay bấm đồng bộ được. "
    "Người bấm 'Đồng bộ ngay' KHÔNG được ghi là người ghi nhận số liệu - dữ liệu đến "
    "từ API, người đó chỉ kích hoạt lần lấy dữ liệu.",
    "Chỉ số kênh luôn gắn với thời điểm ghi nhận của nó. Khi trả lời 'followers hiện "
    "tại', phải hiểu là con số của lần ghi nhận gần nhất và nên nói kèm thời điểm đó; "
    "nó có thể đã cũ vài ngày.",
    "Lịch sử chỉ số chỉ ghi thêm. Không sửa, không xoá bản ghi cũ; muốn sửa số sai thì "
    "ghi nhận một bản mới. Hai lần ghi nhận thủ công không được trùng đúng một thời "
    "điểm trên cùng một kênh.",
    "So sánh chỉ số trong hệ thống là 'so với lần ghi trước', KHÔNG phải 'tăng trưởng "
    "30 ngày', trừ khi hai lần ghi nhận thực sự cách nhau đúng như vậy.",
    "Chỉ số để trống nghĩa là KHÔNG CÓ DỮ LIỆU, không phải bằng 0. Nền tảng khác nhau "
    "công bố số liệu khác nhau, nên một kênh thiếu reach hay impressions là chuyện "
    "bình thường.",
    "Ghi nhận chỉ số kênh là việc của người quản trị kênh (quyền PR_CHANNEL_MANAGE), "
    "không phải ai xem được kênh cũng nhập được. Người nhập luôn được lưu lại.",
    "Đổi nền tảng của một kênh KHÔNG xoá và KHÔNG viết lại lịch sử chỉ số của kênh đó; "
    "các bản ghi cũ vẫn thuộc về kênh đó, đo dưới nền tảng cũ.",
)


#: How the assistant must weigh what it has been given. Stated as an ordered
#: list because the interesting failures are all conflicts: a stale assistant
#: sentence in the history against a fresh row, or the model's own idea of what
#: "derivative" means against this file.
CONTEXT_PRECEDENCE: tuple[str, ...] = (
    "Yêu cầu an toàn và quy định nền tảng.",
    "Bối cảnh nghiệp vụ chuẩn của TasksBot ở mục [CANONICAL DOMAIN CONTEXT].",
    "Dữ liệu thật của người dùng và bản ghi hiện tại ở các mục [CURRENT USER], "
    "[CURRENT OBJECT], [AVAILABLE ACTIONS], [RELEVANT INTERNAL RECORDS].",
    "Lịch sử hội thoại.",
    "Kiến thức chung của mô hình.",
)


#: The grounding rule itself. Written as instructions to the model because that
#: is what it is; every clause here has a test.
GROUNDING_POLICY = """\
Khi câu hỏi liên quan tới TasksBot - nghiệp vụ, thuật ngữ, quy trình, quyền, nội
dung, sản xuất, xuất bản, phái sinh, bình luận, tài nguyên, kênh, chỉ số kênh,
hoặc bản ghi nội bộ:
1. Dùng bối cảnh TasksBot ở trên trước tiên.
2. Không thay luật TasksBot bằng suy đoán chung của ngành.
3. Không bịa bản ghi, trạng thái, quyền, hành động hay kết quả.
4. Nếu dữ liệu cần thiết không có trong bối cảnh, hãy nói thẳng là chưa có -
   ví dụ "Mình chưa có dữ liệu đó trong bối cảnh hiện tại." Không đoán.
5. Không ngụ ý rằng bạn đã tra cứu toàn hệ thống. Bạn chỉ thấy đúng những gì
   được đưa vào bối cảnh này.
6. Chỉ dùng kiến thức chung khi người dùng rõ ràng hỏi ngoài phạm vi TasksBot.
7. Chỉ nói một kênh đang tự đồng bộ khi bối cảnh của CHÍNH kênh đó cho thấy có
   kết nối còn hiệu lực. Có trình kết nối cho YouTube, Facebook, Instagram và
   TikTok; TikTok chỉ đọc được số liệu cộng dồn trọn đời, CHƯA có chỉ số 7/30
   ngày - nói thẳng như vậy nếu được hỏi. Khi nói về số liệu, hãy nói rõ nó đến
   từ API hay do người nhập tay, kèm thời điểm ghi nhận gần nhất.
Thuật ngữ mơ hồ ("phái sinh", "xuất bản", "sản phẩm", "link") phải hiểu theo
định nghĩa chuẩn của TasksBot ở trên khi cuộc trò chuyện đang nói về TasksBot hoặc
về một bản ghi nội bộ.
Nói bằng từ tiếng Việt của hệ thống (vd "Sẵn sàng đăng"), không đọc mã nội bộ
(vd READY_TO_PUBLISH) trừ khi người dùng hỏi về kỹ thuật.
"""


@dataclass(frozen=True, slots=True)
class MeoBotDomainContext:
    """The canonical, versioned domain context. One instance, built once.

    A dataclass rather than a module of loose strings so the whole thing has one
    name, one version and one ``render``, and so a caller cannot pick up half of
    it.
    """

    version: str = MEOBOT_DOMAIN_CONTEXT_VERSION
    glossary: tuple[DomainTerm, ...] = GLOSSARY
    stages: tuple[PrWorkflowStage, ...] = CANONICAL_STAGES
    historical_rules: tuple[str, ...] = HISTORICAL_RULES
    precedence: tuple[str, ...] = CONTEXT_PRECEDENCE
    grounding_policy: str = GROUNDING_POLICY

    def render_workflow(self) -> str:
        """The canonical workflow, in the enum's order, with its Vietnamese."""
        arrow = "\n  → ".join(f"{stage.value} ({stage_label(stage)})" for stage in self.stages)
        return (
            f"Quy trình chuẩn (đúng thứ tự):\n  {arrow}\n"
            f"{PrWorkflowStage.CANCELLED.value} ({stage_label(PrWorkflowStage.CANCELLED)}) là "
            "điểm kết thúc thay thế cho công việc bị bỏ trước khi đăng - không phải một bước "
            "trong hàng trên.\n"
            "Không tự nghĩ ra bước nào khác từ nhãn trên giao diện."
        )

    def render(self) -> str:
        """The whole canonical block, in a fixed order.

        Fixed because a stable layout is what lets a model - and a person
        reading a log - find a definition without hunting for it, which is the
        same reason the prompt's own section order is fixed.
        """
        blocks = [
            f"Phiên bản bối cảnh: {self.version}",
            "TasksBot là trợ lý vận hành BÊN TRONG hệ thống TasksBot - hệ thống quản lý "
            "nội dung và truyền thông (PR) của phòng. Mọi thuật ngữ dưới đây là nghĩa "
            "chuẩn trong hệ thống này.",
            self.render_workflow(),
            "Từ điển nghiệp vụ:\n" + "\n".join(term.render() for term in self.glossary),
            "Luật lịch sử không được khuyên trái:\n"
            + "\n".join(f"- {rule}" for rule in self.historical_rules),
            "Thứ tự ưu tiên khi có mâu thuẫn:\n"
            + "\n".join(f"{index}. {item}" for index, item in enumerate(self.precedence, start=1)),
            self.grounding_policy.strip(),
        ]
        return "\n\n".join(blocks)


#: The one instance. Imported rather than constructed, so "the canonical
#: context" is a thing with an identity rather than a shape anybody may build a
#: variant of.
MEOBOT_DOMAIN_CONTEXT = MeoBotDomainContext()


__all__: list[str] = [
    "CANONICAL_STAGES",
    "CONTEXT_PRECEDENCE",
    "GLOSSARY",
    "GROUNDING_POLICY",
    "HISTORICAL_RULES",
    "MEOBOT_DOMAIN_CONTEXT",
    "MEOBOT_DOMAIN_CONTEXT_VERSION",
    "DomainTerm",
    "MeoBotDomainContext",
]
