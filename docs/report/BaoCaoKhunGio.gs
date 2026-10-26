// ===== BÁO CÁO KHUNG GIỜ (trình BGĐ) =====
// Marketing chạy NGANG (cột), chỉ số chạy DỌC (hàng)

function chayVaBaoCao() {
  chayHomNay();
  dungBaoCaoKhungGio();
}

// Gọi Telegram an toàn: chưa cài file Telegram thì tự bỏ qua, không làm kẹt báo cáo
function _guiTeleAnToan() { try { if (typeof guiTelegram === 'function') guiTelegram(); } catch (e) {} }

// Mốc 12h: chạy khung giờ + kéo đối soát mới (Sếp update khách ~9-10h sáng) + gửi Telegram
function chay12h() {
  chayVaBaoCao();
  capNhatDoiSoat();          // đã bao gồm dựng lại dashboard
  _guiTeleAnToan();
}

// Mốc 23h: chạy khung giờ + gửi Telegram (GIỮ NGUYÊN — 23h off-peak, không cần retry)
function chayKhungGioVaGui() {
  chayVaBaoCao();
  _guiTeleAnToan();
}

// ⭐ MỐC 17h15 RIÊNG (giờ cao điểm cuối ngày, cả team bật tool -> Meta chặn token báo cáo).
// Thử kéo tối đa 3 lần, NGHỈ 90s giữa mỗi lần cho Meta hồi hạn mức, tới khi DATA_TONG CÓ SỐ mới dừng,
// rồi mới dựng báo cáo + gửi Telegram. CHỈ tác động riêng 17h15; các khung khác không đổi.
function chay17h15() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  for (let lan = 0; lan < 3; lan++) {
    if (lan > 0) Utilities.sleep(90000);          // nghỉ 90s cho Meta hồi hạn mức rồi thử lại
    try { chayHomNay(); } catch (e) {}            // kéo snapshot (hàm chung, không sửa)
    const sh = ss.getSheetByName('DATA_TONG');
    let coSo = false;
    if (sh && sh.getLastRow() > 1) {
      const d = sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues();
      coSo = d.some(function(r){ return String(r[1] || '').trim() !== ''; });
    }
    if (coSo) break;                              // đã lấy được số -> thôi thử
  }
  dungBaoCaoKhungGio();                           // có số thì dựng mới; rỗng thì lưới an toàn giữ bảng cũ
  _guiTeleAnToan();
}

// ⚙️ CHẠY TAY 1 LẦN: tách lịch 17h15 sang hàm chay17h15 (có retry), giữ 23h ở chayKhungGioVaGui.
// KHÔNG đụng lịch 8h/12h/21h30/LHĐ. Chạy lại nhiều lần cũng an toàn (tự xoá cũ, tạo lại, không nhân đôi).
function capNhatLich17h15() {
  ScriptApp.getProjectTriggers().forEach(function(t){
    const f = t.getHandlerFunction();
    if (f === 'chayKhungGioVaGui' || f === 'chay17h15') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('chay17h15').timeBased().everyDays(1).atHour(17).nearMinute(15).create();
  ScriptApp.newTrigger('chayKhungGioVaGui').timeBased().everyDays(1).atHour(23).nearMinute(0).create();
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã tách: 17h15 → chay17h15 (retry) · 23h giữ nguyên', 'Xong', 8);
}

function dungBaoCaoKhungGio() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // 1) Danh sách marketing
  const ds = ss.getSheetByName('DANH_SACH_MKT');
  const mktList = [];
  if (ds) {
    const d = ds.getDataRange().getValues();
    for (let i = 1; i < d.length; i++) {
      const ten = String(d[i][0] || '').trim(); if (!ten) continue;
      const keys = String(d[i][1] || '').split(',').map(function(k){ return chuanHoaTxt(k); }).filter(Boolean);
      keys.push(chuanHoaTxt(ten)); // tên hiển thị cũng là 1 từ khóa (đồng bộ với báo cáo cá nhân)
      mktList.push({ ten: ten, keys: keys });
    }
  }

  // 2) Gộp DATA_TONG theo marketing
  const src = ss.getSheetByName('DATA_TONG');
  const data = src ? src.getDataRange().getValues() : [];
  const tong = {};
  for (let i = 1; i < data.length; i++) {
    const id = String(data[i][1] || '').trim();
    if (!id) continue;
    if (typeof _mucDichDacThu === 'function' && _mucDichDacThu(data[i][0])) continue;   // TK đặc thù -> KHÔNG tính vào khung giờ
    const ch = chuanHoaTxt(data[i][0]);
    let matched = [];
    mktList.forEach(function(m){ if (m.keys.some(function(k){ return ch.indexOf(k) >= 0; })) matched.push(m.ten); });
    const mk = matched.length === 1 ? matched[0] : (matched.length === 0 ? 'Chưa gán' : '⚠️ Trùng');
    // GPI-không-SG CỦA MARKETING PHẠM NGỌC THẮNG -> KHÔNG tính vào khung giờ (marketing khác vẫn tính)
    const _locGPI = (typeof _loaiKhoiTong === 'function')
      ? _loaiKhoiTong(mk, data[i][0])
      : (ch.indexOf('GPI') >= 0 && ch.indexOf('SG') < 0 && chuanHoaTxt(mk).indexOf('THANG') >= 0);
    if (_locGPI) continue;
    const spend = Number(data[i][2] || 0);
    const sdt   = Number(data[i][5] || 0);
    if (!tong[mk]) tong[mk] = { tong: 0, coremind: 0, apex: 0, gpi: 0, sdt: 0 };
    tong[mk].tong += spend;
    tong[mk].sdt  += sdt;
    if (ch.indexOf('COREMIND') >= 0) tong[mk].coremind += spend;
    else if (ch.indexOf('GPI') >= 0) tong[mk].gpi += spend;
    else if (ch.indexOf('APEX') >= 0) tong[mk].apex += spend;
  }

  // 3) Thứ tự marketing
  const names = Object.keys(tong).sort(function(a, b){
    const sa = (a.indexOf('⚠️') >= 0 || a === 'Chưa gán') ? 1 : 0;
    const sb = (b.indexOf('⚠️') >= 0 || b === 'Chưa gán') ? 1 : 0;
    if (sa !== sb) return sa - sb;
    return a.localeCompare(b);
  });

  // ⛑️ CHỐNG LỖI DỮ LIỆU TRỐNG: nếu DATA_TONG chưa có số (giờ cao điểm Meta chặn tạm -> kéo về 0 dòng),
  // thì KHÔNG dựng lại bảng (tránh lỗi "range phải có ít nhất 1 cột") -> GIỮ NGUYÊN báo cáo cũ.
  // Lượt chạy sau (23h off-peak) tự cập nhật đủ. Nhờ vậy khung giờ cao điểm KHÔNG bao giờ báo "Không thành công".
  if (names.length === 0) {
    ss.toast('Khung giờ: chưa lấy được số (Meta bận giờ cao điểm) — GIỮ báo cáo cũ, sẽ tự cập nhật lần sau', 'Bỏ qua', 8);
    return;
  }

  // 4) Tổng
  const S = { tong: 0, coremind: 0, apex: 0, gpi: 0, sdt: 0 };
  names.forEach(function(m){ S.tong += tong[m].tong; S.coremind += tong[m].coremind; S.apex += tong[m].apex; S.gpi += (tong[m].gpi || 0); S.sdt += tong[m].sdt; });

  // 5) Bảng xoay
  const now = Utilities.formatDate(new Date(), 'Asia/Ho_Chi_Minh', 'HH:mm - dd/MM/yyyy');
  const nCol = names.length + 2;
  const row1 = new Array(nCol).fill(''); row1[1] = 'BÁO CÁO ADS SG  •  ' + now;
  const rowHead = ['CHỈ SỐ'].concat(names).concat(['TỔNG SG']);
  function hang(label, fn, totalVal) {
    const r = [label];
    names.forEach(function(m){ r.push(fn(tong[m])); });
    r.push(totalVal);
    return r;
  }
  const out = [];
  out.push(row1);
  out.push(rowHead);
  out.push(hang('Tổng ngân sách', function(t){ return t.tong; }, S.tong));
  out.push(hang('NS COREMIND',    function(t){ return t.coremind; }, S.coremind));
  out.push(hang('NS APEX',        function(t){ return t.apex; }, S.apex));
  out.push(hang('NS GPI',         function(t){ return t.gpi || 0; }, S.gpi));
  out.push(hang('Số điện thoại',  function(t){ return t.sdt; }, S.sdt));
  out.push(hang('Chi phí / SĐT',  function(t){ return t.sdt > 0 ? Math.round(t.tong / t.sdt) : 0; },
                                   S.sdt > 0 ? Math.round(S.tong / S.sdt) : 0));

  // 6) Ghi
  let sh = ss.getSheetByName('BÁO CÁO KHUNG GIỜ');
  if (!sh) sh = ss.insertSheet('BÁO CÁO KHUNG GIỜ');
  sh.clear();
  const nRow = out.length;
  const rng = sh.getRange(1, 1, nRow, nCol);
  rng.setValues(out);
  rng.setFontFamily('Arial').setVerticalAlignment('middle');

  // ---- TÔ MÀU SANG CHẢNH ----
  // Lưới mảnh nền
  rng.setBorder(true, true, true, true, true, true, '#d9dee7', SpreadsheetApp.BorderStyle.SOLID);

  // Tiêu đề
  sh.getRange(1, 1, 1, nCol).setBackground('#16233f');
  sh.getRange(1, 2, 1, nCol - 1).merge().setFontColor('#ffffff').setFontSize(14).setFontWeight('bold')
    .setHorizontalAlignment('center');
  sh.setRowHeight(1, 42);

  // Hàng tên marketing
  sh.getRange(2, 1, 1, nCol).setBackground('#2f4a7a').setFontColor('#ffffff').setFontWeight('bold')
    .setHorizontalAlignment('center').setWrap(true);
  sh.setRowHeight(2, 40);

  // Cột nhãn chỉ số
  sh.getRange(3, 1, nRow - 2, 1).setBackground('#eef2f8').setFontWeight('bold').setFontColor('#1f2a44');

  // Sọc xen kẽ + chiều cao hàng dữ liệu
  for (let r = 3; r <= nRow; r++) {
    sh.setRowHeight(r, 30);
    if ((r - 3) % 2 === 1) sh.getRange(r, 2, 1, nCol - 2).setBackground('#f6f9fc');
  }

  // Cột TỔNG SG
  sh.getRange(2, nCol, nRow - 1, 1).setBackground('#f4b400').setFontColor('#1f2a44').setFontWeight('bold');
  sh.getRange(2, nCol, 1, 1).setBackground('#db9b00');

  // Số
  sh.getRange(3, 2, nRow - 2, nCol - 1).setNumberFormat('#,##0').setHorizontalAlignment('right');

  // Hàng "Chi phí / SĐT" in đậm nổi bật
  sh.getRange(nRow, 1, 1, nCol).setFontWeight('bold');

  // Đường ngăn cách đậm dưới hàng tiêu đề marketing
  sh.getRange(2, 1, 1, nCol).setBorder(null, null, true, null, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  // Viền ngoài đậm bao cả bảng (đóng khung sang)
  rng.setBorder(true, true, true, true, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID_THICK);
  // Đường phân tách DỌC giữa từng marketing (đậm hơn lưới)
  sh.getRange(2, 2, nRow - 1, nCol - 1).setBorder(null, null, null, null, true, null, '#8ea3c4', SpreadsheetApp.BorderStyle.SOLID_MEDIUM);

  sh.setColumnWidth(1, 155);
  for (let c = 2; c <= nCol; c++) sh.setColumnWidth(c, 132);
  sh.setFrozenRows(2);
  sh.setFrozenColumns(1);

  ss.toast('Đã dựng BÁO CÁO KHUNG GIỜ', 'OK', 5);
}

// ⭐ CHẠY 1 LẦN DUY NHẤT ĐỂ GẮN HẾT LỊCH TỰ ĐỘNG (8h · 12h · 17h15 · 23h)
function caiDatToanBoLich() {
  const rm = ['capNhatBaoCao', 'chayHomNay', 'chayHomQua', 'chayVaBaoCao', 'chay12h', 'chay8hSang', 'chayKhungGioVaGui'];
  ScriptApp.getProjectTriggers().forEach(function(t){
    if (rm.indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('chay8hSang').timeBased().everyDays(1).atHour(8).nearMinute(0).create();           // 8h: chốt hôm qua + báo cáo cá nhân
  ScriptApp.newTrigger('chay12h').timeBased().everyDays(1).atHour(12).nearMinute(0).create();             // 12h: khung giờ + đối soát + Telegram
  ScriptApp.newTrigger('chayKhungGioVaGui').timeBased().everyDays(1).atHour(17).nearMinute(15).create();  // 17h15: khung giờ + Telegram
  ScriptApp.newTrigger('chayKhungGioVaGui').timeBased().everyDays(1).atHour(23).nearMinute(0).create();   // 23h: khung giờ + Telegram
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã gắn lịch: 8h · 12h · 17h15 · 23h (kèm Telegram)', 'Xong', 8);
}

function taoLichKhungGio() {
  ScriptApp.getProjectTriggers().forEach(function(t){
    const f = t.getHandlerFunction();
    if (f === 'capNhatBaoCao' || f === 'chayHomNay' || f === 'chayVaBaoCao' || f === 'chay12h') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('chay12h').timeBased().everyDays(1).atHour(12).nearMinute(0).create();       // 12h: khung giờ + đối soát
  ScriptApp.newTrigger('chayVaBaoCao').timeBased().everyDays(1).atHour(17).nearMinute(15).create();
  ScriptApp.newTrigger('chayVaBaoCao').timeBased().everyDays(1).atHour(23).nearMinute(0).create();
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã gắn lịch 12h(+đối soát) · 17h15 · 23h', 'Xong', 6);
}

function chuanHoaTxt(s) {
  s = String(s).toUpperCase();
  s = s.replace(/[ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬ]/g,'A')
       .replace(/[ÈÉẺẼẸÊỀẾỂỄỆ]/g,'E')
       .replace(/[ÌÍỈĨỊ]/g,'I')
       .replace(/[ÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ]/g,'O')
       .replace(/[ÙÚỦŨỤƯỪỨỬỮỰ]/g,'U')
       .replace(/[ỲÝỶỸỴ]/g,'Y')
       .replace(/Đ/g,'D');
  return s.replace(/\s+/g,' ').trim();
}