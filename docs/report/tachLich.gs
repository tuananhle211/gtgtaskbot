// ============================================================================
// FILE 11 — TÁCH NHỎ JOB 8H / 21H30 / 12H (chống "Hết thời gian chờ" / vượt 6 phút)
// ----------------------------------------------------------------------------
// Ý TƯỞNG: tách chuỗi nặng thành nhiều lịch nhỏ, mỗi lịch chỉ làm 1 việc gọn.
//   KHÔNG sửa logic kéo/vẽ — chỉ GỌI LẠI đúng các hàm cũ từ nhiều lịch khác nhau.
//
//   JOB 8H    -> 7h50  chay8h_1KeoSo    : kéo Meta hôm qua + chốt ngày
//                8h00  chay8h_2DoiSoat  : đối soát (đọc file ngoài)
//                8h10  chay8h_3Ve       : dựng BÁO CÁO CÁ NHÂN (vẽ nhanh)
//                8h20  chay8h_4Dash     : dựng DASHBOARD
//
//   JOB 21H30 -> 21h45 chay2130_1KeoSo  : kéo Meta hôm qua + chốt ngày
//                22h00 chay2130_2Ve     : dựng BÁO CÁO CÁ NHÂN (full)
//                22h10 chay2130_3Dash   : dựng DASHBOARD
//                22h15 chay2215_DacThu  : dựng CHI PHÍ ĐẶC THÙ (riêng, sau cùng)
//
//   CỤM 12H (tách hẳn đối soát khỏi snapshot):
//                10h30 chay1030_DoiSoat : đối soát file ngoài + BÁO CÁO CÁ NHÂN (vẽ nhanh)
//                10h40 chay1040_Dash    : dựng DASHBOARD
//                13h00 chay13_Snapshot  : kéo Meta hôm nay + dựng báo cáo khung giờ (realtime + snapshot)
//                13h20 chay1320_Telegram: gửi Telegram
//
//   TÁCH VẼ: báo cáo cá nhân và Dashboard nay là 2 lịch RIÊNG (mỗi khâu trọn 6 phút,
//            không cộng dồn gây quá hạn). Chi phí đặc thù chạy 1 lần/ngày lúc 22h15.
//
// CÀI:  capNhatLichTachNho()   (chạy tay 1 lần)
// LUI:  khoiPhucLichCu()       (trả 8h / 12h / 21h30 về gộp 1 lịch như cũ)
// KHÔNG đụng các khung khác: 7h LHĐ, 7h30 đối soát LHĐ, 17h15, 23h.
// ============================================================================

function _hqNgay() { return new Date(new Date().getTime() - 24 * 3600 * 1000); }   // hôm qua

function _veDashboard() { if (typeof dungDashboard === 'function') dungDashboard(); }   // vẽ Dashboard (khúc riêng)

// ---- JOB 8H (4 khúc) ----
function chay8h_1KeoSo()   { chayHomQua(); chotNgay('DATA_HOMQUA', _hqNgay()); }
function chay8h_2DoiSoat() { chotDoiSoat(); }
// 8h10: BÁO CÁO CÁ NHÂN — chỉ vẽ lại KHỐI THÁNG HIỆN TẠI (tháng cũ giữ nguyên -> nhanh)
function chay8h_3Ve()      { dungBaoCaoCaNhan(false, true); }
// 8h20: DASHBOARD (khúc riêng, không cộng dồn với báo cáo cá nhân)
function chay8h_4Dash()    { _veDashboard(); }

// ---- JOB 21H30 (3 khúc + đặc thù) ----
function chay2130_1KeoSo() { chayHomQua(); chotNgay('DATA_HOMQUA', _hqNgay()); }
// 22h: BÁO CÁO CÁ NHÂN dựng FULL 1 lần/ngày (chốt chuẩn + phòng lệch của bản vẽ nhanh)
function chay2130_2Ve()    { dungBaoCaoCaNhan(false, false); }
// 22h10: DASHBOARD (khúc riêng)
function chay2130_3Dash()  { _veDashboard(); }
// 22h15: CHI PHÍ ĐẶC THÙ — dựng riêng sau cùng (đã gỡ khỏi dungBaoCaoCaNhan để giảm tải khâu vẽ)
function chay2215_DacThu() { if (typeof dungChiPhiDacThu === 'function') dungChiPhiDacThu(); }

// ---- CỤM 12H (tách hẳn đối soát ra khỏi snapshot) ----
// 10h30: đối soát + BÁO CÁO CÁ NHÂN (vẽ nhanh khối tháng hiện tại)
function chay1030_DoiSoat()  { chotDoiSoat(); dungBaoCaoCaNhan(false, true); }
// 10h40: DASHBOARD (khúc riêng)
function chay1040_Dash()     { _veDashboard(); }
// 13h00: báo cáo snapshot CHỈ lấy số realtime + chụp khung giờ (nhẹ).
function chay13_Snapshot()   { chayVaBaoCao(); }           // = chayHomNay + dungBaoCaoKhungGio
// 13h20: gửi Telegram (sau khi snapshot dựng xong).
function chay1320_Telegram() { _guiTeleAnToan(); }

// 2h SÁNG: QUÉT RỘNG BÙ KHO (đêm khuya Meta rảnh nhất -> quét cả active rất nhanh, không đụng ai).
// Kéo lại hôm qua theo diện RỘNG rồi chốt -> BẮT các TK mới/nghỉ-lâu-chạy-lại mà hàm kéo chặt bỏ sót,
// GHI vào kho. Chạy TRƯỚC khung 8h nên báo cáo sáng đã đủ; hôm sau hàm kéo chặt cũng tự thấy chúng.
function chayDem_BuKho() { if (typeof chayHomQuaFull === 'function') chayHomQuaFull(); }

// Xoá mọi trigger có tên hàm nằm trong danh sách (chỉ đụng đúng các hàm liệt kê).
function _xoaTriggerTheoTen(tenList) {
  var n = 0;
  ScriptApp.getProjectTriggers().forEach(function(t){
    if (tenList.indexOf(t.getHandlerFunction()) >= 0) { ScriptApp.deleteTrigger(t); n++; }
  });
  return n;
}

// Danh sách MỌI hàm do File 11 quản lý (để gỡ sạch trước khi cài lại -> không nhân đôi).
var _DS_HAM_TACH = [
  'chay8hSang', 'chay21h30', 'chay12h',
  'chay8h_1KeoSo', 'chay8h_2DoiSoat', 'chay8h_3Ve', 'chay8h_4Dash',
  'chay2130_1KeoSo', 'chay2130_2Ve', 'chay2130_3Dash', 'chay2215_DacThu',
  'chay1030_DoiSoat', 'chay1040_Dash', 'chay13_Snapshot', 'chay1320_Telegram',
  'chayDem_BuKho', 'chay2330_BuKho'
];

// ============================================================================
// CÀI LỊCH TÁCH NHỎ — chỉ thay đổi lịch của 8h, 21h30, 12h. Giữ nguyên mọi lịch khác.
// Chạy lại nhiều lần vẫn an toàn (tự gỡ bản cũ trước khi tạo -> không nhân đôi).
// ============================================================================
function capNhatLichTachNho() {
  // 1) Gỡ lịch của đúng các hàm bị thay (gồm cả bản tách cũ nếu đã cài).
  _xoaTriggerTheoTen(_DS_HAM_TACH);

  // 2) JOB 8H -> 4 khúc: 7h50 / 8h00 / 8h10 (báo cáo cá nhân) / 8h20 (Dashboard)
  ScriptApp.newTrigger('chay8h_1KeoSo').timeBased().everyDays(1).atHour(7).nearMinute(50).create();
  ScriptApp.newTrigger('chay8h_2DoiSoat').timeBased().everyDays(1).atHour(8).nearMinute(0).create();
  ScriptApp.newTrigger('chay8h_3Ve').timeBased().everyDays(1).atHour(8).nearMinute(10).create();
  ScriptApp.newTrigger('chay8h_4Dash').timeBased().everyDays(1).atHour(8).nearMinute(20).create();

  // 3) JOB 21H30 -> 21h45 (kéo) / 22h00 (báo cáo cá nhân) / 22h10 (Dashboard) / 22h15 (chi phí đặc thù)
  ScriptApp.newTrigger('chay2130_1KeoSo').timeBased().everyDays(1).atHour(21).nearMinute(45).create();
  ScriptApp.newTrigger('chay2130_2Ve').timeBased().everyDays(1).atHour(22).nearMinute(0).create();
  ScriptApp.newTrigger('chay2130_3Dash').timeBased().everyDays(1).atHour(22).nearMinute(10).create();
  ScriptApp.newTrigger('chay2215_DacThu').timeBased().everyDays(1).atHour(22).nearMinute(15).create();

  // 4) CỤM 12H -> đối soát 10h30 (+ báo cáo cá nhân) · Dashboard 10h40 · snapshot 13h · Telegram 13h20
  ScriptApp.newTrigger('chay1030_DoiSoat').timeBased().everyDays(1).atHour(10).nearMinute(30).create();
  ScriptApp.newTrigger('chay1040_Dash').timeBased().everyDays(1).atHour(10).nearMinute(40).create();
  ScriptApp.newTrigger('chay13_Snapshot').timeBased().everyDays(1).atHour(13).nearMinute(0).create();
  ScriptApp.newTrigger('chay1320_Telegram').timeBased().everyDays(1).atHour(13).nearMinute(20).create();

  // 5) QUÉT RỘNG BÙ KHO lúc 2h SÁNG (Meta rảnh nhất) -> bắt TK mới/nghỉ-lâu vào kho, chạy trước khung 8h
  ScriptApp.newTrigger('chayDem_BuKho').timeBased().everyDays(1).atHour(2).nearMinute(0).create();

  SpreadsheetApp.getActiveSpreadsheet().toast(
    'ĐÃ CÀI: bù kho 2h · 8h→7h50/8h/8h10(bc)/8h20(dash) · 21h30→21h45/22h(bc)/22h10(dash)/22h15(đặc thù) · 10h30 đối soát/10h40 dash · snapshot 13h · Telegram 13h20. Khung 7h/7h30/17h15/23h giữ nguyên.',
    'Xong', 14);
}

// ============================================================================
// ĐƯỜNG LUI — trả 8h / 12h / 21h30 về nguyên bản (gộp 1 lịch như cũ).
// ============================================================================
function khoiPhucLichCu() {
  _xoaTriggerTheoTen(_DS_HAM_TACH);
  ScriptApp.newTrigger('chay8hSang').timeBased().everyDays(1).atHour(8).nearMinute(0).create();
  ScriptApp.newTrigger('chay21h30').timeBased().everyDays(1).atHour(21).nearMinute(30).create();
  ScriptApp.newTrigger('chay12h').timeBased().everyDays(1).atHour(12).nearMinute(0).create();
  SpreadsheetApp.getActiveSpreadsheet().toast('ĐÃ KHÔI PHỤC lịch cũ: 8h · 12h · 21h30 (gộp 1 lịch).', 'Xong', 10);
}

// ============================================================================
// KIỂM TRA NHANH — liệt kê tên hàm của toàn bộ trigger hiện có ra Nhật ký.
// Chạy để xác nhận sau khi cài: các khung khác còn nguyên, 8h/21h30/12h đã tách.
// ============================================================================
function xemLichHienTai() {
  var dg = ScriptApp.getProjectTriggers().map(function(t){ return t.getHandlerFunction(); });
  dg.sort();
  Logger.log('Tổng ' + dg.length + ' trigger:\n- ' + dg.join('\n- '));
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã ghi danh sách ' + dg.length + ' trigger ra Nhật ký thực thi', 'Xem lịch', 8);
}