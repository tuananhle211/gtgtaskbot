// ===== LẤY GIAO DỊCH THANH TOÁN META (theo từng tài khoản) =====
// Cơ chế (đúng cách openclaw đã chạy được):
//   GET act_{id}/activities?fields=event_time,event_type,extra_data,translated_event_type&since=..&until=..&limit=100
//   -> lọc event_type = 'ad_account_billing_charge'
//   -> ID giao dịch = extra_data.transaction_id | Ngày = event_time (GMT+7) | Số tiền = extra_data.new_value
// BỔ SUNG THỬ: 4 số cuối thẻ (cấp tài khoản, qua funding_source_details) + dò mã tham chiếu trong extra_data.
// Chạy ĐỘC LẬP, không đụng hàm/tự động nào khác. Cần ACCESS_TOKEN đủ quyền.

// ⭐ SOÁT NHANH: /me/adaccounts đang trả về những TK nào, theo trạng thái nào.
// Dùng để kiểm tra TK die/khoá/chưa thanh toán CÓ nằm trong danh sách API trả về không.
// Ghi ra tab SOAT_TK. Chạy: soatTKTheoStatus()
function soatTKTheoStatus() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ten = { 1: 'Đang hoạt động', 2: 'Bị vô hiệu (die)', 3: 'Chưa thanh toán', 7: 'Chờ review rủi ro',
    8: 'Chờ thanh toán', 9: 'Thời gian ân hạn', 100: 'Chờ đóng', 101: 'Đã đóng', 201: 'Any active', 202: 'Any closed' };
  let accs;
  try {
    accs = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { SpreadsheetApp.getActiveSpreadsheet().toast('Lỗi: ' + e.message, 'Soát TK', 8); return; }
  const dem = {};
  const rows = accs.map(function(a){
    const s = a.account_status; dem[s] = (dem[s] || 0) + 1;
    return [a.name, String(a.account_id), s, ten[s] || ('Khác (' + s + ')')];
  });
  const tomtat = 'Tổng /me/adaccounts: ' + accs.length + '  —  ' +
    Object.keys(dem).sort(function(a, b){ return a - b; }).map(function(s){ return (ten[s] || s) + ': ' + dem[s]; }).join(' · ');
  let sh = ss.getSheetByName('SOAT_TK'); if (!sh) sh = ss.insertSheet('SOAT_TK');
  sh.clear();
  sh.getRange(1, 1).setValue(tomtat).setFontWeight('bold').setBackground('#16233f').setFontColor('#fff');
  sh.getRange(1, 1, 1, 4).merge();
  sh.getRange(3, 1, 1, 4).setValues([['Tên TK', 'ID TKQC', 'Mã status', 'Trạng thái']]).setFontWeight('bold').setBackground('#3a5a99').setFontColor('#fff');
  if (rows.length) {
    sh.getRange(4, 2, rows.length, 1).setNumberFormat('@');
    SpreadsheetApp.flush();
    sh.getRange(4, 1, rows.length, 4).setValues(rows);
  }
  sh.setColumnWidth(1, 320); sh.setColumnWidth(2, 175); sh.setColumnWidth(3, 90); sh.setColumnWidth(4, 200);
  sh.setFrozenRows(3);
  ss.toast(tomtat, 'Soát TK', 15);
  Logger.log(tomtat);
}

function thuLayGiaoDich() {
  const tz = 'Asia/Ho_Chi_Minh', now = new Date();
  const since = Utilities.formatDate(new Date(now.getFullYear(), now.getMonth(), 1), tz, 'yyyy-MM-dd');
  const until = Utilities.formatDate(now, tz, 'yyyy-MM-dd');
  layGiaoDichKhoang(since, until, true);
}
function layGiaoDichThang(y, m) {
  const tz = 'Asia/Ho_Chi_Minh';
  const since = Utilities.formatDate(new Date(y, m - 1, 1), tz, 'yyyy-MM-dd');
  const until = Utilities.formatDate(new Date(y, m, 0), tz, 'yyyy-MM-dd');
  layGiaoDichKhoang(since, until, true);
}

// chiHomNay=true -> chỉ TK có chi tiêu hôm nay; false -> TẤT CẢ TK đang hoạt động
function layGiaoDichKhoang(since, until, chiHomNay) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tz = 'Asia/Ho_Chi_Minh';
  const today = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
  const log = []; function L(s){ log.push(s); }
  L('=== GIAO DỊCH THANH TOÁN ' + since + ' → ' + until + ' ===');

  // 1) Lấy TẤT CẢ TK (gồm die/khoá/chưa thanh toán)
  let accs;
  try {
    accs = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { L('✗ ' + e.message); Logger.log(log.join('\n')); return; }
  let dsTK = locTKCanLay(accs);   // gồm cả TK die/khoá/chưa thanh toán -> không sót số
  L('TK lấy dữ liệu (gồm die/chưa TT): ' + dsTK.length);

  // 2) lọc chi tiêu hôm nay (tuỳ chọn)
  if (chiHomNay) {
    const st = {};
    for (let i = 0; i < dsTK.length; i += 50) {
      const nhom = dsTK.slice(i, i + 50);
      const batch = nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id +
        '/insights?fields=spend&time_range=' + encodeURIComponent(JSON.stringify({ since: today, until: today })) + '&limit=1' }; });
      let res; try { res = goiBatch(batch); } catch (e) { continue; }
      res.forEach(function(r, idx){ if (!r || r.code !== 200 || !r.body) return; let b; try { b = JSON.parse(r.body); } catch (e) { return; }
        if (b.data && b.data.length && Number(b.data[0].spend || 0) > 0) st[nhom[idx].account_id] = 1; });
    }
    dsTK = dsTK.filter(function(a){ return st[a.account_id]; });
    L('→ TK có chi tiêu hôm nay: ' + dsTK.length);
  }

  // 3) LẤY 4 SỐ CUỐI THẺ (cấp tài khoản) qua funding_source_details
  const cardMap = {};
  for (let i = 0; i < dsTK.length; i += 50) {
    const nhom = dsTK.slice(i, i + 50);
    const batch = nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id + '?fields=funding_source_details' }; });
    let res; try { res = goiBatch(batch); } catch (e) { L('  ⚠️ Batch funding_source lỗi: ' + e.message); continue; }
    res.forEach(function(r, idx){
      if (!r || r.code !== 200 || !r.body) return;
      let b; try { b = JSON.parse(r.body); } catch (e) { return; }
      if (idx === 0) L('   [MẪU funding_source_details] ' + JSON.stringify(b.funding_source_details || {}));
      cardMap[nhom[idx].account_id] = _card4(b.funding_source_details);
    });
  }

  // 4) Lấy billing charges từng TK qua /activities
  const rows = []; let mau = 0, ok = 0, loi = 0;
  dsTK.forEach(function(a){
    let acts;
    try {
      acts = goiAPI('https://graph.facebook.com/' + API_VERSION + '/act_' + a.account_id +
        '/activities?fields=event_time,event_type,extra_data,translated_event_type' +
        '&since=' + since + '&until=' + until + '&limit=100&access_token=' + encodeURIComponent(ACCESS_TOKEN));
    } catch (e) { loi++; L('   ✗ ' + a.name + ': ' + e.message); return; }
    ok++;
    const seen = {}; let cnt = 0;
    acts.forEach(function(ev){
      if (ev.event_type !== 'ad_account_billing_charge') return;
      let ex = {}; try { ex = (typeof ev.extra_data === 'string') ? JSON.parse(ev.extra_data) : (ev.extra_data || {}); } catch (e) { ex = {}; }
      const txId = ex.transaction_id || '';
      if (txId && seen[txId]) return; if (txId) seen[txId] = 1;
      const soTien = (ex.new_value != null) ? Number(ex.new_value) : (ex.amount != null ? Number(ex.amount) : '');
      // DÒ mã tham chiếu trong nhiều tên trường có thể có
      const maTC = ex.reference || ex.billing_reference || ex.reference_number || ex.transaction_reference ||
                   ex.provider_transaction_id || ex.provider_amount_reference || ex.receipt_id || ex.receipt ||
                   ex.charge_id || ex.provider_charge_id || ex.reference_id || ex.reference_code || '';
      const ngay = _doiGioGMT7(ev.event_time, tz);
      if (mau < 2) { L('   [FULL activity · ' + a.name + '] ' + JSON.stringify(ev)); mau++; }   // dump NGUYÊN object để soi mọi trường
      rows.push([a.name, 'act_' + a.account_id, txId, ngay, soTien, cardMap[a.account_id] || '', maTC, JSON.stringify(ex)]);
      cnt++;
    });
    L('   ✓ ' + a.name + ': ' + cnt + ' giao dịch (thẻ ' + (cardMap[a.account_id] || '?') + ')');
  });

  // 5) Ghi sheet
  let sh = ss.getSheetByName('GIAO_DICH_META'); if (!sh) sh = ss.insertSheet('GIAO_DICH_META');
  sh.clear();
  const header = ['Tài khoản', 'act_id', 'ID giao dịch', 'Ngày (GMT+7)', 'Số tiền', '4 số cuối thẻ', 'Mã tham chiếu (thử)', 'extra_data (đầy đủ - chẩn đoán)'];
  sh.getRange(1, 1, 1, header.length).setValues([header]).setBackground('#16233f').setFontColor('#fff').setFontWeight('bold');
  if (rows.length) {
    sh.getRange(2, 1, rows.length, header.length).setValues(rows);
    sh.getRange(2, 5, rows.length, 1).setNumberFormat('#,##0');
    sh.getRange(2, 3, rows.length, 1).setNumberFormat('@');
    sh.getRange(2, 6, rows.length, 1).setNumberFormat('@');
  }
  sh.setColumnWidth(1, 240); sh.setColumnWidth(2, 150); sh.setColumnWidth(3, 300);
  sh.setColumnWidth(4, 160); sh.setColumnWidth(5, 130); sh.setColumnWidth(6, 110); sh.setColumnWidth(7, 200);
  sh.setColumnWidth(8, 520);
  sh.setFrozenRows(1);

  L('=== ' + rows.length + ' giao dịch (' + ok + ' TK ok, ' + loi + ' lỗi) → GIAO_DICH_META ===');
  L('>> Soi các dòng [FULL extra_data] ở trên: nếu THẤY trường mã tham chiếu -> báo em tên trường, em map vào cột.');
  Logger.log(log.join('\n'));
  ss.toast('Xong: ' + rows.length + ' giao dịch. Xem GIAO_DICH_META + Nhật ký (có dump extra_data).', 'Giao dịch', 8);
}

// 4 số cuối thẻ từ funding_source_details.display_string (robust nhiều định dạng)
function _card4(fsd) {
  if (!fsd) return '';
  const s = String(fsd.display_string || fsd.display || fsd.card_last4 || '').trim();
  if (!s) return '';
  // 1) 4 số ngay SAU ký tự che (• * x . · hoặc khoảng trắng) -> kiểu "•••• 4037", "xxxx 4037"
  let m = s.match(/[•*xX·.\-\s]\s*(\d{4})\b/);
  if (m) return m[1];
  // 2) nhóm ĐÚNG 4 số đứng riêng (không thuộc số dài hơn) -> lấy nhóm CUỐI
  const all = s.match(/\b\d{4}\b/g);
  if (all && all.length) return all[all.length - 1];
  // 3) cuối cùng: 4 số cuối của toàn bộ chữ số
  const digits = s.replace(/\D/g, '');
  return digits.length >= 4 ? digits.slice(-4) : '';
}

// event_time UTC -> GMT+7 dd/MM/yyyy HH:mm:ss
function _doiGioGMT7(iso, tz) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return Utilities.formatDate(d, tz || 'Asia/Ho_Chi_Minh', 'dd/MM/yyyy HH:mm:ss');
}


// ===== TAB "LHĐ": CHI TIẾT LẬP HOÁ ĐƠN THEO MARKETING (theo ngày trong tháng) =====
// Độc lập, CHỈ ghi vào tab "LHĐ". Không đụng cấu trúc file. Dùng chung ganMKT/docDSMKT để lọc đúng MKT.
function dungLHD_Huy_T7() { dungLHD('Ngô Trọng Huy', 2026, 7); }

function dungLHD(tenMKT, year, month) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tz = 'Asia/Ho_Chi_Minh';
  const nDays = new Date(year, month, 0).getDate();     // số ngày trong tháng
  const target = chuanHoaTxt(tenMKT);
  const log = []; function L(s){ log.push(s); }
  function p2(n){ return ('0' + n).slice(-2); }
  L('=== LHĐ ' + tenMKT + ' — Tháng ' + month + '/' + year + ' ===');

  // 1) Tài khoản đang hoạt động
  let accs;
  try {
    accs = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { L('✗ ' + e.message); Logger.log(log.join('\n')); return; }
  const active = locTKCanLay(accs);   // gồm cả TK die/khoá/chưa thanh toán -> không sót số

  // 2) Lọc TK thuộc MKT (dùng ganMKT giống toàn hệ thống)
  const mktList = docDSMKT(ss);
  const mine = active.filter(function(a){ return chuanHoaTxt(ganMKT(chuanHoaTxt(a.name), mktList)) === target; });
  L('TK thuộc ' + tenMKT + ': ' + mine.length);
  mine.forEach(function(a){ L('   • ' + a.name + ' (act_' + a.account_id + ')'); });
  if (!mine.length) { L('(Không tìm thấy TK nào khớp MKT này — kiểm tên trong DANH_SACH_MKT)'); Logger.log(log.join('\n')); ss.toast('Không có TK khớp ' + tenMKT, 'LHĐ', 6); return; }

  // 3) 4 số cuối thẻ (cấp TK)
  const cardMap = {};
  for (let i = 0; i < mine.length; i += 50) {
    const nhom = mine.slice(i, i + 50);
    const batch = nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id + '?fields=funding_source_details' }; });
    let res; try { res = goiBatch(batch); } catch (e) { continue; }
    res.forEach(function(r, idx){ if (!r || r.code !== 200 || !r.body) return; let b; try { b = JSON.parse(r.body); } catch (e) { return; } cardMap[nhom[idx].account_id] = _card4(b.funding_source_details); });
  }

  // 4) Khoảng kéo có ĐỆM 2 đầu (bắt trọn ngày theo GMT+7), rồi lọc đúng tháng GMT+7
  const prevM = month === 1 ? 12 : month - 1, prevY = month === 1 ? year - 1 : year;
  const prevLast = new Date(prevY, prevM, 0).getDate();
  const nextM = month === 12 ? 1 : month + 1, nextY = month === 12 ? year + 1 : year;
  const since = prevY + '-' + p2(prevM) + '-' + p2(prevLast);
  const until = nextY + '-' + p2(nextM) + '-01';

  // 5) Kéo billing charges từng TK -> cộng theo NGÀY (GMT+7)
  const data = {}; // act_id -> {ten, id, card, byDay[], tong}
  mine.forEach(function(a){
    const rec = { ten: a.name, id: a.account_id, card: cardMap[a.account_id] || '', byDay: {}, tong: 0 };
    for (let d = 1; d <= nDays; d++) rec.byDay[d] = 0;
    let acts;
    try {
      acts = goiAPI('https://graph.facebook.com/' + API_VERSION + '/act_' + a.account_id +
        '/activities?fields=event_time,event_type,extra_data&since=' + since + '&until=' + until +
        '&limit=100&access_token=' + encodeURIComponent(ACCESS_TOKEN));
    } catch (e) { L('   ✗ ' + a.name + ': ' + e.message); data[a.account_id] = rec; return; }
    const seen = {}; let cnt = 0;
    acts.forEach(function(ev){
      if (ev.event_type !== 'ad_account_billing_charge') return;
      let ex = {}; try { ex = (typeof ev.extra_data === 'string') ? JSON.parse(ev.extra_data) : (ev.extra_data || {}); } catch (e) { ex = {}; }
      const txId = ex.transaction_id || '';
      if (txId && seen[txId]) return; if (txId) seen[txId] = 1;
      const d = new Date(ev.event_time); if (isNaN(d.getTime())) return;
      const gy = Number(Utilities.formatDate(d, tz, 'yyyy')), gm = Number(Utilities.formatDate(d, tz, 'MM')), gd = Number(Utilities.formatDate(d, tz, 'dd'));
      if (gy !== year || gm !== month) return;                 // chỉ tính đúng tháng theo GMT+7
      const val = (ex.new_value != null) ? Number(ex.new_value) : 0;
      rec.byDay[gd] += val; rec.tong += val; cnt++;
    });
    L('   ✓ ' + a.name + ': ' + cnt + ' GD, tổng ' + Math.round(rec.tong).toLocaleString('vi-VN'));
    data[a.account_id] = rec;
  });

  // 6) Dựng bảng ra tab LHĐ
  let sh = ss.getSheetByName('LHĐ'); if (!sh) sh = ss.insertSheet('LHĐ');
  sh.clear();
  const nCol = 4 + nDays;
  const out = [];
  const titleR = ['CHI TIẾT LẬP HOÁ ĐƠN THÁNG ' + month + '/' + year + ' — MARKETING ' + tenMKT.toUpperCase()];
  while (titleR.length < nCol) titleR.push('');
  out.push(titleR);
  const hdr = ['Tên TK', 'ID TKQC', 'Số Thẻ', 'Tổng Tháng']; for (let d = 1; d <= nDays; d++) hdr.push(d);
  out.push(hdr);
  const ids = Object.keys(data);
  const tongNgay = {}; for (let d = 1; d <= nDays; d++) tongNgay[d] = 0; let grand = 0;
  ids.forEach(function(id){
    const r = data[id];
    const row = [r.ten, r.id, r.card, r.tong];
    for (let d = 1; d <= nDays; d++) { row.push(r.byDay[d] || 0); tongNgay[d] += (r.byDay[d] || 0); }
    grand += r.tong;
    out.push(row);
  });
  const tongRow = ['TỔNG', '', '', grand]; for (let d = 1; d <= nDays; d++) tongRow.push(tongNgay[d]);
  out.push(tongRow);

  const nRow = out.length;
  sh.getRange(1, 1, nRow, nCol).setValues(out);

  // Định dạng
  sh.getRange(1, 1, 1, nCol).merge().setBackground('#16233f').setFontColor('#fff').setFontWeight('bold').setFontSize(13).setHorizontalAlignment('center');
  sh.setRowHeight(1, 40);
  sh.getRange(2, 1, 1, nCol).setBackground('#2f4a7a').setFontColor('#fff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.getRange(3, 4, nRow - 2, 1 + nDays).setNumberFormat('#,##0');          // Tổng Tháng + các ngày
  sh.getRange(2, 2, nRow - 1, 1).setNumberFormat('@');                       // ID TKQC dạng text
  sh.getRange(2, 3, nRow - 1, 1).setNumberFormat('@');                       // Số thẻ dạng text
  // Ép ID TKQC về text CHẮC CHẮN (đặt '@' rồi ghi lại) -> hiện đủ số, không rút gọn/khoa học
  if (ids.length) { const idCol = ids.map(function(id){ return [String(data[id].id)]; }); sh.getRange(3, 2, idCol.length, 1).setNumberFormat('@'); sh.getRange(3, 2, idCol.length, 1).setValues(idCol); }
  sh.getRange(nRow, 1, 1, nCol).setBackground('#fff7e6').setFontWeight('bold');   // dòng TỔNG
  sh.getRange(3, 4, nRow - 3, 1).setFontWeight('bold');                      // cột Tổng Tháng in đậm
  sh.getRange(1, 1, nRow, nCol).setBorder(true, true, true, true, true, true, '#d9dee7', SpreadsheetApp.BorderStyle.SOLID);
  sh.setColumnWidth(1, 260); sh.setColumnWidth(2, 150); sh.setColumnWidth(3, 90); sh.setColumnWidth(4, 130);
  for (let c = 5; c <= nCol; c++) sh.setColumnWidth(c, 95);
  sh.setFrozenRows(2);   // chỉ cố định dòng (tiêu đề hợp nhất toàn bảng nên KHÔNG cố định cột để tránh lỗi merge)

  L('=== Xong: ' + ids.length + ' TK, tổng tháng ' + Math.round(grand).toLocaleString('vi-VN') + ' -> tab LHĐ ===');
  Logger.log(log.join('\n'));
  ss.toast('Đã dựng tab LHĐ: ' + ids.length + ' TK, tổng ' + Math.round(grand).toLocaleString('vi-VN'), 'LHĐ', 8);
}


// ===== SOI THẺ 1 TÀI KHOẢN (chẩn đoán vì sao không ra số thẻ) =====
// Chạy: soiThe('1683182232148621')  -> xem Nhật ký thực thi
function soiThe(accId) {
  accId = String(accId).replace(/^act_/, '');
  const url = 'https://graph.facebook.com/' + API_VERSION + '/act_' + accId +
    '?fields=name,account_status,currency,funding_source,funding_source_details' +
    '&access_token=' + encodeURIComponent(ACCESS_TOKEN);
  let data;
  try { data = JSON.parse(fetchRetry(url, { muteHttpExceptions: true }).getContentText()); }
  catch (e) { Logger.log('✗ Lỗi gọi API: ' + e.message); return; }

  Logger.log('=== SOI THẺ act_' + accId + ' ===');
  if (data.error) { Logger.log('✗ API TRẢ LỖI: ' + JSON.stringify(data.error)); }
  Logger.log('Tên TK       : ' + (data.name || '(không có)'));
  Logger.log('account_status: ' + data.account_status);
  Logger.log('funding_source (id): ' + (data.funding_source || '(không có)'));
  const fsd = data.funding_source_details;
  Logger.log('funding_source_details: ' + JSON.stringify(fsd || null));
  if (fsd) {
    Logger.log('   → type        : ' + (fsd.type != null ? fsd.type : '(không có)'));
    Logger.log('   → display_string: ' + (fsd.display_string || '(không có)'));
  }
  Logger.log('=> _card4 hiện tính ra: "' + _card4(fsd) + '"');
  Logger.log('(Nếu type KHÁC thẻ tín dụng, hoặc display_string không chứa 4 số -> đó là lý do trống)');
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã soi act_' + accId + ' — xem Chế độ xem > Nhật ký', 'Soi thẻ', 8);
}


// Bấm-là-chạy: soi thẻ đúng TK 1683182232148621 (không cần nhập tham số)
function soiThe_1683() { soiThe('1683182232148621'); }


// ===== QUÉT THẺ TẤT CẢ TÀI KHOẢN -> bảng THE_TKQC (fix hết vụ trống thẻ) =====
// Dựng bảng: ID TKQC | Tên TK | 4 số cuối thẻ | Loại nguồn | Mô tả nguồn | Ghi chú.
// TK trống thẻ được xếp LÊN ĐẦU + ghi rõ lý do (nguồn không phải thẻ / không đọc được / chưa gắn nguồn).
function dungBangThe() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const log = []; function L(s){ log.push(s); }
  L('=== QUÉT THẺ TẤT CẢ TÀI KHOẢN ===');

  let accs;
  try {
    accs = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { L('✗ ' + e.message); Logger.log(log.join('\n')); return; }
  const active = locTKCanLay(accs);   // gồm cả TK die/khoá/chưa thanh toán -> không sót số
  L('TK lấy dữ liệu (gồm die/chưa TT): ' + active.length);

  const rows = []; let coThe = 0, khong = 0, loi = 0;
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    const batch = nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id + '?fields=name,funding_source_details' }; });
    let res; try { res = goiBatch(batch); } catch (e) { L('  ⚠️ batch lỗi: ' + e.message); nhom.forEach(function(a){ rows.push([a.account_id, a.name, '', '', '', 'LỖI batch']); loi++; }); continue; }
    res.forEach(function(r, idx){
      const a = nhom[idx];
      if (!r || r.code !== 200 || !r.body) { rows.push([a.account_id, a.name, '', '', '', 'Không đọc được (quyền?)']); loi++; return; }
      let b; try { b = JSON.parse(r.body); } catch (e) { rows.push([a.account_id, a.name, '', '', '', 'Lỗi parse']); loi++; return; }
      const fsd = b.funding_source_details;
      const card = _card4(fsd);
      const type = (fsd && fsd.type != null) ? fsd.type : '';
      const disp = (fsd && fsd.display_string) ? fsd.display_string : '';
      let ghichu = '';
      if (card) { coThe++; }
      else { khong++; ghichu = !fsd ? 'Chưa gắn nguồn thanh toán' : (disp ? 'Nguồn không phải thẻ' : 'Nguồn không có 4 số'); }
      rows.push([a.account_id, b.name || a.name, card, type, disp, ghichu]);
    });
  }

  // TK trống thẻ lên đầu để dễ soi
  rows.sort(function(x, y){ return (x[2] ? 1 : 0) - (y[2] ? 1 : 0); });

  let sh = ss.getSheetByName('THE_TKQC'); if (!sh) sh = ss.insertSheet('THE_TKQC');
  sh.clear();
  const header = ['ID TKQC', 'Tên TK', '4 số cuối thẻ', 'Loại nguồn (type)', 'Mô tả nguồn (display_string)', 'Ghi chú'];
  sh.getRange(1, 1, 1, 6).setValues([header]).setBackground('#16233f').setFontColor('#fff').setFontWeight('bold');
  if (rows.length) {
    sh.getRange(2, 1, rows.length, 6).setValues(rows);
    // Ép ID TKQC + Số thẻ về TEXT chắc chắn (giữ đủ số, không mất số 0 đầu)
    sh.getRange(2, 1, rows.length, 1).setNumberFormat('@'); sh.getRange(2, 1, rows.length, 1).setValues(rows.map(function(r){ return [String(r[0])]; }));
    sh.getRange(2, 3, rows.length, 1).setNumberFormat('@'); sh.getRange(2, 3, rows.length, 1).setValues(rows.map(function(r){ return [String(r[2])]; }));
  }
  sh.setColumnWidth(1, 170); sh.setColumnWidth(2, 270); sh.setColumnWidth(3, 110);
  sh.setColumnWidth(4, 120); sh.setColumnWidth(5, 330); sh.setColumnWidth(6, 200);
  sh.setFrozenRows(1);

  L('Có thẻ: ' + coThe + ' | Không có thẻ: ' + khong + ' | Lỗi: ' + loi + ' | Tổng: ' + active.length);
  let dumped = 0;
  rows.forEach(function(rr){ if (!rr[2] && dumped < 12) { L('   ⋯ ' + rr[1] + ' (act_' + rr[0] + ') type=' + rr[3] + ' | disp="' + rr[4] + '" | ' + rr[5]); dumped++; } });
  Logger.log(log.join('\n'));
  ss.toast('THE_TKQC: ' + coThe + '/' + active.length + ' TK có thẻ, ' + khong + ' không có. Xem sheet + Nhật ký.', 'Quét thẻ', 10);
}


// ===== THỬ endpoint transactions + payment_reference_id (theo gợi ý Meta AI) =====
// Quét nhiều version để biết endpoint 'transactions' có tồn tại ở version nào không.
function thuTransactions() {
  const accId = '208511808777670';   // TK có nhiều giao dịch (thẻ 6688) để dễ thấy dữ liệu
  const versions = ['v20.0', 'v21.0', 'v22.0', 'v23.0', 'v19.0', 'v18.0'];
  const log = []; function L(s){ log.push(s); }
  L('=== THỬ transactions + payment_reference_id — act_' + accId + ' ===');
  versions.forEach(function(v){
    const url = 'https://graph.facebook.com/' + v + '/act_' + accId +
      '/transactions?fields=id,time,amount,payment_reference_id,status&limit=5&access_token=' + encodeURIComponent(ACCESS_TOKEN);
    let data;
    try { data = JSON.parse(fetchRetry(url, { muteHttpExceptions: true }).getContentText()); }
    catch (e) { L(v + ': ✗ Exception ' + e.message); return; }
    if (data.error) { L(v + ': ✗ ' + (data.error.message || JSON.stringify(data.error))); return; }
    L(v + ': ✓ OK — ' + (data.data ? data.data.length : 0) + ' dòng');
    if (data.data && data.data.length) L('   MẪU: ' + JSON.stringify(data.data[0]));
  });
  Logger.log(log.join('\n'));
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã thử transactions nhiều version — xem Nhật ký', 'Thử transactions', 8);
}


// ========================================================================
// PHẦN A — KHO LICH_SU_LHD: billing charge TẤT CẢ tài khoản, mỗi TK / mỗi ngày
// Ghi kiểu THAY THEO THÁNG an toàn: tháng khác + TK chưa kéo được -> GIỮ NGUYÊN.
// Cột kho: Ngày | ID TKQC | Tên TK | Marketing | Số thẻ | Số tiền
// ========================================================================
function dungKhoLHD_T7() { dungKhoLHD(2026, 7); }

function dungKhoLHD(year, month) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tz = 'Asia/Ho_Chi_Minh';
  const mktList = docDSMKT(ss);
  const log = []; function L(s){ log.push(s); }
  function p2(n){ return ('0' + n).slice(-2); }
  L('=== KHO LHĐ Tháng ' + month + '/' + year + ' — kéo billing charge TẤT CẢ tài khoản ===');

  // 1) Lấy TẤT CẢ TK (gồm die/khoá/chưa thanh toán)
  let accs;
  try {
    accs = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { L('✗ ' + e.message); Logger.log(log.join('\n')); return; }
  const active = locTKCanLay(accs);   // gồm cả TK die/khoá/chưa thanh toán -> không sót số
  L('TK lấy dữ liệu (gồm die/chưa TT): ' + active.length);

  // 2) 4 số cuối thẻ (batch)
  const cardMap = {};
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    const batch = nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id + '?fields=funding_source_details' }; });
    let res; try { res = goiBatch(batch); } catch (e) { continue; }
    res.forEach(function(r, idx){ if (!r || r.code !== 200 || !r.body) return; let b; try { b = JSON.parse(r.body); } catch (e) { return; } cardMap[nhom[idx].account_id] = _card4(b.funding_source_details); });
  }

  // 3) khoảng đệm 2 đầu -> lọc đúng tháng theo GMT+7
  const prevM = month === 1 ? 12 : month - 1, prevY = month === 1 ? year - 1 : year, prevLast = new Date(prevY, prevM, 0).getDate();
  const nextM = month === 12 ? 1 : month + 1, nextY = month === 12 ? year + 1 : year;
  const since = prevY + '-' + p2(prevM) + '-' + p2(prevLast);
  const until = nextY + '-' + p2(nextM) + '-01';

  // 4) kéo billing charge — GỘP BATCH 50 TK/lượt (chống vượt 6 phút khi nhiều TK gồm die)
  const newRows = []; const pulled = {}; let ok = 0, loi = 0, tongTien = 0;
  const relURL = function(a){ return 'act_' + a.account_id + '/activities?fields=event_time,event_type,extra_data&since=' + since + '&until=' + until + '&limit=500'; };
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    let res; try { res = goiBatch(nhom.map(function(a){ return { method: 'GET', relative_url: relURL(a) }; })); }
    catch (e) { loi += nhom.length; L('   ✗ batch ' + i + ': ' + e.message); continue; }
    res.forEach(function(r, idx){
      const a = nhom[idx];
      if (!r || r.code !== 200 || !r.body) { loi++; return; }
      let body; try { body = JSON.parse(r.body); } catch (e) { loi++; return; }
      let acts = (body && body.data) ? body.data : [];
      const _next = body.paging && body.paging.next ? body.paging.next : null;   // TK nhiều thao tác -> lấy nốt, không cắt (thiếu ngày đầu tháng)
      if (_next) { try { acts = acts.concat(goiAPI(_next)); } catch (e) {} }
      ok++; pulled[a.account_id] = 1;
      const mk = ganMKT(chuanHoaTxt(a.name), mktList);
      const card = cardMap[a.account_id] || '';
      const byDay = {}; const seen = {};
      acts.forEach(function(ev){
        if (ev.event_type !== 'ad_account_billing_charge') return;
        let ex = {}; try { ex = (typeof ev.extra_data === 'string') ? JSON.parse(ev.extra_data) : (ev.extra_data || {}); } catch (e) { ex = {}; }
        const txId = ex.transaction_id || ''; if (txId && seen[txId]) return; if (txId) seen[txId] = 1;
        const d = new Date(ev.event_time); if (isNaN(d.getTime())) return;
        const gy = Number(Utilities.formatDate(d, tz, 'yyyy')), gm = Number(Utilities.formatDate(d, tz, 'MM')), gd = Number(Utilities.formatDate(d, tz, 'dd'));
        if (gy !== year || gm !== month) return;
        const val = (ex.new_value != null) ? Number(ex.new_value) : 0;
        byDay[gd] = (byDay[gd] || 0) + val;
      });
      Object.keys(byDay).forEach(function(gd){
        newRows.push([new Date(year, month - 1, Number(gd)), a.account_id, a.name, mk, card, byDay[gd]]);
        tongTien += byDay[gd];
      });
    });
  }
  L('Kéo OK ' + ok + ' TK · lỗi ' + loi + ' TK · dòng mới ' + newRows.length + ' · tổng tiền tháng ' + Math.round(tongTien).toLocaleString('vi-VN'));

  if (!Object.keys(pulled).length) { L('Không kéo được gì -> GIỮ NGUYÊN kho'); Logger.log(log.join('\n')); ss.toast('Không kéo được — giữ nguyên kho', 'Kho LHĐ', 6); return; }

  // 5) Ghi kho: giữ dòng THÁNG KHÁC hoặc TK chưa kéo được lần này; thay dòng tháng này của TK đã kéo
  let sh = ss.getSheetByName('LICH_SU_LHD'); if (!sh) sh = ss.insertSheet('LICH_SU_LHD');
  const header = ['Ngày', 'ID TKQC', 'Tên TK', 'Marketing', 'Số thẻ', 'Số tiền'];
  const old = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, 6).getValues() : [];
  // Giữ nguyên các THÁNG KHÁC; bỏ TOÀN BỘ tháng đang kéo (rồi ghi mới) -> không trùng, không lệ thuộc so khớp ID cũ.
  const keep = old.filter(function(r){
    const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return false;   // dòng ngày rác -> bỏ
    return !(d.getFullYear() === year && d.getMonth() + 1 === month);
  }).map(function(r){ r[0] = toDate(r[0]); r[1] = String(r[1]); r[4] = (r[4] == null ? '' : String(r[4])); return r; });

  const all = keep.concat(newRows);
  sh.clear();
  sh.getRange(1, 1, 1, 6).setValues([header]);
  if (all.length) {
    // Đặt ĐỊNH DẠNG TEXT cho cột ID TKQC + Số thẻ TRƯỚC khi ghi -> KHÔNG bị số hoá/khoa học -> tránh gộp nhầm TK
    sh.getRange(2, 2, all.length, 1).setNumberFormat('@');
    sh.getRange(2, 5, all.length, 1).setNumberFormat('@');
    const norm = all.map(function(r){ return [r[0], String(r[1]), r[2], r[3], (r[4] == null ? '' : String(r[4])), r[5]]; });
    sh.getRange(2, 1, all.length, 6).setValues(norm);
    sh.getRange(2, 1, all.length, 1).setNumberFormat('dd/MM/yyyy');
    sh.getRange(2, 6, all.length, 1).setNumberFormat('#,##0');
  }
  L('=== Kho LICH_SU_LHD: ' + all.length + ' dòng (mới ' + newRows.length + ', giữ ' + keep.length + ') ===');
  Logger.log(log.join('\n'));
  ss.toast('Kho LHĐ xong: ' + newRows.length + ' dòng T' + month + ', tổng ' + Math.round(tongTien).toLocaleString('vi-VN'), 'Kho LHĐ', 10);
}

// ========================================================================
// PHẦN A' — KÉO LHĐ THEO CỬA SỔ NGÀY (nhẹ, tự lấp nếu lỡ trigger).
// Chỉ THAY dữ liệu các ngày trong cửa sổ [hôm nay lùi soNgay ... hôm nay];
// ngày ngoài cửa sổ + tháng khác GIỮ NGUYÊN. Không kéo được gì -> giữ nguyên kho.
// ========================================================================
function dungKhoLHDCuaSo(soNgay) {
  soNgay = soNgay || 5;
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tz = 'Asia/Ho_Chi_Minh';
  const mktList = docDSMKT(ss);
  const log = []; function L(s){ log.push(s); }
  function ymdOf(y, m, d){ return y * 10000 + m * 100 + d; }

  const now = new Date();
  const endYmd = ymdOf(Number(Utilities.formatDate(now, tz, 'yyyy')), Number(Utilities.formatDate(now, tz, 'MM')), Number(Utilities.formatDate(now, tz, 'dd')));
  const startD = new Date(now.getTime() - soNgay * 24 * 3600 * 1000);
  const startYmd = ymdOf(Number(Utilities.formatDate(startD, tz, 'yyyy')), Number(Utilities.formatDate(startD, tz, 'MM')), Number(Utilities.formatDate(startD, tz, 'dd')));
  const since = Utilities.formatDate(new Date(startD.getTime() - 24 * 3600 * 1000), tz, 'yyyy-MM-dd');
  const until = Utilities.formatDate(new Date(now.getTime() + 24 * 3600 * 1000), tz, 'yyyy-MM-dd');
  L('=== KHO LHĐ cửa sổ ' + soNgay + ' ngày [' + startYmd + '..' + endYmd + '] ===');

  // 1) Lấy TẤT CẢ TK (gồm die/khoá/chưa thanh toán)
  let accs;
  try {
    accs = goiAPI('https://graph.facebook.com/' + API_VERSION + '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { L('✗ ' + e.message); Logger.log(log.join('\n')); return; }
  // Quét gọn (7h hằng ngày): TK đang hoạt động + có chi tiêu/hoá đơn 14 ngày gần đây (gồm TK vừa die)
  // -> nhanh, không quá 6 phút. (Bù trọn tháng dùng dungKhoLHD/buLHDDayDu quét đủ mọi TK.)
  const active = dsTKCanQuet(ss, accs);

  // 2) 4 số cuối thẻ (batch)
  const cardMap = {};
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    const batch = nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id + '?fields=funding_source_details' }; });
    let res; try { res = goiBatch(batch); } catch (e) { continue; }
    res.forEach(function(r, idx){ if (!r || r.code !== 200 || !r.body) return; let b; try { b = JSON.parse(r.body); } catch (e) { return; } cardMap[nhom[idx].account_id] = _card4(b.funding_source_details); });
  }

  // 3) kéo billing charge trong cửa sổ — GỘP BATCH 50 TK/lượt (nhẹ, chống vượt 6 phút)
  const newRows = []; let pulledAny = false, ok = 0, loi = 0, tongTien = 0;
  const relURL = function(a){ return 'act_' + a.account_id + '/activities?fields=event_time,event_type,extra_data&since=' + since + '&until=' + until + '&limit=500'; };
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    let res; try { res = goiBatch(nhom.map(function(a){ return { method: 'GET', relative_url: relURL(a) }; })); }
    catch (e) { loi += nhom.length; continue; }
    res.forEach(function(r, idx){
      const a = nhom[idx];
      if (!r || r.code !== 200 || !r.body) { loi++; return; }
      let body; try { body = JSON.parse(r.body); } catch (e) { loi++; return; }
      let acts = (body && body.data) ? body.data : [];
      const _next = body.paging && body.paging.next ? body.paging.next : null;   // TK nhiều thao tác -> lấy nốt, không cắt (thiếu ngày đầu tháng)
      if (_next) { try { acts = acts.concat(goiAPI(_next)); } catch (e) {} }
      ok++; pulledAny = true;
      const mk = ganMKT(chuanHoaTxt(a.name), mktList);
      const card = cardMap[a.account_id] || '';
      const byKey = {}; const seen = {};
      acts.forEach(function(ev){
        if (ev.event_type !== 'ad_account_billing_charge') return;
        let ex = {}; try { ex = (typeof ev.extra_data === 'string') ? JSON.parse(ev.extra_data) : (ev.extra_data || {}); } catch (e) { ex = {}; }
        const txId = ex.transaction_id || ''; if (txId && seen[txId]) return; if (txId) seen[txId] = 1;
        const d = new Date(ev.event_time); if (isNaN(d.getTime())) return;
        const gy = Number(Utilities.formatDate(d, tz, 'yyyy')), gm = Number(Utilities.formatDate(d, tz, 'MM')), gd = Number(Utilities.formatDate(d, tz, 'dd'));
        const k = ymdOf(gy, gm, gd);
        if (k < startYmd || k > endYmd) return;   // ngoài cửa sổ -> bỏ
        const val = (ex.new_value != null) ? Number(ex.new_value) : 0;
        if (!byKey[k]) byKey[k] = { gy: gy, gm: gm, gd: gd, val: 0 };
        byKey[k].val += val;
      });
      Object.keys(byKey).forEach(function(k){
        const o = byKey[k];
        newRows.push([new Date(o.gy, o.gm - 1, o.gd), a.account_id, a.name, mk, card, o.val]);
        tongTien += o.val;
      });
    });
  }
  L('Kéo OK ' + ok + ' TK · lỗi ' + loi + ' · dòng mới ' + newRows.length);

  if (!pulledAny) { L('Không kéo được gì -> GIỮ NGUYÊN kho'); Logger.log(log.join('\n')); ss.toast('LHĐ: không kéo được — giữ nguyên kho', 'Kho LHĐ', 6); return; }

  // 4) Ghi kho: BỎ dòng rơi TRONG CỬA SỔ (mọi TK), GIỮ nguyên phần còn lại; rồi ghi mới.
  let sh = ss.getSheetByName('LICH_SU_LHD'); if (!sh) sh = ss.insertSheet('LICH_SU_LHD');
  const header = ['Ngày', 'ID TKQC', 'Tên TK', 'Marketing', 'Số thẻ', 'Số tiền'];
  const old = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, 6).getValues() : [];
  const keep = old.filter(function(r){
    const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return false;   // ngày rác -> bỏ
    const k = ymdOf(d.getFullYear(), d.getMonth() + 1, d.getDate());
    return !(k >= startYmd && k <= endYmd);   // trong cửa sổ -> bỏ (sẽ ghi mới)
  }).map(function(r){ r[0] = toDate(r[0]); r[1] = String(r[1]); r[4] = (r[4] == null ? '' : String(r[4])); return r; });

  const all = keep.concat(newRows);
  sh.clear();
  sh.getRange(1, 1, 1, 6).setValues([header]);
  if (all.length) {
    // ÉP TEXT cột ID TKQC + Số thẻ TRƯỚC khi ghi -> không số hoá/scientific -> tránh gộp nhầm TK
    sh.getRange(2, 2, all.length, 1).setNumberFormat('@');
    sh.getRange(2, 5, all.length, 1).setNumberFormat('@');
    const norm = all.map(function(r){ return [r[0], String(r[1]), r[2], r[3], (r[4] == null ? '' : String(r[4])), r[5]]; });
    sh.getRange(2, 1, all.length, 6).setValues(norm);
    sh.getRange(2, 1, all.length, 1).setNumberFormat('dd/MM/yyyy');
    sh.getRange(2, 6, all.length, 1).setNumberFormat('#,##0');
  }
  L('=== Kho LHĐ: ' + all.length + ' dòng (mới ' + newRows.length + ', giữ ' + keep.length + ') ===');
  Logger.log(log.join('\n'));
  ss.toast('LHĐ cửa sổ ' + soNgay + ' ngày: ' + newRows.length + ' dòng, tổng ' + Math.round(tongTien).toLocaleString('vi-VN'), 'Kho LHĐ', 8);
}

// 7h sáng: cập nhật kho LHĐ (cửa sổ 5 ngày) + đầu tháng bù trọn tháng trước.
// KHÔNG dựng lại báo cáo tại đây -> nhẹ, chắc dưới giới hạn 6 phút. Khối ③ được cập nhật
// khi báo cáo dựng lại lúc 8h sáng (và 21h30) — luôn chạy sau 7h.
function chayLHDHangNgay() {
  try { dungKhoLHDCuaSo(5); } catch (e) { Logger.log('LHĐ cửa sổ lỗi: ' + e.message); }
  try {
    const tz = 'Asia/Ho_Chi_Minh'; const now = new Date();
    const d = Number(Utilities.formatDate(now, tz, 'dd'));
    if (d <= 3) {   // đầu tháng: chốt sạch trọn tháng trước 1 lần
      const y = Number(Utilities.formatDate(now, tz, 'yyyy')), m = Number(Utilities.formatDate(now, tz, 'MM'));
      const pm = m === 1 ? 12 : m - 1, py = m === 1 ? y - 1 : y;
      dungKhoLHD(py, pm);
    }
  } catch (e) { Logger.log('LHĐ bù tháng trước lỗi: ' + e.message); }
}

// BẬT lịch LHĐ 7h sáng (chỉ THÊM 1 trigger, KHÔNG đụng lịch khác). Chạy tay 1 lần.
function themLichLHD7h() {
  ScriptApp.getProjectTriggers().forEach(function(t){ if (t.getHandlerFunction() === 'chayLHDHangNgay') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('chayLHDHangNgay').timeBased().everyDays(1).atHour(7).nearMinute(0).create();
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã BẬT lịch LHĐ 7h sáng (cửa sổ 5 ngày)', 'Xong', 8);
}

// ĐƯỜNG LUI: TẮT lịch LHĐ 7h (chỉ xoá đúng trigger này).
function xoaLichLHD7h() {
  let n = 0;
  ScriptApp.getProjectTriggers().forEach(function(t){ if (t.getHandlerFunction() === 'chayLHDHangNgay') { ScriptApp.deleteTrigger(t); n++; } });
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã TẮT lịch LHĐ 7h (' + n + ' trigger)', 'Xong', 6);
}

// ========================================================================
// BÙ LHĐ ĐẦY ĐỦ (độc lập, chạy tay) — kéo LẠI trọn 1 tháng, PHÂN TRANG đầy đủ
// nên KHÔNG bị cắt 100 hoạt động -> lấy đủ cả các ngày ĐẦU THÁNG bị thiếu.
// Ghi SỐ GỐC vào kho (thay theo tháng). +10% VAT của Coremind/Apex vẫn TỰ ÁP khi
// hiển thị khối ③ (heSoVATLHD) -> KHÔNG cộng VAT vào kho để tránh nhân đôi.
// Chạy: buLHD_T7()  hoặc buLHDThangNay()  hoặc buLHDDayDu(year, month)
// ========================================================================
function buLHD_T7()      { return buLHDDayDu(2026, 7); }
function buLHDThangNay() {
  const tz = 'Asia/Ho_Chi_Minh', now = new Date();
  return buLHDDayDu(Number(Utilities.formatDate(now, tz, 'yyyy')), Number(Utilities.formatDate(now, tz, 'MM')));
}

function buLHDDayDu(year, month) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tz = 'Asia/Ho_Chi_Minh';
  const mktList = docDSMKT(ss);
  const log = []; function L(s){ log.push(s); }
  function p2(n){ return ('0' + n).slice(-2); }
  L('=== BÙ LHĐ ĐẦY ĐỦ Tháng ' + month + '/' + year + ' (phân trang, không cắt) ===');

  // 1) TK (gồm die/khoá/chưa thanh toán)
  let accs;
  try { accs = goiAPI('https://graph.facebook.com/' + API_VERSION + '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN)); }
  catch (e) { L('✗ ' + e.message); Logger.log(log.join('\n')); return; }
  const active = locTKCanLay(accs);
  L('TK lấy dữ liệu: ' + active.length);

  // 2) 4 số cuối thẻ (batch)
  const cardMap = {};
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    let res; try { res = goiBatch(nhom.map(function(a){ return { method: 'GET', relative_url: 'act_' + a.account_id + '?fields=funding_source_details' }; })); } catch (e) { continue; }
    res.forEach(function(r, idx){ if (!r || r.code !== 200 || !r.body) return; let b; try { b = JSON.parse(r.body); } catch (e) { return; } cardMap[nhom[idx].account_id] = _card4(b.funding_source_details); });
  }

  // 3) khoảng đệm 2 đầu -> lọc đúng tháng theo GMT+7
  const prevM = month === 1 ? 12 : month - 1, prevY = month === 1 ? year - 1 : year, prevLast = new Date(prevY, prevM, 0).getDate();
  const nextM = month === 12 ? 1 : month + 1, nextY = month === 12 ? year + 1 : year;
  const since = prevY + '-' + p2(prevM) + '-' + p2(prevLast);
  const until = nextY + '-' + p2(nextM) + '-01';

  // Gom billing (TK, ngày) trong đúng tháng từ danh sách activities đã lấy ĐỦ
  const newRows = []; const pulled = {}; let tongTien = 0;
  function gom(a, actsList) {
    const mk = ganMKT(chuanHoaTxt(a.name), mktList);
    const card = cardMap[a.account_id] || '';
    const byDay = {}; const seen = {};
    actsList.forEach(function(ev){
      if (ev.event_type !== 'ad_account_billing_charge') return;
      let ex = {}; try { ex = (typeof ev.extra_data === 'string') ? JSON.parse(ev.extra_data) : (ev.extra_data || {}); } catch (e) { ex = {}; }
      const txId = ex.transaction_id || ''; if (txId && seen[txId]) return; if (txId) seen[txId] = 1;
      const d = new Date(ev.event_time); if (isNaN(d.getTime())) return;
      const gy = Number(Utilities.formatDate(d, tz, 'yyyy')), gm = Number(Utilities.formatDate(d, tz, 'MM')), gd = Number(Utilities.formatDate(d, tz, 'dd'));
      if (gy !== year || gm !== month) return;
      const val = (ex.new_value != null) ? Number(ex.new_value) : 0;
      byDay[gd] = (byDay[gd] || 0) + val;
    });
    Object.keys(byDay).forEach(function(gd){ newRows.push([new Date(year, month - 1, Number(gd)), a.account_id, a.name, mk, card, byDay[gd]]); tongTien += byDay[gd]; });
  }

  // 4) Batch 40 TK/lượt, limit=500; TK nào còn phân trang -> lấy nốt qua goiAPI(next) -> KHÔNG sót
  let ok = 0, loi = 0;
  const relURL = function(a){ return 'act_' + a.account_id + '/activities?fields=event_time,event_type,extra_data&since=' + since + '&until=' + until + '&limit=500'; };
  for (let i = 0; i < active.length; i += 40) {
    const nhom = active.slice(i, i + 40);
    let res; try { res = goiBatch(nhom.map(function(a){ return { method: 'GET', relative_url: relURL(a) }; })); }
    catch (e) { loi += nhom.length; L('   ✗ batch ' + i + ': ' + e.message); continue; }
    res.forEach(function(r, idx){
      const a = nhom[idx];
      if (!r || r.code !== 200 || !r.body) { loi++; return; }
      let body; try { body = JSON.parse(r.body); } catch (e) { loi++; return; }
      let acts = (body && body.data) ? body.data : [];
      const next = body.paging && body.paging.next ? body.paging.next : null;
      if (next) { try { acts = acts.concat(goiAPI(next)); } catch (e) { L('   ⚠ phân trang ' + a.name + ': ' + e.message); } }
      ok++; pulled[a.account_id] = 1;
      gom(a, acts);
    });
  }
  L('Kéo OK ' + ok + ' · lỗi ' + loi + ' · dòng mới ' + newRows.length + ' · tổng ' + Math.round(tongTien).toLocaleString('vi-VN'));
  if (!Object.keys(pulled).length) { L('Không kéo được -> GIỮ NGUYÊN kho'); Logger.log(log.join('\n')); ss.toast('Không kéo được — giữ nguyên kho', 'Bù LHĐ', 6); return; }

  // 5) Ghi kho: THAY trọn tháng đang bù, GIỮ nguyên các tháng khác
  let sh = ss.getSheetByName('LICH_SU_LHD'); if (!sh) sh = ss.insertSheet('LICH_SU_LHD');
  const header = ['Ngày', 'ID TKQC', 'Tên TK', 'Marketing', 'Số thẻ', 'Số tiền'];
  const old = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, 6).getValues() : [];
  const keep = old.filter(function(r){
    const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return false;
    return !(d.getFullYear() === year && d.getMonth() + 1 === month);
  }).map(function(r){ r[0] = toDate(r[0]); r[1] = String(r[1]); r[4] = (r[4] == null ? '' : String(r[4])); return r; });

  const all = keep.concat(newRows);
  sh.clear();
  sh.getRange(1, 1, 1, 6).setValues([header]);
  if (all.length) {
    sh.getRange(2, 2, all.length, 1).setNumberFormat('@');
    sh.getRange(2, 5, all.length, 1).setNumberFormat('@');
    const norm = all.map(function(r){ return [r[0], String(r[1]), r[2], r[3], (r[4] == null ? '' : String(r[4])), r[5]]; });
    sh.getRange(2, 1, all.length, 6).setValues(norm);
    sh.getRange(2, 1, all.length, 1).setNumberFormat('dd/MM/yyyy');
    sh.getRange(2, 6, all.length, 1).setNumberFormat('#,##0');
  }
  L('=== Kho LICH_SU_LHD: ' + all.length + ' dòng (mới ' + newRows.length + ', giữ ' + keep.length + ') ===');
  Logger.log(log.join('\n'));
  ss.toast('Bù LHĐ T' + month + ' xong: ' + newRows.length + ' dòng, tổng ' + Math.round(tongTien).toLocaleString('vi-VN') + '. Chạy capNhatDoiSoat để cập nhật báo cáo.', 'Bù LHĐ', 12);
}

// ========================================================================
// dungTabLHD — dựng tab LHĐ cho 1 marketing, ĐỌC TỪ KHO (nhanh, không gọi API)
// ========================================================================
function dungTabLHD_Huy_T7() { dungTabLHD('Ngô Trọng Huy', 2026, 7); }

function dungTabLHD(tenMKT, year, month) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const nDays = new Date(year, month, 0).getDate();
  const target = chuanHoaTxt(tenMKT);
  const sh0 = ss.getSheetByName('LICH_SU_LHD');
  if (!sh0 || sh0.getLastRow() < 2) { ss.toast('Kho LICH_SU_LHD trống — chạy dungKhoLHD trước', 'LHĐ', 6); return; }
  const data = sh0.getRange(2, 1, sh0.getLastRow() - 1, 6).getValues();

  const acc = {};
  data.forEach(function(r){
    const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
    if (d.getFullYear() !== year || d.getMonth() + 1 !== month) return;
    if (chuanHoaTxt(r[3]) !== target) return;
    const id = String(r[1]);
    if (!acc[id]) { acc[id] = { ten: r[2], id: id, card: r[4], byDay: {}, tong: 0 }; for (let dd = 1; dd <= nDays; dd++) acc[id].byDay[dd] = 0; }
    acc[id].byDay[d.getDate()] += Number(r[5] || 0); acc[id].tong += Number(r[5] || 0);
  });

  const ids = Object.keys(acc);
  const nCol = 4 + nDays;
  const out = [];
  const titleR = ['CHI TIẾT LẬP HOÁ ĐƠN THÁNG ' + month + '/' + year + ' — MARKETING ' + tenMKT.toUpperCase()];
  while (titleR.length < nCol) titleR.push('');
  out.push(titleR);
  const hdr = ['Tên TK', 'ID TKQC', 'Số Thẻ', 'Tổng Tháng']; for (let d = 1; d <= nDays; d++) hdr.push(d);
  out.push(hdr);
  const tongNgay = {}; for (let d = 1; d <= nDays; d++) tongNgay[d] = 0; let grand = 0;
  ids.forEach(function(id){
    const r = acc[id]; const row = [r.ten, r.id, r.card, r.tong];
    for (let d = 1; d <= nDays; d++) { row.push(r.byDay[d] || 0); tongNgay[d] += (r.byDay[d] || 0); }
    grand += r.tong; out.push(row);
  });
  const tongRow = ['TỔNG', '', '', grand]; for (let d = 1; d <= nDays; d++) tongRow.push(tongNgay[d]);
  out.push(tongRow);

  let sh = ss.getSheetByName('LHĐ'); if (!sh) sh = ss.insertSheet('LHĐ');
  sh.clear();
  const nRow = out.length;
  sh.getRange(1, 1, nRow, nCol).setValues(out);
  sh.getRange(1, 1, 1, nCol).merge().setBackground('#16233f').setFontColor('#fff').setFontWeight('bold').setFontSize(13).setHorizontalAlignment('center');
  sh.setRowHeight(1, 40);
  sh.getRange(2, 1, 1, nCol).setBackground('#2f4a7a').setFontColor('#fff').setFontWeight('bold').setHorizontalAlignment('center');
  sh.getRange(3, 4, nRow - 2, 1 + nDays).setNumberFormat('#,##0');
  sh.getRange(2, 2, nRow - 1, 1).setNumberFormat('@');
  sh.getRange(2, 3, nRow - 1, 1).setNumberFormat('@');
  if (ids.length) { const idCol = ids.map(function(id){ return [String(acc[id].id)]; }); sh.getRange(3, 2, idCol.length, 1).setNumberFormat('@'); sh.getRange(3, 2, idCol.length, 1).setValues(idCol); }
  sh.getRange(nRow, 1, 1, nCol).setBackground('#fff7e6').setFontWeight('bold');
  if (nRow - 3 >= 1) sh.getRange(3, 4, nRow - 3, 1).setFontWeight('bold');
  sh.getRange(1, 1, nRow, nCol).setBorder(true, true, true, true, true, true, '#d9dee7', SpreadsheetApp.BorderStyle.SOLID);
  sh.setColumnWidth(1, 260); sh.setColumnWidth(2, 150); sh.setColumnWidth(3, 90); sh.setColumnWidth(4, 130);
  for (let c = 5; c <= nCol; c++) sh.setColumnWidth(c, 95);
  sh.setFrozenRows(2);
  ss.toast('Tab LHĐ (' + tenMKT + '): ' + ids.length + ' TK, tổng ' + Math.round(grand).toLocaleString('vi-VN'), 'LHĐ', 8);
}