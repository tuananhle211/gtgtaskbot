// ============================================================================
// FILE 12 — TẮT / BẬT LẠI BÁO CÁO KHUNG GIỜ + GỬI TELEGRAM
// ----------------------------------------------------------------------------
//   tatKhungGioTelegram()    -> DỪNG: xoá mọi trigger khung giờ + Telegram (13h · 13h20 · 17h15 · 23h...).
//   batLaiKhungGioTelegram() -> BẬT LẠI đúng lịch khung giờ + Telegram như trước.
//   xemLichKhungGio()        -> xem nhanh các trigger khung giờ/Telegram đang có.
//
// CHỈ đụng nhóm khung giờ + Telegram. KHÔNG đụng các khung khác:
//   8h / 10h30 / 22h (báo cáo cá nhân + Dashboard), 7h / 7h30 (lập hoá đơn), 2h (bù kho).
// ============================================================================

// Mọi hàm thuộc nhóm KHUNG GIỜ + TELEGRAM (để gỡ sạch, không nhân đôi).
var _DS_HAM_KHUNGGIO = [
  'chay13_Snapshot', 'chay1320_Telegram',
  'chay17h15', 'chayKhungGioVaGui', 'chayVaBaoCao', 'chay12h'
];

function _xoaTrigKhungGio(tenList) {
  var n = 0;
  ScriptApp.getProjectTriggers().forEach(function(t){
    if (tenList.indexOf(t.getHandlerFunction()) >= 0) { ScriptApp.deleteTrigger(t); n++; }
  });
  return n;
}

// ⛔ TẮT báo cáo khung giờ + Telegram (xoá các trigger liên quan). Chạy tay 1 lần.
function tatKhungGioTelegram() {
  var n = _xoaTrigKhungGio(_DS_HAM_KHUNGGIO);
  SpreadsheetApp.getActiveSpreadsheet().toast(
    'ĐÃ TẮT báo cáo khung giờ + Telegram (gỡ ' + n + ' trigger). Khung 8h/10h30/22h · 7h/7h30 · 2h giữ nguyên.',
    'Đã tắt', 10);
}

// ✅ BẬT LẠI khung giờ + Telegram: 13h snapshot · 13h20 Telegram · 17h15 (retry) · 23h.
// Chạy lại nhiều lần vẫn an toàn (tự gỡ cũ trước khi tạo -> không nhân đôi).
function batLaiKhungGioTelegram() {
  _xoaTrigKhungGio(_DS_HAM_KHUNGGIO);
  ScriptApp.newTrigger('chay13_Snapshot').timeBased().everyDays(1).atHour(13).nearMinute(0).create();
  ScriptApp.newTrigger('chay1320_Telegram').timeBased().everyDays(1).atHour(13).nearMinute(20).create();
  ScriptApp.newTrigger('chay17h15').timeBased().everyDays(1).atHour(17).nearMinute(15).create();
  ScriptApp.newTrigger('chayKhungGioVaGui').timeBased().everyDays(1).atHour(23).nearMinute(0).create();
  SpreadsheetApp.getActiveSpreadsheet().toast('ĐÃ BẬT LẠI khung giờ + Telegram: 13h · 13h20 · 17h15 · 23h.', 'Đã bật', 10);
}

// Xem các trigger khung giờ + Telegram đang có (ghi ra Nhật ký thực thi).
function xemLichKhungGio() {
  var ds = ScriptApp.getProjectTriggers()
    .filter(function(t){ return _DS_HAM_KHUNGGIO.indexOf(t.getHandlerFunction()) >= 0; })
    .map(function(t){ return t.getHandlerFunction(); });
  ds.sort();
  Logger.log('Trigger khung giờ + Telegram đang có (' + ds.length + '):\n- ' + ds.join('\n- '));
  SpreadsheetApp.getActiveSpreadsheet().toast('Có ' + ds.length + ' trigger khung giờ/Telegram (xem Nhật ký).', 'Xem lịch', 8);
}