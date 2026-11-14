## Thay đổi gì (ngôn ngữ nghiệp vụ)
<!-- Ai làm gì, ở màn nào, kết quả khác trước ra sao. 1-3 câu. -->

## Mức rủi ro
<!-- Xanh / Vàng / Đỏ theo skill apm-change, kèm lý do. Đỏ: link/ghi chú kế hoạch đã được chủ dự án duyệt. -->

## Quy tắc nghiệp vụ thay đổi
<!-- Trước → sau, mã lỗi mới. "Không đổi" nếu chỉ là sửa lỗi/giao diện. -->

## Kiểm tra
- [ ] `python3 scripts/check_changes.py --base origin/dev` → ALL GREEN
- [ ] Thay đổi nhiều module / vùng đỏ: `--full` → ALL GREEN
- [ ] Giao diện: ảnh `shoot.sh` sáng + tối (đính kèm)
- [ ] Migration: `alembic upgrade head` trên DB local có dữ liệu, đếm hàng trước/sau
- [ ] Review `apm-reviewer`: đã xử lý các phát hiện

## Tài liệu
- [ ] `docs/modules/<module>.md`: Quy tắc nghiệp vụ hiện hành + 1 dòng Mốc thay đổi
- [ ] Migration mới → `Migration head` trong `CLAUDE.md`
<!-- Nếu hành vi không đổi nên không sửa doc, thêm một dòng riêng:
docs-ok: <lý do>
-->

## Giả định / chưa chắc / cần người duyệt
