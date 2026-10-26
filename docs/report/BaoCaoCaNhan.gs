// ===== BÁO CÁO CÁ NHÂN — KIẾN TRÚC THÁNG/NĂM + KHỐI REALTIME + KHỐI LỌC =====
// Kho: LICH_SU (ads) + LICH_SU_DS (đối soát) -> sheet "BC - [tên] - [năm]"
const DOISOAT_ID = '1XU5PdyJnM_EDRnci2IC09iaG7x2AhOm882bWZiLQYTs';

// 8h sáng: số hôm qua -> 2 kho -> dựng báo cáo
function chay8hSang() {
  chayHomQua();
  const hq = new Date(new Date().getTime() - 24 * 3600 * 1000);
  chotNgay('DATA_HOMQUA', hq);
  chotDoiSoat();                                              // dựng lại đối soát (gán marketing mới nhất)
  dungBaoCaoCaNhan();                                         // báo cáo cá nhân (tự có sheet MKT mới)
  if (typeof dungDashboard === 'function') dungDashboard();   // dashboard tự cập nhật
}

// 21h30: KIỂM TRA LẠI chi tiêu HÔM QUA rồi GHI ĐÈ báo cáo hôm qua cho khớp thực tế.
// Lý do: 8h sáng Meta chưa "settle" đủ -> TK ngân sách lớn lệch nhiều; cuối ngày số đã ổn định.
// Cùng chuỗi xử lý như 8h (đã kiểm chứng), chỉ khác thời điểm chạy -> không phát sinh logic mới.
// 21h30 CHỈ lo đúng việc cốt lõi: CHỐT CHI TIÊU CHUẨN của hôm qua + báo cáo cá nhân + dashboard hiển thị đúng.
// KHÔNG chạy chotDoiSoat (đối soát/doanh thu đã do khung 12h đảm bảo) -> bỏ bước phụ thuộc file ngoài, nhẹ hơn.
function chay21h30() {
  chayHomQua();                                              // kéo lại số HÔM QUA mới nhất từ Meta (field expansion, có chốt thời gian)
  const hq = new Date(new Date().getTime() - 24 * 3600 * 1000);
  chotNgay('DATA_HOMQUA', hq);                               // ghi ĐÈ đúng ngày hôm qua trong LICH_SU (có khoá chống mất số)
  dungBaoCaoCaNhan();                                        // dựng lại báo cáo cá nhân (số chi tiêu hôm qua hiện đúng)
  if (typeof dungDashboard === 'function') dungDashboard();  // dashboard hiển thị đúng chi tiêu
}

// BẬT khung 21h30 — chỉ THÊM 1 lịch, KHÔNG đụng 8h/12h/17h15/23h. Chạy tay 1 lần là xong.
// (Chạy lại nhiều lần cũng an toàn: tự xoá cái cũ rồi tạo lại, không nhân đôi.)
function themLich21h30() {
  ScriptApp.getProjectTriggers().forEach(function(t){
    if (t.getHandlerFunction() === 'chay21h30') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('chay21h30').timeBased().everyDays(1).atHour(21).nearMinute(30).create();
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã BẬT lịch 21h30: tự kiểm tra lại chi tiêu hôm qua', 'Xong', 8);
}

// ĐƯỜNG LUI: TẮT khung 21h30 (chỉ xoá đúng trigger này, không ảnh hưởng lịch khác).
function xoaLich21h30() {
  let n = 0;
  ScriptApp.getProjectTriggers().forEach(function(t){
    if (t.getHandlerFunction() === 'chay21h30') { ScriptApp.deleteTrigger(t); n++; }
  });
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã TẮT lịch 21h30 (' + n + ' trigger)', 'Xong', 6);
}

// TEST: chốt số + đối soát rồi dựng (KHÔNG gọi Facebook)
// Gán nhãn = HÔM QUA để khớp thực tế (DATA_HOMQUA/DATA_TONG lúc sáng là số của hôm qua)
function testBaoCaoCaNhan() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const hq = new Date(new Date().getTime() - 24 * 3600 * 1000);
  const src = ss.getSheetByName('DATA_HOMQUA') ? 'DATA_HOMQUA' : 'DATA_TONG';
  chotNgay(src, hq);
  chotDoiSoat();
  dungBaoCaoCaNhan();
}

// Bấm khi vừa sửa file ĐỐI SOÁT: dựng lại đối soát -> báo cáo cá nhân -> dashboard.
// (CHỈ đọc đối soát + vẽ lại; KHÔNG đụng số ads đã chốt, khung giờ, Telegram)
function capNhatDoiSoat() {
  chotDoiSoat();
  dungBaoCaoCaNhan();
  if (typeof dungDashboard === 'function') dungDashboard();
}

// Bấm khi vừa THÊM/SỬA marketing trong DANH_SACH_MKT: gán lại Marketing cho TOÀN BỘ
// lịch sử ads (theo Tên TK), rồi dựng lại đối soát + báo cáo cá nhân. Không gọi Facebook.
function ganLaiMarketing() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ls = ss.getSheetByName('LICH_SU');
  if (ls && ls.getLastRow() > 1) {
    const mktList = docDSMKT(ss);
    const rng = ls.getRange(2, 1, ls.getLastRow() - 1, 7);
    const data = rng.getValues();
    data.forEach(function(r){
      const ch = chuanHoaTxt(r[2]);                       // Tên TK
      r[3] = ganMKT(ch, mktList);                         // gán lại Marketing
      r[4] = _nguonTK(r[2]);                              // Nguồn (Coremind/Apex/GPI/Khác)
    });
    rng.setValues(data);
  }
  chotDoiSoat();
  dungBaoCaoCaNhan();
  ss.toast('Đã gán lại Marketing toàn bộ lịch sử + dựng lại báo cáo', 'OK', 6);
}

// ============================================================================
// BÙ CHI TIÊU ĐẦY ĐỦ 1 tháng (độc lập, chạy tay): kéo insights trọn tháng cho TẤT CẢ
// tài khoản (gồm die), gom theo TK/ngày, GHI ĐÈ đúng tháng đó trong LICH_SU (giữ tháng khác).
// Dùng để bổ sung các TK bị thiếu chi tiêu (die/chốt lỗi trước đây). Chạy: buChiTieu_T8()
// hoặc buChiTieuThang(year, month). Sau đó chạy capNhatDoiSoat.
// ============================================================================
function buChiTieu_T8() { return buChiTieuThang(2026, 8); }

function buChiTieuThang(year, month) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const tz = 'Asia/Ho_Chi_Minh';
  const mktList = docDSMKT(ss);
  function p2(n){ return ('0' + n).slice(-2); }
  const since = year + '-' + p2(month) + '-01';
  const until = year + '-' + p2(month) + '-' + p2(new Date(year, month, 0).getDate());
  const timeRange = encodeURIComponent(JSON.stringify({ since: since, until: until }));

  let accs;
  try { accs = goiAPI('https://graph.facebook.com/' + API_VERSION + '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' + encodeURIComponent(ACCESS_TOKEN)); }
  catch (e) { ss.toast('Lỗi lấy TK: ' + e.message, 'Bù chi tiêu', 8); return; }
  const active = locTKCanLay(accs);

  const byId = {};   // id -> { name, days: { d: {ct, sdt} } }
  let pulled = false, ok = 0, loi = 0;
  const relURL = function(a){ return 'act_' + a.account_id + '/insights?level=account&fields=spend,actions&time_range=' + timeRange + '&time_increment=1&limit=400'; };
  for (let i = 0; i < active.length; i += 50) {
    const nhom = active.slice(i, i + 50);
    let res; try { res = goiBatch(nhom.map(function(a){ return { method: 'GET', relative_url: relURL(a) }; })); }
    catch (e) { loi += nhom.length; continue; }
    res.forEach(function(r, idx){
      const a = nhom[idx];
      if (!r || r.code !== 200 || !r.body) { loi++; return; }
      let body; try { body = JSON.parse(r.body); } catch (e) { loi++; return; }
      let rows = (body && body.data) ? body.data : [];
      const next = body.paging && body.paging.next ? body.paging.next : null;
      if (next) { try { rows = rows.concat(goiAPI(next)); } catch (e) {} }
      ok++; pulled = true;
      rows.forEach(function(it){
        const d = new Date(it.date_start); if (isNaN(d.getTime())) return;
        const gy = Number(Utilities.formatDate(d, tz, 'yyyy')), gm = Number(Utilities.formatDate(d, tz, 'MM')), gd = Number(Utilities.formatDate(d, tz, 'dd'));
        if (gy !== year || gm !== month) return;
        const ct = Number(it.spend || 0);
        const sdt = layGiaTriAction(it.actions, ACTION_MUA);   // đúng như chốt hằng ngày (cột "Số ĐT" = lượt mua)
        const id = String(a.account_id);
        if (!byId[id]) byId[id] = { name: a.name, days: {} };
        if (!byId[id].days[gd]) byId[id].days[gd] = { ct: 0, sdt: 0 };
        byId[id].days[gd].ct += ct; byId[id].days[gd].sdt += sdt;
      });
    });
  }
  if (!pulled) { ss.toast('Không kéo được — GIỮ NGUYÊN LICH_SU', 'Bù chi tiêu', 8); return; }

  // GHI ĐÈ trọn tháng đang bù, GIỮ nguyên các tháng khác
  let ls = ss.getSheetByName('LICH_SU'); if (!ls) ls = ss.insertSheet('LICH_SU');
  const header = ['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT'];
  const old = ls.getLastRow() > 1 ? ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues() : [];
  let lsDT = ss.getSheetByName('LICH_SU_DACTHU');
  const dtHeader = ['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT', 'Mục đích'];
  const oldDT = (lsDT && lsDT.getLastRow() > 1) ? lsDT.getRange(2, 1, lsDT.getLastRow() - 1, 8).getValues() : [];
  const trongThang = function(d){ return (d instanceof Date) && !isNaN(d.getTime()) && d.getFullYear() === year && d.getMonth() + 1 === month; };

  // KHOÁ TÊN theo (id|dd/mm/yyyy) từ dữ liệu ĐÃ LƯU (LICH_SU + kho đặc thù) của tháng đang bù:
  // bù số ngày cũ -> GIỮ tên đã lưu (ghi vào đúng dòng cũ), KHÔNG kéo sang tên mới dù Meta đã đổi tên.
  const tenCu = {};
  function napTen(r){ const d = toDate(r[0]); if (!trongThang(d)) return; const k = String(r[1]) + '|' + dmy(r[0]); if (tenCu[k] == null) tenCu[k] = r[2]; }
  old.forEach(napTen); oldDT.forEach(napTen);

  // Dựng dòng mới: khoá tên + định tuyến (đặc thù -> kho riêng; 'Khác' -> bỏ; hợp lệ -> LICH_SU)
  const newRows = [], newRowsDT = [];
  let boKhac = 0;
  Object.keys(byId).forEach(function(id){
    const o = byId[id];
    Object.keys(o.days).forEach(function(gd){
      const dObj = new Date(year, month - 1, Number(gd));
      const k = String(id) + '|' + dmy(dObj);
      const ten = (tenCu[k] != null) ? tenCu[k] : o.name;   // khoá tên nếu ngày này đã có
      const ch = chuanHoaTxt(ten);
      const md = _mucDichDacThu(ten), nguon = _nguonTK(ten);
      const dd = o.days[gd];
      if (md) newRowsDT.push([dObj, id, ten, ganMKT(ch, mktList), nguon, dd.ct, dd.sdt, md]);
      else if (nguon === 'Khác') boKhac++;
      else newRows.push([dObj, id, ten, ganMKT(ch, mktList), nguon, dd.ct, dd.sdt]);
    });
  });

  // Ghi LICH_SU: giữ tháng khác + dòng hợp lệ mới
  const keep = old.filter(function(r){ return !trongThang(toDate(r[0])); }).map(function(r){ r[0] = toDate(r[0]); r[1] = String(r[1]); return r; });
  const all = keep.concat(newRows);
  ls.clear();
  ls.getRange(1, 1, 1, 7).setValues([header]);
  if (all.length) {
    ls.getRange(2, 2, all.length, 1).setNumberFormat('@');      // ID -> TEXT (chống scientific/lệch TK)
    ls.getRange(2, 1, all.length, 1).setNumberFormat('dd/MM/yyyy');
    SpreadsheetApp.flush();
    const norm = all.map(function(r){ return [r[0], String(r[1]), r[2], r[3], r[4], r[5], r[6]]; });
    ls.getRange(2, 1, all.length, 7).setValues(norm);
  }
  // Ghi LICH_SU_DACTHU: giữ tháng khác + dòng đặc thù mới
  const keepDT = oldDT.filter(function(r){ return !trongThang(toDate(r[0])); }).map(function(r){ r[0] = toDate(r[0]); r[1] = String(r[1]); return r; });
  const allDT = keepDT.concat(newRowsDT);
  if (lsDT || allDT.length) {
    if (!lsDT) lsDT = ss.insertSheet('LICH_SU_DACTHU');
    lsDT.clear();
    lsDT.getRange(1, 1, 1, 8).setValues([dtHeader]);
    if (allDT.length) {
      lsDT.getRange(2, 2, allDT.length, 1).setNumberFormat('@');
      lsDT.getRange(2, 1, allDT.length, 1).setNumberFormat('dd/MM/yyyy');
      SpreadsheetApp.flush();
      const normDT = allDT.map(function(r){ return [r[0], String(r[1]), r[2], r[3], r[4], r[5], r[6], r[7]]; });
      lsDT.getRange(2, 1, allDT.length, 8).setValues(normDT);
    }
  }
  ss.toast('Bù chi tiêu T' + month + ': ' + newRows.length + ' hợp lệ · ' + newRowsDT.length + ' đặc thù · bỏ ' + boKhac + ' không pháp nhân. Chạy capNhatDoiSoat để dựng lại.', 'Bù chi tiêu', 12);
}

// Chốt số ads 1 ngày vào LICH_SU (Ngày lưu dạng NGÀY THẬT để lọc được)
function chotNgay(sourceName, dateObj) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const src = ss.getSheetByName(sourceName);
  if (!src) throw new Error('Chưa có tab ' + sourceName);
  const data = src.getDataRange().getValues();
  const mktList = docDSMKT(ss);
  const dstr = Utilities.formatDate(dateObj, 'Asia/Ho_Chi_Minh', 'dd/MM/yyyy');

  const acc = {};
  for (let i = 1; i < data.length; i++) {
    const id = String(data[i][1] || '').trim(); if (!id) continue;
    if (!acc[id]) acc[id] = { ten: data[i][0], chiTieu: 0, sdt: 0 };
    acc[id].chiTieu += Number(data[i][2] || 0);
    acc[id].sdt     += Number(data[i][5] || 0);
  }
  // ⛑️ CHỐNG MẤT SỐ: nếu nguồn (DATA_HOMQUA) TRỐNG — do lượt kéo giờ cao điểm về 0 dòng — thì DỪNG,
  // GIỮ NGUYÊN LICH_SU, KHÔNG ghi đè/xoá số ngày hôm đó. Tránh chốt nhầm số rỗng làm mất dữ liệu đã có.
  // Lượt chạy sau (21h30/8h hôm sau) kéo đủ sẽ tự chốt lại đúng.
  if (Object.keys(acc).length === 0) {
    ss.toast('Chốt ngày: nguồn ' + sourceName + ' trống — GIỮ NGUYÊN LICH_SU, không ghi đè', 'Bỏ qua', 8);
    return;
  }
  let ls = ss.getSheetByName('LICH_SU'); if (!ls) ls = ss.insertSheet('LICH_SU');
  const header = ['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT'];
  const old = ls.getLastRow() > 1 ? ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues() : [];
  // ⛑️ CHỐNG MẤT SỐ khi lượt kéo BỊ CẮT DỞ (Meta chậm -> dừng sớm -> thiếu TK): nếu số TK kéo về cho ngày
  // này ÍT HƠN số đã lưu -> GIỮ số cũ, KHÔNG ghi đè (tránh 21h30 kéo thiếu đè mất số 8h đã đủ). Lượt sau kéo đủ tự chốt lại.
  const soDaLuu = old.filter(function(r){ return dmy(r[0]) === dstr; }).length;
  if (soDaLuu > 0 && Object.keys(acc).length < soDaLuu) {
    ss.toast('Chốt ' + dstr + ': kéo về ' + Object.keys(acc).length + ' TK < đã lưu ' + soDaLuu + ' → GIỮ số cũ', 'Bỏ qua', 8);
    return;
  }
  const keep = old.filter(function(r){ return dmy(r[0]) !== dstr; }).map(function(r){ r[0] = toDate(r[0]); return r; });

  // ĐỊNH TUYẾN (áp cho dữ liệu ngày đang chốt):
  //   ③ TK đặc thù (Tuyển dụng/PR) -> kho LICH_SU_DACTHU (không vào LICH_SU chính).
  //   ① TK không gán được pháp nhân (nguồn 'Khác') -> BỎ, không ghi đâu cả.
  //   Còn lại (Coremind/Apex/GPI) -> LICH_SU như cũ.
  let lsDT = ss.getSheetByName('LICH_SU_DACTHU');
  const dtHeader = ['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT', 'Mục đích'];
  const oldDT = (lsDT && lsDT.getLastRow() > 1) ? lsDT.getRange(2, 1, lsDT.getLastRow() - 1, 8).getValues() : [];
  const keepDT = oldDT.filter(function(r){ return dmy(r[0]) !== dstr; }).map(function(r){ r[0] = toDate(r[0]); return r; });
  // KHOÁ TÊN THEO (ID + NGÀY): (id, ngày đang chốt) ĐÃ có trong kho (LICH_SU hoặc kho đặc thù) -> GIỮ tên đã lưu,
  // chỉ cập nhật số. Nhờ vậy đổi tên/bù số ngày cũ KHÔNG kéo dòng sang tên mới. Ngày mới (chưa có) -> lấy tên hiện tại.
  const tenCu = {};
  old.forEach(function(r){ if (dmy(r[0]) === dstr) { const id = String(r[1]); if (tenCu[id] == null) tenCu[id] = r[2]; } });
  oldDT.forEach(function(r){ if (dmy(r[0]) === dstr) { const id = String(r[1]); if (tenCu[id] == null) tenCu[id] = r[2]; } });
  let soKhac = 0, soDacThu = 0;
  Object.keys(acc).forEach(function(id){
    const a = acc[id];
    const ten = (tenCu[id] != null) ? tenCu[id] : a.ten;   // KHOÁ TÊN: ưu tiên tên đã lưu cho ngày này
    const ch = chuanHoaTxt(ten);
    const md = _mucDichDacThu(ten);
    const nguon = _nguonTK(ten);
    if (md) {                                   // đặc thù -> kho riêng
      keepDT.push([toDate(dateObj), id, ten, ganMKT(ch, mktList), nguon, a.chiTieu, a.sdt, md]); soDacThu++;
    } else if (nguon === 'Khác') {              // không pháp nhân -> bỏ
      soKhac++;
    } else {                                    // hợp lệ -> LICH_SU
      keep.push([toDate(dateObj), id, ten, ganMKT(ch, mktList), nguon, a.chiTieu, a.sdt]);
    }
  });
  ls.clear();
  ls.getRange(1, 1, 1, 7).setValues([header]);
  if (keep.length) {
    // ÉP TEXT cột ID (B) TRƯỚC khi ghi -> ID 16–17 số KHÔNG bị coerce về số (mất chữ số / scientific)
    ls.getRange(2, 2, keep.length, 1).setNumberFormat('@');
    ls.getRange(2, 1, keep.length, 1).setNumberFormat('dd/MM/yyyy');
    SpreadsheetApp.flush();                                   // commit format trước khi setValues
    keep.forEach(function(r){ r[1] = String(r[1]); });        // đảm bảo ID là chuỗi (kể cả dòng cũ đọc lên là số)
    ls.getRange(2, 1, keep.length, 7).setValues(keep);
  }
  // Ghi kho đặc thù (chỉ tạo/đụng khi có kho sẵn hoặc có dòng đặc thù)
  if (lsDT || keepDT.length) {
    if (!lsDT) lsDT = ss.insertSheet('LICH_SU_DACTHU');
    lsDT.clear();
    lsDT.getRange(1, 1, 1, 8).setValues([dtHeader]);
    if (keepDT.length) {
      lsDT.getRange(2, 2, keepDT.length, 1).setNumberFormat('@');
      lsDT.getRange(2, 1, keepDT.length, 1).setNumberFormat('dd/MM/yyyy');
      SpreadsheetApp.flush();
      keepDT.forEach(function(r){ r[1] = String(r[1]); });
      lsDT.getRange(2, 1, keepDT.length, 8).setValues(keepDT);
    }
  }
  if (soKhac || soDacThu) ss.toast('Chốt ' + dstr + ': bỏ ' + soKhac + ' TK không pháp nhân · tách ' + soDacThu + ' TK đặc thù', 'Định tuyến', 6);
}

// Dựng lại LICH_SU_DS từ TẤT CẢ tab tháng của đối soát (đầy đủ mọi tháng)
function chotDoiSoat() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const mktList = docDSMKT(ss);
  let ext; try { ext = SpreadsheetApp.openById(DOISOAT_ID); } catch (e) { return; }
  const agg = {}; // "mk|dd/MM/yyyy" -> {mk,d,nong,goi,toiuu,khach,dt,bong,dtHN,dtMB}
  const hnAll = {}; // "dd/MM/yyyy" -> {d,dt}  (MỌI khách note HN, KHÔNG lọc theo DS MKT)
  const nguonAll = {}; // "nguon|dd/MM/yyyy" -> {nguon,d,khach,nong,goi,toiuu,bong,dt}  (Hotline & nguồn team KHÔNG gắn cá nhân)

  ext.getSheets().forEach(function(sh){
    if (chuanHoaTxt(sh.getName()).indexOf('THANG') < 0) return; // chỉ tab THÁNG
    // Khoá hệ số ×10 theo TÊN TAB: tháng = số ngay sau "THÁNG"; năm = số 20xx trong tên tab (nếu có). KHÔNG lệ thuộc ngày từng dòng.
    const _tenTab = chuanHoaTxt(sh.getName());
    const _tm = _tenTab.match(/THANG\s*0*(\d{1,2})/);   const tabThang = _tm ? Number(_tm[1]) : 0;
    const _ty = _tenTab.match(/(20\d{2})/);             const tabNam   = _ty ? Number(_ty[1]) : 0;
    const data = sh.getDataRange().getValues();
    let hr = -1, col = {};
    for (let i = 0; i < data.length; i++) {
      let f = false; data[i].forEach(function(c){ if (chuanHoaTxt(c) === 'MA KHACH HANG') f = true; });
      if (f) { hr = i; data[i].forEach(function(c, idx){ col[chuanHoaTxt(c)] = idx; }); break; }
    }
    if (hr < 0) return;
    const cNgay = col['NGAY'], cMkt = col['MARKETING'], cLoai = col['LOAI LICH'], cDT = col['DOANH THU'], cMa = col['MA KHACH HANG'];
    // dò cột Ghi chú + Tình trạng (tên cột có thể khác nhau giữa các tháng)
    let cGhiChu = null, cTinhTrang = null, cDiaChi = null, cNo = null;
    Object.keys(col).forEach(function(h){
      if (cGhiChu == null && h.indexOf('GHI CHU') >= 0) cGhiChu = col[h];
      if (cTinhTrang == null && (h.indexOf('TINH TRANG') >= 0 || h.indexOf('TRANG THAI') >= 0)) cTinhTrang = col[h];
      if (cDiaChi == null && h.indexOf('DIA CHI') >= 0) cDiaChi = col[h];   // Địa chỉ: LOẠI khỏi quét note (địa chỉ có "HN" Hà Nội -> nhầm nguồn HN)
      if (cNo == null && (h === 'NO' || h.indexOf('CONG NO') >= 0)) cNo = col[h];
    });
    for (let i = hr + 1; i < data.length; i++) {
      const r = data[i]; const ma = String(r[cMa] || '').trim();
      if (!ma || chuanHoaTxt(ma) === 'TONG') continue;
      const dObj = toDate(r[cNgay]);
      if (!(dObj instanceof Date) || isNaN(dObj.getTime())) continue;
      const dstrRow = Utilities.formatDate(dObj, 'Asia/Ho_Chi_Minh', 'dd/MM/yyyy');
      const mRow = dObj.getMonth() + 1, yRow = dObj.getFullYear();
      // Quy ước bảo mật: CHỈ các TAB "THÁNG 4/5/6" của năm 2026 ghi doanh thu = thực tế/10 -> nhân lại 10.
      // Khoá theo TAB nên tab Tháng 7 trở đi TUYỆT ĐỐI không bị nhân (dù ngày trong dòng gõ lệch); T4/5/6 luôn nhân đủ.
      const laT456 = (tabThang === 4 || tabThang === 5 || tabThang === 6);
      const dungNam = (tabNam === 2026) || (tabNam === 0 && yRow === 2026); // tab có ghi năm thì theo tab; không thì theo năm của dòng
      const heSoDT = (laT456 && dungNam) ? 10 : 1;
      // VAT: từ T7/2026, doanh thu ghi nhận gồm 8% VAT (xuất hoá đơn) -> chia 1.08 ra doanh thu THỰC.
      // Loại trừ T4/5/6 (đang dùng ×10) -> 2 cơ chế này không bao giờ trùng tháng.
      const tuT7dt = (yRow > 2026) || (yRow === 2026 && mRow >= 7);
      const heSoVATdt = tuT7dt ? (1 / 1.08) : 1;
      const dtRow = (cDT != null ? parseNum(r[cDT]) : 0) * heSoDT * heSoVATdt;
      const loai = (cLoai != null) ? chuanHoaTxt(r[cLoai]) : '';
      const tt = (cTinhTrang != null) ? chuanHoaTxt(r[cTinhTrang]) : '';
      const gc = (cGhiChu != null) ? chuanHoaTxt(r[cGhiChu]) : '';
      const gcTok = gc.split(/[^A-Z0-9]+/);   // tách token Ghi chú (MB, GG... không đụng chữ khác như COMBO)
      // QUÉT NOTE theo đúng cơ chế: ưu tiên cột GHI CHÚ TRƯỚC, rồi quét CẢ HÀNG phòng khi note gõ nhầm cột.
      // LOẠI TRỪ cột nhiễu: Marketing, Địa chỉ (có "HN" Hà Nội), Nợ, Doanh thu, Ngày, Mã KH -> không nhầm nguồn.
      const _boQuaCot = {}; [cMkt, cDiaChi, cNo, cDT, cNgay, cMa].forEach(function(c){ if (c != null) _boQuaCot[c] = true; });
      let noteStr = (cGhiChu != null ? String(r[cGhiChu]) : '');                              // GHI CHÚ trước (ưu tiên)
      r.forEach(function(c, idx){ if (!_boQuaCot[idx] && idx !== cGhiChu) noteStr += ' ' + c; });  // rồi các cột còn lại (đã bỏ nhiễu)
      noteStr = chuanHoaTxt(noteStr);
      const noteTok = noteStr.split(/[^A-Z0-9]+/);
      const isBong = tt.indexOf('BONG') >= 0;

      const mch = chuanHoaTxt(r[cMkt]);

      // (1) NGUỒN TEAM không gắn cá nhân: Hotline / HOTLINE / HotlineFB -> kho nguồn (tính cho TEAM, phần Cơ sở HCM)
      //     TÍNH LÙI MỌI THÁNG (kể cả tháng 7 & trước đó) — bổ sung đủ nguồn Hotline vào tổng team.
      if (mch.indexOf('HOTLINE') >= 0) {
        const nkey = 'Hotline|' + dstrRow;
        if (!nguonAll[nkey]) nguonAll[nkey] = { nguon: 'Hotline', d: dObj, khach: 0, nong: 0, goi: 0, toiuu: 0, bong: 0, dt: 0 };
        const g = nguonAll[nkey];
        g.khach++; g.dt += dtRow;
        if (loai === 'LICH NONG') g.nong++; else if (loai === 'LICH GOI') g.goi++; else if (loai === 'LICH TOI UU') g.toiuu++;
        if (isBong) g.bong++;
        continue; // Hotline KHÔNG đi vào nhóm marketing cá nhân
      }

      // (1b) NGUỒN TEAM khác theo GHI CHÚ: MKT Cũ / Google(GG) / Tiktok -> kho nguồn (TÍNH TEAM đủ chỉ số),
      //      TÁCH RIÊNG từng kênh, KHÔNG gắn cá nhân, tính lùi mọi tháng. -> Dashboard A hiện dòng ◆ + cộng tổng B.
      let _ngGC = '';
      if (noteStr.indexOf('MKT CU') >= 0) _ngGC = 'MKT Cũ';
      else if (noteTok.indexOf('GG') >= 0 || noteStr.indexOf('GOOGLE') >= 0) _ngGC = 'Google';
      else if (noteStr.indexOf('TIKTOK') >= 0) _ngGC = 'Tiktok';
      if (_ngGC) {
        const nkey = _ngGC + '|' + dstrRow;
        if (!nguonAll[nkey]) nguonAll[nkey] = { nguon: _ngGC, d: dObj, khach: 0, nong: 0, goi: 0, toiuu: 0, bong: 0, dt: 0 };
        const g = nguonAll[nkey];
        g.khach++; g.dt += dtRow;
        if (loai === 'LICH NONG') g.nong++; else if (loai === 'LICH GOI') g.goi++; else if (loai === 'LICH TOI UU') g.toiuu++;
        if (isBong) g.bong++;
        continue; // nguồn team -> không đi vào marketing cá nhân
      }

      // (2) MB = khách data SG nhưng LÀM Ở CƠ SỞ HN (note "MB" ở Ghi chú): doanh thu tính CÁ NHÂN, KHÔNG tính team; khách bỏ hẳn.
      //     Áp dụng HỄ CÓ note "MB" (không khoá theo tháng) — vì note MB chỉ do Sếp chủ động ghi, dữ liệu cũ không dính.
      const lamMB = noteTok.indexOf('MB') >= 0;

      // HN chuyển HCM: note "HN" ở BẤT KỲ cột nào (quét toàn hàng như MB — trước đây chỉ soi cột Ghi chú
      // nên HN ghi ở cột khác bị SÓT -> khách không khớp MKT SG sẽ rơi vào nhánh bỏ qua -> mất doanh thu).
      // HOẶC Marketing chứa "HN-" / "-HN". KHÔNG áp cho khách MB.
      const isHN = !lamMB && (gc.indexOf('HN') >= 0 || noteTok.indexOf('HN') >= 0 ||
                              mch.indexOf('HN-') >= 0 || mch.indexOf('-HN') >= 0);
      // Khớp tên marketing — KHÔNG bắt buộc tên phải chứa "SG"/"HCM" nữa (trước đây bắt buộc -> tên gõ trần bị rớt).
      const mk = ganMKT(mch, mktList);
      const listed = (mk !== 'Chưa gán' && String(mk).indexOf('⚠️') < 0);

      if (isHN) {
        // ✅ MỌI khách ghi chú "HN" -> nguồn HN CHUYỂN MIỀN (kho HN), KHÔNG tính cho marketing cá nhân —
        // KỂ CẢ khi tên marketing khớp danh sách MKT. (Trước đây HN mà khớp tên thì bị đếm sang cá nhân
        // -> dòng "HN chưa gán" thiếu khách: đối soát 11 mà dashboard chỉ 7.) Đếm đủ khách + loại lịch + bong + DT.
        if (!hnAll[dstrRow]) hnAll[dstrRow] = { d: dObj, khach: 0, nong: 0, goi: 0, toiuu: 0, bong: 0, dt: 0 };
        const h = hnAll[dstrRow];
        h.khach++; h.dt += dtRow;
        if (loai === 'LICH NONG') h.nong++; else if (loai === 'LICH GOI') h.goi++; else if (loai === 'LICH TOI UU') h.toiuu++;
        if (isBong) h.bong++;
      } else if (listed) {
        // Khách khớp DS MKT (KHÔNG phải HN) -> vào LICH_SU_DS (báo cáo cá nhân + tổng)
        const key = mk + '|' + dstrRow;
        if (!agg[key]) agg[key] = { mk: mk, d: dObj, nong: 0, goi: 0, toiuu: 0, khach: 0, dt: 0, bong: 0, dtHN: 0, dtMB: 0 };
        const o = agg[key];
        if (lamMB) {
          // MB: chỉ cộng DOANH THU cho cá nhân; đánh dấu dtMB để dashboard TRỪ khỏi team. KHÔNG đếm khách/lịch/bong.
          o.dt += dtRow; o.dtMB += dtRow;
        } else {
          o.khach++; o.dt += dtRow;
          if (loai === 'LICH NONG') o.nong++; else if (loai === 'LICH GOI') o.goi++; else if (loai === 'LICH TOI UU') o.toiuu++;
          if (isBong) o.bong++;
        }
      } else {
        // ⛑️ KHÔNG khớp marketing + KHÔNG phải HN/MB/nguồn -> VẪN GHI NHẬN vào nguồn "Chưa gán"
        // (trước đây rơi vào đây là BỎ QUA -> MẤT doanh thu). Đếm đủ khách/lịch/bong để tổng không thiếu.
        const nkey = 'Chưa gán|' + dstrRow;
        if (!nguonAll[nkey]) nguonAll[nkey] = { nguon: 'Chưa gán', d: dObj, khach: 0, nong: 0, goi: 0, toiuu: 0, bong: 0, dt: 0 };
        const g2 = nguonAll[nkey];
        g2.khach++; g2.dt += dtRow;
        if (loai === 'LICH NONG') g2.nong++; else if (loai === 'LICH GOI') g2.goi++; else if (loai === 'LICH TOI UU') g2.toiuu++;
        if (isBong) g2.bong++;
      }
    }
  });

  let ds = ss.getSheetByName('LICH_SU_DS'); if (!ds) ds = ss.insertSheet('LICH_SU_DS');
  ds.clear();
  ds.getRange(1, 1, 1, 10).setValues([['Ngày', 'Marketing', 'Lịch nóng', 'Lịch gối', 'Lịch tối ưu', 'Khách', 'Doanh thu', 'Khách bong', 'DT HN', 'DT MB']]);
  const rows = Object.keys(agg).map(function(k){ const o = agg[k]; return [o.d, o.mk, o.nong, o.goi, o.toiuu, o.khach, o.dt, o.bong, o.dtHN, o.dtMB || 0]; });
  if (rows.length) {
    ds.getRange(2, 1, rows.length, 10).setValues(rows);
    ds.getRange(2, 1, rows.length, 1).setNumberFormat('dd/MM/yyyy');
  }

  // Kho HN riêng: khách HN CHƯA gán MKT theo ngày (đếm đủ chỉ số để cộng vào tổng dashboard)
  let dhn = ss.getSheetByName('LICH_SU_HN'); if (!dhn) dhn = ss.insertSheet('LICH_SU_HN');
  dhn.clear();
  dhn.getRange(1, 1, 1, 7).setValues([['Ngày', 'Khách', 'Lịch nóng', 'Lịch gối', 'Lịch tối ưu', 'Khách bong', 'Doanh thu']]);
  const hnRows = Object.keys(hnAll).map(function(k){ const h = hnAll[k]; return [h.d, h.khach, h.nong, h.goi, h.toiuu, h.bong, h.dt]; });
  if (hnRows.length) {
    dhn.getRange(2, 1, hnRows.length, 7).setValues(hnRows);
    dhn.getRange(2, 1, hnRows.length, 1).setNumberFormat('dd/MM/yyyy');
  }

  // Kho NGUỒN team (Hotline...) theo ngày -> dashboard cộng vào TỔNG team (phần Cơ sở HCM). Báo cáo cá nhân BỎ QUA kho này.
  let dng = ss.getSheetByName('LICH_SU_NGUON'); if (!dng) dng = ss.insertSheet('LICH_SU_NGUON');
  dng.clear();
  dng.getRange(1, 1, 1, 8).setValues([['Ngày', 'Nguồn', 'Khách', 'Lịch nóng', 'Lịch gối', 'Lịch tối ưu', 'Khách bong', 'Doanh thu']]);
  const ngRows = Object.keys(nguonAll).map(function(k){ const g = nguonAll[k]; return [g.d, g.nguon, g.khach, g.nong, g.goi, g.toiuu, g.bong, g.dt]; });
  if (ngRows.length) {
    dng.getRange(2, 1, ngRows.length, 8).setValues(ngRows);
    dng.getRange(2, 1, ngRows.length, 1).setNumberFormat('dd/MM/yyyy');
  }
}

// CHẨN ĐOÁN: liệt kê các TKQC đang được gán cho 1 Marketing (theo LICH_SU) + tổng chi tiêu mỗi TK.
// Dùng để soi TK nào bị gán nhầm gây lệch chi tiêu. Xem tab SOI_TK_MKT.
function soiTK_HoangAnh() { return soiTKcuaMKT('Hoàng Anh'); }

function soiTKcuaMKT(tenMKT) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const mktList = docDSMKT(ss);
  const target = chuanHoaTxt(tenMKT);
  const entry = mktList.filter(function(x){ return chuanHoaTxt(x.ten) === target; })[0];
  const ls = ss.getSheetByName('LICH_SU');
  if (!ls || ls.getLastRow() < 2) { ss.toast('LICH_SU trống', 'Soi TK', 6); return; }
  const data = ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues();
  const acc = {};   // id -> {ten, nguon, ct, days}
  data.forEach(function(r){
    if (chuanHoaTxt(r[3]) !== target) return;
    const id = String(r[1]);
    if (!acc[id]) acc[id] = { ten: r[2], nguon: r[4], ct: 0, days: {} };
    acc[id].ct += Number(r[5] || 0);
    acc[id].days[dmy(r[0])] = true;
  });
  const ids = Object.keys(acc).sort(function(a, b){ return acc[b].ct - acc[a].ct; });
  const out = [['ID TKQC', 'Tên TK', 'Nguồn', 'Tổng chi tiêu (gốc, LICH_SU)', 'Số ngày có CT']];
  let tong = 0;
  ids.forEach(function(id){ const a = acc[id]; out.push([id, a.ten, a.nguon, a.ct, Object.keys(a.days).length]); tong += a.ct; });
  out.push(['— TỔNG —', '', '', tong, '']);

  let sh = ss.getSheetByName('SOI_TK_MKT'); if (!sh) sh = ss.insertSheet('SOI_TK_MKT'); sh.clear();
  const tieu = 'TKQC gán cho: ' + tenMKT + '   ·   ' + ids.length + ' tài khoản   ·   Từ khoá nhận diện: ' +
    (entry ? entry.keys.join(' | ') : '⚠️ KHÔNG thấy trong DANH_SACH_MKT');
  sh.getRange(1, 1).setValue(tieu).setFontWeight('bold').setBackground('#16233f').setFontColor('#fff');
  sh.getRange(1, 1, 1, 5).merge();
  sh.getRange(3, 1, out.length, 5).setValues(out);
  sh.getRange(3, 1, 1, 5).setFontWeight('bold').setBackground('#3a5a99').setFontColor('#fff');
  const n = out.length - 1;
  if (n > 0) {
    sh.getRange(4, 1, n, 1).setNumberFormat('@'); SpreadsheetApp.flush();
    const idcol = []; for (let i = 1; i < out.length; i++) idcol.push([String(out[i][0])]);
    sh.getRange(4, 1, n, 1).setValues(idcol);
    sh.getRange(4, 4, n, 1).setNumberFormat('#,##0');
  }
  sh.setColumnWidth(1, 185); sh.setColumnWidth(2, 350); sh.setColumnWidth(3, 110); sh.setColumnWidth(4, 190); sh.setColumnWidth(5, 120);
  sh.setFrozenRows(3);
  ss.toast(ids.length + ' TKQC gán cho ' + tenMKT + ' · tổng CT gốc ' + Math.round(tong).toLocaleString('vi-VN'), 'Soi TK', 12);
}

// CHẨN ĐOÁN MB: tìm mọi dòng có token "MB" trong file đối soát, cho biết MB nằm ở cột nào,
// cột Ghi chú hệ thống nhận diện là cột nào, và khách có phải nguồn SG không. Xem tab SOI_MB.
function soiMB() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let ext; try { ext = SpreadsheetApp.openById(DOISOAT_ID); } catch (e) { ss.toast('Không mở được file đối soát', 'Soi MB', 6); return; }
  const out = [['Tab', 'Cột Ghi chú hệ thống nhận', 'Marketing (gốc)', 'Ghi chú (gốc)', 'MB nằm ở cột', 'Bắt được MB?', 'SG?']];
  ext.getSheets().forEach(function(sh){
    if (chuanHoaTxt(sh.getName()).indexOf('THANG') < 0) return;
    const data = sh.getDataRange().getValues();
    let hr = -1, col = {};
    for (let i = 0; i < data.length; i++) { let f = false; data[i].forEach(function(c){ if (chuanHoaTxt(c) === 'MA KHACH HANG') f = true; }); if (f) { hr = i; data[i].forEach(function(c, idx){ col[chuanHoaTxt(c)] = idx; }); break; } }
    if (hr < 0) return;
    const headers = Object.keys(col);
    let cGhiChu = null; headers.forEach(function(h){ if (cGhiChu == null && h.indexOf('GHI CHU') >= 0) cGhiChu = col[h]; });
    const ghiChuHeader = (cGhiChu == null) ? '⚠️ KHÔNG thấy cột Ghi chú' : headers.find(function(h){ return col[h] === cGhiChu; });
    const cMkt = col['MARKETING'], cMa = col['MA KHACH HANG'];
    for (let i = hr + 1; i < data.length; i++) {
      const r = data[i]; const ma = String(r[cMa] || '').trim(); if (!ma || chuanHoaTxt(ma) === 'TONG') continue;
      // tìm token MB ở từng cột của hàng
      let cotCoMB = [];
      r.forEach(function(c, idx){ const t = chuanHoaTxt(c).split(/[^A-Z0-9]+/); if (t.indexOf('MB') >= 0) cotCoMB.push(headers.find(function(h){ return col[h] === idx; }) || ('cột#' + idx)); });
      if (!cotCoMB.length) continue;
      // Cách hệ thống đang bắt: quét toàn hàng trừ cột Marketing
      let noteStr = ''; r.forEach(function(c, idx){ if (idx !== cMkt) noteStr += ' ' + chuanHoaTxt(c); });
      const batDuoc = noteStr.split(/[^A-Z0-9]+/).indexOf('MB') >= 0;
      const mch = cMkt != null ? chuanHoaTxt(r[cMkt]) : '';
      const isSG = mch.indexOf('HCM') >= 0 || mch.indexOf('SG') >= 0;
      out.push([sh.getName(), ghiChuHeader, String(cMkt != null ? r[cMkt] : ''), String(cGhiChu != null ? r[cGhiChu] : '(không có)'), cotCoMB.join(', '), batDuoc ? 'CÓ' : '❌ KHÔNG', isSG ? 'SG' : 'không']);
    }
  });
  let sh = ss.getSheetByName('SOI_MB'); if (!sh) sh = ss.insertSheet('SOI_MB'); sh.clear();
  sh.getRange(1, 1, out.length, 7).setValues(out);
  sh.getRange(1, 1, 1, 7).setFontWeight('bold').setBackground('#16233f').setFontColor('#fff');
  sh.setColumnWidth(1, 130); sh.setColumnWidth(2, 190); sh.setColumnWidth(3, 220); sh.setColumnWidth(4, 240); sh.setColumnWidth(5, 170); sh.setColumnWidth(6, 110); sh.setColumnWidth(7, 70);
  sh.setFrozenRows(1);
  ss.toast('Soi MB: ' + (out.length - 1) + ' dòng có token MB. Xem tab SOI_MB.', 'Soi MB', 10);
}

// CHẨN ĐOÁN: soi tên cột + tìm cột nào chứa "HN" trong file đối soát (xem ở Nhật ký thực thi)
function xemCotDoiSoat() {
  let ext; try { ext = SpreadsheetApp.openById(DOISOAT_ID); } catch (e) { Logger.log('Không mở được file đối soát'); return; }
  let done = false;
  ext.getSheets().forEach(function(sh){
    if (done) return;
    if (chuanHoaTxt(sh.getName()).indexOf('THANG') < 0) return;
    const data = sh.getDataRange().getValues();
    let hr = -1;
    for (let i = 0; i < data.length; i++) {
      let f = false; data[i].forEach(function(c){ if (chuanHoaTxt(c) === 'MA KHACH HANG') f = true; });
      if (f) { hr = i; break; }
    }
    if (hr < 0) return;
    done = true;
    Logger.log('=== TAB: ' + sh.getName() + ' ===');
    Logger.log('CÁC CỘT: ' + JSON.stringify(data[hr]));
    for (let c = 0; c < data[hr].length; c++) {
      let cnt = 0; const samples = [];
      for (let i = hr + 1; i < Math.min(data.length, hr + 400); i++) {
        const v = chuanHoaTxt(data[i][c]);
        if (v.indexOf('HN') >= 0) { cnt++; if (samples.length < 3) samples.push(String(data[i][c])); }
      }
      if (cnt > 0) Logger.log('>> Cột [' + data[hr][c] + '] có ' + cnt + ' ô chứa "HN". VD: ' + JSON.stringify(samples));
    }
  });
}

// chiThayDoi=true  -> CHỈ dựng lại marketing CÓ hoạt động 3 ngày gần đây (hoặc sheet chưa có).
// veThang=true     -> với sheet NĂM NAY đã có: chỉ VẼ LẠI KHỐI THÁNG HIỆN TẠI (tháng cũ giữ nguyên,
//                     nhanh hẳn); nếu không vẽ nhanh được thì tự dựng FULL tháng đó.
// Cả hai bỏ trống (mặc định) -> dựng lại TẤT CẢ, FULL như cũ (đường lui an toàn, số luôn đúng).
// tinhCaCu = true  -> TÍNH LẠI CẢ T4–T6 (dùng khi chạy tay tinhLaiTatCaThang để "chốt" số 1 lần).
// tinhCaCu = false -> (mặc định) ĐÓNG BĂNG T4–T6: giữ nguyên số đã có trên bảng, chỉ tính lại T7+.
function dungBaoCaoCaNhan(chiThayDoi, veThang, tinhCaCu) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ls = ss.getSheetByName('LICH_SU');
  if (!ls || ls.getLastRow() < 2) throw new Error('LICH_SU trống — chạy chốt ngày trước');
  const ads = ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues();
  const dsSheet = ss.getSheetByName('LICH_SU_DS');
  const dsRows = (dsSheet && dsSheet.getLastRow() > 1) ? dsSheet.getRange(2, 1, dsSheet.getLastRow() - 1, 10).getValues() : [];  // đọc đủ 10 cột (cần DT MB ở cột 10)

  const adsByMkt = {};
  ads.forEach(function(r){
    if (_mucDichDacThu(r[2])) return;   // ③ TK đặc thù (Tuyển dụng/PR) -> KHÔNG tính vào báo cáo cá nhân
    const mk = r[3]; if (!mk || mk === 'Chưa gán' || String(mk).indexOf('⚠️') >= 0) return;
    (adsByMkt[mk] = adsByMkt[mk] || []).push(r);
  });
  const dsByMkt = {};
  dsRows.forEach(function(r){
    const mk = r[1]; if (!mk) return;
    (dsByMkt[mk] = dsByMkt[mk] || {})[dmy(r[0])] = { nong: Number(r[2] || 0), goi: Number(r[3] || 0), toiuu: Number(r[4] || 0), khach: Number(r[5] || 0), dt: Number(r[6] || 0), bong: Number(r[7] || 0), dtMB: Number(r[9] || 0) };
  });

  // Gộp mọi marketing xuất hiện ở ads HOẶC đối soát (marketing mới chắc chắn có sheet)
  const allMk = {};
  Object.keys(adsByMkt).forEach(function(mk){ allMk[mk] = true; });
  Object.keys(dsByMkt).forEach(function(mk){ allMk[mk] = true; });

  // Mốc "có thay đổi gần đây" = 3 ngày. Marketing có ads/đối soát trong 3 ngày -> cần vẽ lại.
  const curYear = new Date().getFullYear();
  const moc3 = new Date(new Date().getTime() - 3 * 24 * 3600 * 1000);
  function coHoatDongGanDay(aRows, dMap){
    for (let i = 0; i < aRows.length; i++){ const d = toDate(aRows[i][0]); if (d instanceof Date && !isNaN(d.getTime()) && d >= moc3) return true; }
    const ks = Object.keys(dMap);
    for (let j = 0; j < ks.length; j++){ const p = ks[j].split('/'); const d2 = new Date(Number(p[2]), Number(p[1]) - 1, Number(p[0])); if (d2 >= moc3) return true; }
    return false;
  }

  _napCacheLHD(ss, 31);   // TỐI ƯU: đọc kho LHĐ 1 lần cho cả lượt dựng (thay vì quét lại mỗi sheet)
  let n = 0, boQua = 0;
  try {
  Object.keys(allMk).forEach(function(mk){
    const aRows = adsByMkt[mk] || [], dMap = dsByMkt[mk] || {};
    const years = {};
    aRows.forEach(function(r){ years[toDate(r[0]).getFullYear()] = true; });
    Object.keys(dMap).forEach(function(dstr){ years[Number(dstr.split('/')[2])] = true; });
    const ganDay = coHoatDongGanDay(aRows, dMap);
    Object.keys(years).sort().forEach(function(y){
      const yy = Number(y);
      if (chiThayDoi) {
        const daCo = !!ss.getSheetByName('BC - ' + mk + ' - ' + yy);
        const canVe = (yy >= curYear) ? ganDay : false;   // năm cũ đã chốt -> không vẽ lại; năm nay chỉ vẽ nếu có hoạt động gần đây
        if (daCo && !canVe) { boQua++; return; }           // sheet đã có + không cần vẽ -> BỎ QUA
      }
      // veThang: năm nay + sheet đã có -> chỉ vẽ lại khối THÁNG HIỆN TẠI. Không được -> full.
      if (veThang && yy === curYear && ss.getSheetByName('BC - ' + mk + ' - ' + yy)) {
        if (veLaiThangHienTai(ss, mk, yy, aRows, dMap)) { n++; return; }
      }
      buildSheetYear(ss, mk, yy, aRows, dMap, tinhCaCu); n++;
    });
  });
  } finally { _LHD_CACHE = null; }   // TẮT cache -> không rò sang lần gọi khác
  // GỠ dựng chi phí đặc thù khỏi đây: nay chạy RIÊNG lúc 22h15 (hàm chay2215_DacThu) để giảm tải khâu vẽ.
  // Muốn cập nhật tab đặc thù ngoài lịch -> chạy tay dungChiPhiDacThu().
  ss.toast('Đã dựng ' + n + ' báo cáo' + (chiThayDoi ? ' · bỏ qua ' + boQua + ' không đổi' : ''), 'OK', 6);
}

// ⭐ CỜ BẬT/TẮT khối ③ LẬP HOÁ ĐƠN trong báo cáo cá nhân.
//    Gạt = false -> TẮT NGAY, báo cáo về y hệt như cũ (đường lui an toàn).
const LHD_BAT = true;

// TỐI ƯU: đọc kho LICH_SU_LHD MỘT LẦN cho cả lượt dựng báo cáo, gom sẵn theo
// { marketing: { năm: { tháng: {accId:{...}} } } }. _docLHD sẽ lấy từ cache này thay vì
// quét lại sheet cho từng marketing-năm (trước đây quét thừa N lần). Cache chỉ sống trong
// 1 lượt dungBaoCaoCaNhan (bật đầu lượt, tắt cuối lượt) -> KHÔNG rò/ảnh hưởng nơi khác.
var _LHD_CACHE = null;
function _napCacheLHD(ss, nDays) {
  nDays = nDays || 31;
  const cache = {};
  const sh = ss.getSheetByName('LICH_SU_LHD');
  if (sh && sh.getLastRow() >= 2) {
    const data = sh.getRange(2, 1, sh.getLastRow() - 1, 6).getValues();
    data.forEach(function(r){
      const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      if (_mucDichDacThu(r[2])) return;   // đặc thù -> KHÔNG vào khối ③ / Số Tiền LHĐ của báo cáo cá nhân
      const y = d.getFullYear(), m = d.getMonth() + 1, ngay = d.getDate(), id = String(r[1]);
      if (ngay < 1 || ngay > nDays) return;
      const mkC = chuanHoaTxt(r[3]);
      const nguon = _nguonTK(r[2]);
      if (!cache[mkC]) cache[mkC] = {};
      if (!cache[mkC][y]) cache[mkC][y] = {};
      const res = cache[mkC][y];
      if (!res[m]) res[m] = {};
      const key = id + '|' + chuanHoaTxt(r[2]);   // GOM THEO ID + TÊN -> đổi tên TK thì tách dòng riêng
      if (!res[m][key]) res[m][key] = { ten: r[2], id: id, card: String(r[4] || ''), nguon: nguon, tong: 0, d: {} };
      const tien = Number(r[5] || 0);
      res[m][key].tong += tien;
      res[m][key].d[ngay] = (res[m][key].d[ngay] || 0) + tien;
    });
  }
  _LHD_CACHE = cache;
}

// Đọc kho LICH_SU_LHD -> { tháng: { accId: {ten, id, card, nguon, tong, d:{ngày:tiền}} } } cho 1 marketing + năm.
// Kho rỗng / chưa có -> trả {} -> khối ③ tự bỏ qua. (nDays: số ngày lưới, mặc định 31)
function _docLHD(ss, mk, year, nDays) {
  nDays = nDays || 31;
  if (_LHD_CACHE) {   // TỐI ƯU: đã đọc kho 1 lần -> lấy từ cache, khỏi quét lại sheet
    const mkC = chuanHoaTxt(mk);
    return (_LHD_CACHE[mkC] && _LHD_CACHE[mkC][year]) ? _LHD_CACHE[mkC][year] : {};
  }
  const res = {};   // { tháng: { accId: {ten, id, card, nguon, tong, d} } }
  const sh = ss.getSheetByName('LICH_SU_LHD');
  if (!sh || sh.getLastRow() < 2) return res;
  const data = sh.getRange(2, 1, sh.getLastRow() - 1, 6).getValues();
  const target = chuanHoaTxt(mk);
  data.forEach(function(r){
    const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
    if (_mucDichDacThu(r[2])) return;   // đặc thù -> KHÔNG vào khối ③ / Số Tiền LHĐ của báo cáo cá nhân
    if (d.getFullYear() !== year) return;
    if (chuanHoaTxt(r[3]) !== target) return;
    const m = d.getMonth() + 1, ngay = d.getDate(), id = String(r[1]);
    if (ngay < 1 || ngay > nDays) return;
    const nguon = _nguonTK(r[2]);   // Bóc nguồn theo tên TK: Coremind / Apex / GPI / Khác
    if (!res[m]) res[m] = {};
    const key = id + '|' + chuanHoaTxt(r[2]);   // GOM THEO ID + TÊN -> đổi tên TK thì tách dòng riêng
    if (!res[m][key]) res[m][key] = { ten: r[2], id: id, card: String(r[4] || ''), nguon: nguon, tong: 0, d: {} };
    const tien = Number(r[5] || 0);
    res[m][key].tong += tien;
    res[m][key].d[ngay] = (res[m][key].d[ngay] || 0) + tien;
  });
  return res;
}

// ID được coi là SẠCH: toàn chữ số, ≥ 5 ký tự (ID scientific/hỏng sẽ chứa E, dấu chấm, phẩy...)
function _idSach(s){ return /^\d{5,}$/.test(String(s || '').trim()); }

// ============================================================================
// SỬA ID TKQC HỎNG trong kho LICH_SU (dạng "2,07481E+15" do coerce cũ).
// Dò lại ID GỐC theo Tên TK từ các kho SẠCH (LICH_SU_LHD, DATA_TONG, DATA_HOMQUA,
// và chính các dòng ID sạch sẵn có trong LICH_SU). Ghi lại LICH_SU với ID ép TEXT.
// Độc lập, chạy 1 lần: suaIDTKQC(). Sau đó chạy capNhatDoiSoat để dựng lại báo cáo.
// ============================================================================
function suaIDTKQC() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const map = {};   // chuanHoaTxt(Tên TK) -> ID sạch
  function nap(sheetName, colTen, colId){
    const sh = ss.getSheetByName(sheetName);
    if (!sh || sh.getLastRow() < 2) return;
    const w = Math.max(colTen, colId) + 1;
    sh.getRange(2, 1, sh.getLastRow() - 1, w).getValues().forEach(function(r){
      const ten = chuanHoaTxt(r[colTen]); const id = String(r[colId] || '').trim();
      if (ten && _idSach(id) && !map[ten]) map[ten] = id;   // ưu tiên ID sạch gặp đầu tiên
    });
  }
  nap('LICH_SU_LHD', 2, 1);   // Tên C(2), ID B(1)
  nap('DATA_TONG',  0, 1);    // Tên A(0), ID B(1)
  nap('DATA_HOMQUA',0, 1);
  nap('LICH_SU',    2, 1);    // Tên C(2), ID B(1) — thu ID sạch sẵn có

  const ls = ss.getSheetByName('LICH_SU');
  if (!ls || ls.getLastRow() < 2) { ss.toast('LICH_SU trống', 'Sửa ID', 5); return; }
  const rng = ls.getRange(2, 1, ls.getLastRow() - 1, 7);
  const data = rng.getValues();
  let sua = 0; const chuaSua = [];
  data.forEach(function(r){
    const id = String(r[1] || '').trim();
    if (_idSach(id)) return;                       // đã sạch -> bỏ qua
    const clean = map[chuanHoaTxt(r[2])];          // dò theo Tên TK
    if (clean) { r[1] = clean; sua++; }
    else if (chuaSua.indexOf(r[2]) < 0) chuaSua.push(r[2]);   // không tìm được nguồn sạch
  });
  // Ghi lại: ép TEXT cột ID (B) + NGÀY (A) TRƯỚC rồi mới setValues
  ls.getRange(2, 2, data.length, 1).setNumberFormat('@');
  ls.getRange(2, 1, data.length, 1).setNumberFormat('dd/MM/yyyy');
  SpreadsheetApp.flush();
  data.forEach(function(r){ r[0] = toDate(r[0]); r[1] = String(r[1]); });
  rng.setValues(data);

  const msg = 'Đã sửa ' + sua + ' ID hỏng.' + (chuaSua.length ? ' Chưa tìm được nguồn sạch cho ' + chuaSua.length + ' TK.' : ' Tất cả đã sạch.');
  ss.toast(msg, 'Sửa ID TKQC', 10);
  Logger.log(msg + (chuaSua.length ? '\nTK chưa sửa: ' + JSON.stringify(chuaSua) : ''));
  return { sua: sua, chuaSua: chuaSua };
}

// ============================================================================
// SOI KHỚP ID: Chi tiêu (LICH_SU) ⇄ Lập hoá đơn (LICH_SU_LHD) cho 1 tháng.
// Độc lập 100% — CHỈ ĐỌC 2 kho + ghi ra tab "SOI_KHOP_ID". Không đụng auto/ads/chốt.
// Chạy: soiKhopID_T7()  (hoặc soiKhopIDChiTieuLHD(2026, 7))
// ============================================================================
function soiKhopID_T7() { return soiKhopIDChiTieuLHD(2026, 7); }

function soiKhopIDChiTieuLHD(year, month) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // 1) Chi tiêu (LICH_SU): { mkt: { id: {ten, ct} } } — chỉ TK có chi tiêu > 0 trong tháng
  const chi = {};
  const ls = ss.getSheetByName('LICH_SU');
  if (ls && ls.getLastRow() > 1) {
    ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues().forEach(function(r){
      const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      if (d.getFullYear() !== year || d.getMonth() + 1 !== month) return;
      const mk = r[3]; if (!mk || mk === 'Chưa gán' || String(mk).indexOf('⚠️') >= 0) return;
      const id = String(r[1]).trim(), ct = Number(r[5] || 0);
      if (!id) return;
      if (!chi[mk]) chi[mk] = {};
      if (!chi[mk][id]) chi[mk][id] = { ten: r[2], ct: 0 };
      chi[mk][id].ct += ct;
    });
  }

  // 2) Lập hoá đơn (LICH_SU_LHD): { mkt: { id: {ten, lhd} } }
  const lhd = {};
  const lh = ss.getSheetByName('LICH_SU_LHD');
  if (lh && lh.getLastRow() > 1) {
    lh.getRange(2, 1, lh.getLastRow() - 1, 6).getValues().forEach(function(r){
      const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      if (d.getFullYear() !== year || d.getMonth() + 1 !== month) return;
      const mk = r[3]; if (!mk) return;
      const id = String(r[1]).trim(), tien = Number(r[5] || 0);
      if (!id) return;
      if (!lhd[mk]) lhd[mk] = {};
      if (!lhd[mk][id]) lhd[mk][id] = { ten: r[2], lhd: 0 };
      lhd[mk][id].lhd += tien;
    });
  }

  // 3) Đối chiếu theo từng marketing
  const allMk = {}; Object.keys(chi).forEach(function(m){ allMk[m] = true; }); Object.keys(lhd).forEach(function(m){ allMk[m] = true; });
  const out = [['Marketing', 'ID TKQC', 'Tên TK', 'Trạng thái', 'Chi tiêu', 'LHĐ']];
  const txtRows = [];   // dòng cần ép cột B (ID) về TEXT
  let sKhop = 0, sChiOnly = 0, sLhdOnly = 0;

  Object.keys(allMk).sort().forEach(function(mk){
    const c = chi[mk] || {}, l = lhd[mk] || {};
    const ids = {}; Object.keys(c).forEach(function(i){ ids[i] = true; }); Object.keys(l).forEach(function(i){ ids[i] = true; });
    Object.keys(ids).sort().forEach(function(id){
      const inC = !!c[id], inL = !!l[id];
      let tt;
      if (inC && inL) { tt = '✅ Khớp'; sKhop++; }
      else if (inC && !inL) { tt = '⚠️ Chỉ có chi tiêu (chưa/không phát sinh LHĐ)'; sChiOnly++; }
      else { tt = '❗ Chỉ có LHĐ (không thấy chi tiêu)'; sLhdOnly++; }
      const ten = (c[id] && c[id].ten) || (l[id] && l[id].ten) || '';
      out.push([mk, id, ten, tt, inC ? c[id].ct : '', inL ? l[id].lhd : '']);
      txtRows.push(out.length);   // 1-based row trong khối dữ liệu (sẽ +1 khi ghi vì header ở dòng 1)
    });
  });

  // 4) Ghi ra tab SOI_KHOP_ID
  let sh = ss.getSheetByName('SOI_KHOP_ID'); if (!sh) sh = ss.insertSheet('SOI_KHOP_ID');
  sh.clear();
  const tieu = 'SOI KHỚP ID · Chi tiêu ⇄ Lập hoá đơn · Tháng ' + month + '/' + year +
    '   —   Khớp: ' + sKhop + ' · Chỉ chi tiêu: ' + sChiOnly + ' · Chỉ LHĐ: ' + sLhdOnly;
  sh.getRange(1, 1).setValue(tieu).setFontWeight('bold').setFontColor('#fff').setBackground('#16233f').setFontSize(12);
  sh.getRange(1, 1, 1, 6).merge();
  sh.getRange(3, 1, out.length, 6).setValues(out);
  sh.getRange(3, 1, 1, 6).setFontWeight('bold').setBackground('#3a5a99').setFontColor('#fff');
  // Cột ID (B) -> TEXT (ép '@' + flush + ghi lại chuỗi) để không rơi scientific & so khớp chuẩn xác
  const nData = out.length - 1;
  if (nData > 0) {
    sh.getRange(4, 2, nData, 1).setNumberFormat('@');
    SpreadsheetApp.flush();
    const idCol = []; for (let i = 1; i < out.length; i++) idCol.push([String(out[i][1])]);
    sh.getRange(4, 2, nData, 1).setValues(idCol);
    sh.getRange(4, 5, nData, 2).setNumberFormat('#,##0');
  }
  sh.setColumnWidth(1, 150); sh.setColumnWidth(2, 175); sh.setColumnWidth(3, 300);
  sh.setColumnWidth(4, 320); sh.setColumnWidth(5, 130); sh.setColumnWidth(6, 130);
  sh.setFrozenRows(3);
  ss.toast('Soi khớp ID T' + month + ': Khớp ' + sKhop + ' · Chỉ chi tiêu ' + sChiOnly + ' · Chỉ LHĐ ' + sLhdOnly, 'SOI_KHOP_ID', 8);
  Logger.log(tieu);
  return { khop: sKhop, chiOnly: sChiOnly, lhdOnly: sLhdOnly };
}

// ============================================================================
// ĐỌC LẠI KHỐI "TỔNG THEO THÁNG" CŨ trên sheet -> { nhãn(trim): { tháng: giá trị } }.
// Dùng để ĐÓNG BĂNG T4–T6 (giữ nguyên số cũ, không tính lại). Chỉ đọc trong vùng
// từ dòng "📊 TỔNG THEO THÁNG" tới trước "🔎 LỌC" -> không đụng KHỐI LỌC / block tháng.
// ============================================================================
function _docTongCu(sh) {
  const res = {};
  if (!sh) return res;
  const lastR = sh.getLastRow(), lastC = sh.getLastColumn();
  if (lastR < 2 || lastC < 2) return res;
  const vals = sh.getRange(1, 1, lastR, lastC).getValues();
  // 1) Tìm dòng tiêu đề "TỔNG THEO THÁNG"
  let hdrRow = -1;
  for (let i = 0; i < vals.length; i++) {
    if (String(vals[i][0]).indexOf('TỔNG THEO THÁNG') >= 0) { hdrRow = i; break; }
  }
  if (hdrRow < 0 || hdrRow + 1 >= vals.length) return res;
  // 2) Dòng kế tiếp: "Chỉ số | Tháng 4 | Tháng 5 | ..." -> map cột -> tháng
  const dateRow = hdrRow + 1;
  const colThang = {};
  for (let c = 1; c < lastC; c++) {
    const mt = String(vals[dateRow][c]).match(/Tháng\s*(\d+)/);
    if (mt) colThang[c] = Number(mt[1]);
  }
  // 3) Đọc các dòng số cho tới khi gặp khối LỌC
  for (let r = dateRow + 1; r < vals.length; r++) {
    const lb = String(vals[r][0]).trim();
    if (lb.indexOf('🔎') >= 0 || lb.indexOf('LỌC THEO') >= 0) break;
    if (!lb) continue;
    for (const c in colThang) {
      const v = vals[r][c];
      if (v === '' || v == null) continue;
      if (!res[lb]) res[lb] = {};
      res[lb][colThang[c]] = v;
    }
  }
  return res;
}

// ============================================================================
// CHẠY TAY 1 LẦN — "chốt" lại toàn bộ T4–T6 theo công thức cuối cùng.
// Sau khi chạy hàm này, các lần dựng sau (trigger/vẽ nhanh) sẽ GIỮ NGUYÊN T4–T6,
// chỉ tính lại T7 trở đi. Muốn tính lại toàn bộ 1 lần nữa -> chạy lại hàm này.
// ============================================================================
function tinhLaiTatCaThang() {
  dungBaoCaoCaNhan(false, false, true);                 // báo cáo cá nhân: tính lại cả T4–T6
  if (typeof dungDashboard === 'function') dungDashboard(true);   // Dashboard B: tính lại cả T4–T6
  SpreadsheetApp.getActiveSpreadsheet().toast('Đã tính lại TẤT CẢ các tháng ở báo cáo cá nhân + Dashboard (T4–T6 chốt theo công thức mới nhất).', 'Xong', 9);
}

function buildSheetYear(ss, mk, year, adsRows, dsMap, tinhCaCu) {
  const nDays = 31, nCol = 4 + nDays;   // đủ cho khối ③ LHĐ: Tên TK | ID TKQC | Số Thẻ | Tổng Tháng | ngày 1..31
  const dayHdr = []; for (let d = 1; d <= nDays; d++) dayHdr.push(d);
  const mkq = '"' + String(mk).replace(/"/g, '""') + '"';

  // tháng có dữ liệu
  const monthsSet = {};
  adsRows.forEach(function(r){ const d = toDate(r[0]); if (d.getFullYear() === year) monthsSet[d.getMonth() + 1] = true; });
  Object.keys(dsMap).forEach(function(s){ const p = s.split('/'); if (Number(p[2]) === year) monthsSet[Number(p[1])] = true; });
  const months = Object.keys(monthsSet).map(Number).sort(function(a, b){ return a - b; });

  const name = 'BC - ' + mk + ' - ' + year;
  let sh = ss.getSheetByName(name);

  // Đọc lại Từ ngày / Đến ngày cũ theo NHÃN (không theo vị trí cố định)
  let oldFrom = '', oldTo = '';
  if (sh) {
    const nr = Math.min(sh.getLastRow(), 40);
    if (nr > 0) {
      const av = sh.getRange(1, 1, nr, 2).getValues();
      for (let i = 0; i < av.length; i++) {
        const lab = String(av[i][0]).trim();
        if (lab === 'Từ ngày') oldFrom = av[i][1];
        if (lab === 'Đến ngày') oldTo = av[i][1];
      }
    }
  }
  // ĐÓNG BĂNG T4–T6: đọc lại khối "TỔNG THEO THÁNG" cũ (nhãn -> {tháng: giá trị}) TRƯỚC khi clear.
  // tinhCaCu = true -> bỏ qua, tính lại tất cả (dùng cho tinhLaiTatCaThang chốt số 1 lần).
  const frozenTong = (!tinhCaCu && sh) ? _docTongCu(sh) : {};
  if (!sh) sh = ss.insertSheet(name);
  sh.clear();
  try { sh.getRange(1, 1, sh.getMaxRows(), 1).shiftRowGroupDepth(-8); } catch (e) {}   // xoá nhóm gấp cũ (nếu có) trước khi dựng lại
  const SEP = locaSep(sh); // dấu phân cách hàm theo ngôn ngữ bảng ( , hoặc ; )

  const out = [];
  const F = []; // {r,c,f} công thức ghi bằng setFormula (tự đổi , -> ; theo locale)
  const mark = { title: [], hdr: [], monthHdr: [], khoiHdr: [], dateHdr: [], tong: [], ratioRows: [], labelRanges: [], idCells: [], groups: [], lhdCells: [] };
  // PHẦN B: đọc kho LHĐ cho marketing + năm này (rỗng nếu tắt cờ hoặc chưa có kho) -> vẽ khối ③
  const lhdByMonth = (typeof LHD_BAT !== 'undefined' && LHD_BAT) ? _docLHD(ss, mk, year, nDays) : {};
  function push(row){ out.push(row); return out.length; } // trả về số dòng (1-based)
  function pad(arr){ while (arr.length < nCol) arr.push(''); return arr; }
  function setF(r, c, f){ F.push({ r: r, c: c, f: (f.charAt(0) === '=' ? f : '=' + f) }); }

  // 1) Tiêu đề
  mark.title.push(push(pad(['BÁO CÁO CÁ NHÂN — ' + mk + ' — NĂM ' + year])));
  push(pad(['']));

  const A = 'LICH_SU', B = 'LICH_SU_DS';
  // Sinh 12 chỉ số cho 1 khối tổng, theo tiêu chí ngày (g,l)
  function metricRows(g, l){
    const S = SEP;
    const jul   = 'DATE(2026' + S + '7' + S + '1)';    // 01/07/2026 — mốc bắt đầu cộng VAT
    const eoJul = 'DATE(2026' + S + '7' + S + '31)';   // 31/07/2026 — hết tháng áp +11%
    const aug   = 'DATE(2026' + S + '8' + S + '1)';    // 01/08/2026 — từ đây VAT còn +9,1%
    // chi tiêu theo khoảng; extraNguon = lọc 1 nguồn; lo/hi = chặn dưới(>=) / chặn trên(<=) tuỳ chọn
    function _spend(extraNguon, lo, hi){
      let f = 'SUMIFS(' + A + '!F:F' + S + A + '!D:D' + S + mkq;
      if (extraNguon) f += S + A + '!E:E' + S + '"' + extraNguon + '"';
      f += S + A + '!A:A' + S + g + S + A + '!A:A' + S + l;
      if (lo) f += S + A + '!A:A' + S + '">="&' + lo;
      if (hi) f += S + A + '!A:A' + S + '"<="&' + hi;
      return f + ')';
    }
    // SUMIFS 1 cột (F=chi tiêu / G=SĐT) theo khoảng + các cặp tiêu chí phụ [cột, điều kiện]
    function _sumCol(col, extra){
      let f = 'SUMIFS(' + A + '!' + col + ':' + col + S + A + '!D:D' + S + mkq + S + A + '!A:A' + S + g + S + A + '!A:A' + S + l;
      (extra || []).forEach(function(p){ f += S + A + '!' + p[0] + ':' + p[0] + S + p[1]; });
      return f + ')';
    }
    // Chỉ marketing Phạm Ngọc Thắng mới loại GPI-không-SG khỏi KHỐI LỌC; marketing khác tính đầy đủ.
    const locGPI = _laMktLocGPI(mk);
    // TỔNG 1 cột LOẠI GPI-không-SG = (nguồn<>GPI) + (nguồn=GPI VÀ Tên TK chứa "SG")
    function _tongHopLe(col){ return _sumCol(col, [['E', '"<>GPI"']]) + '+' + _sumCol(col, [['E', '"GPI"'], ['C', '"*SG*"']]); }
    // Chi tiêu SỐ GỐC (KHÔNG VAT/phí).
    function ct(){ return locGPI ? _tongHopLe('F') : _sumCol('F', []); }          // Tổng chi tiêu
    function nsx(x){
      if (x === 'GPI') return locGPI ? _sumCol('F', [['E', '"GPI"'], ['C', '"*SG*"']]) : _sumCol('F', [['E', '"GPI"']]);   // NS GPI
      return _sumCol('F', [['E', '"' + x + '"']]);                                // COREMIND / APEX
    }
    function sdtx(){ return locGPI ? _tongHopLe('G') : _sumCol('G', []); }        // Số điện thoại
    function dsx(c){ return 'SUMIFS(' + B + '!' + c + ':' + c + S + B + '!B:B' + S + mkq + S + B + '!A:A' + S + g + S + B + '!A:A' + S + l + ')'; }
    return [
      ['Tổng chi tiêu', ct(), false],
      ['NS COREMIND', nsx('COREMIND'), false],
      ['NS APEX', nsx('APEX'), false],
      ['NS GPI', nsx('GPI'), false],
      ['Số điện thoại', sdtx(), false],
      ['Chi phí / SĐT', 'IFERROR(' + ct() + '/' + sdtx() + S + '0)', false],
      ['Lịch nóng', dsx('C'), false],
      ['Lịch gối', dsx('D'), false],
      ['Lịch tối ưu', dsx('E'), false],
      ['Khách đến cửa', dsx('F'), false],
      ['Tổng doanh thu', dsx('G'), false],
      ['Bill TB', 'IFERROR(' + dsx('G') + '/' + dsx('F') + S + '0)', false],
      ['Chi phí Ads / Doanh thu', 'IFERROR(' + ct() + '/' + dsx('G') + S + '0)', true]
    ];
  }
  function emitMetrics(g, l, valHdr){
    push(pad(['Chỉ số', valHdr]));
    const rows = metricRows(g, l);
    const start = out.length + 1;
    rows.forEach(function(m){ const r = push(pad([m[0], ''])); setF(r, 2, m[1]); if (m[2]) mark.ratioRows.push(r); });
    mark.labelRanges.push([start, rows.length]);
  }

  // 2) KHỐI TỔNG THEO THÁNG — chia mục ①②③④⑤ (giao diện như khối tổng Dashboard B); mỗi tháng 1 cột.
  //    Mỗi dòng: [nhãn, spec, %]. spec = số -> metricVal(idx); hàm(o,m) -> DÒNG MỚI (chỉ T8+); null -> để trống.
  // Công thức DÒNG MỚI (áp từ T8/2026, tháng trước để trống):
  //   • Doanh Thu Xét KPI = doanh thu hiện tại × 1.08 (undo phần /1.08 lúc ghi nhận -> doanh thu GỘP, chưa trừ VAT)
  //   • Doanh Thu Hoa Hồng = doanh thu hiện tại (đã /1.08), không tách nhánh
  //   • Số Tiền Lập Hoá Đơn = Tổng LHĐ tháng (đã VAT 8/10%) × 1.02 (+2%)
  //   • Ads / Doanh Thu Thực = Số tiền LHĐ / Doanh Thu Xét KPI
  function _tuT8(m){ return (year > 2026) || (year === 2026 && m >= 8); }
  function _gopVAT(m){ return ((year > 2026) || (year === 2026 && m >= 7)) ? 1.08 : 1; }   // T7+ có 8% VAT -> ×1.08 ra số thực; T4/5/6 không VAT -> ×1
  function _lhdThang(m){
    const L = lhdByMonth[m]; if (!L) return 0;
    let s = 0; Object.keys(L).forEach(function(id){ const a = L[id]; if (_loaiKhoiTong(mk, a.ten)) return; s += (a.tong || 0) * heSoVATLHD(a.nguon, year, m); });
    return s;
  }
  // SỐ TIỀN LẬP HOÁ ĐƠN = tổng theo TỪNG tài khoản: LHĐ (đã VAT 10%) × phí theo cụm (1.011 / 1.021).
  // GPI không "SG" -> KHÔNG cộng vào Số Tiền LHĐ tổng (vẫn hiện ở khối ③ chi tiết).
  function _soTienLHD(m){
    const L = lhdByMonth[m]; if (!L) return 0;
    let s = 0; Object.keys(L).forEach(function(id){ const a = L[id]; if (_loaiKhoiTong(mk, a.ten)) return; s += (a.tong || 0) * heSoVATLHD(a.nguon, year, m) * _phiTheLHD(a.ten); });
    return s;
  }
  function _dtXetKPI(o, m){ return o.dt * _gopVAT(m); }   // doanh thu KHÔNG chia VAT = số thực trên đối soát
  const B1_SEC = [
    ['① CHI PHÍ QUẢNG CÁO', [
      ['Tổng chi tiêu', 0, false], ['NS COREMIND', 1, false], ['NS APEX', 2, false], ['NS GPI', 14, false],
      ['Số Tiền Lập Hoá Đơn', function(o, m){ return Math.round(_soTienLHD(m)); }, false]
    ]],
    ['② LEAD (SỐ ĐIỆN THOẠI)', [
      ['Số điện thoại', 3, false], ['Chi phí / SĐT', 4, false]
    ]],
    ['③ KHÁCH ĐẾN CỬA', [
      ['Lịch nóng', 5, false], ['Lịch gối', 6, false], ['Lịch tối ưu', 7, false], ['Khách đến cửa', 8, false],
      ['Tỷ lệ bong', function(o){ return o.khach > 0 ? o.bong / o.khach : 0; }, true]   // Khách bong / Khách đến cửa (mọi tháng)
    ]],
    ['④ DOANH THU', [
      ['Doanh Thu Xét KPI', function(o, m){ return Math.round(_dtXetKPI(o, m)); }, false],
      ['   ├ Doanh thu HCM', function(o, m){ return Math.round((o.dt - (o.dtMB || 0)) * _gopVAT(m)); }, false],
      ['   └ HCM chuyển HN', function(o, m){ return Math.round((o.dtMB || 0) * _gopVAT(m)); }, false],
      ['Doanh Thu Hoa Hồng', function(o){ return Math.round(o.dt); }, false],   // = doanh thu hiện tại (mọi tháng, kể cả T4-T7)
      ['Bill TB', 12, false]
    ]],
    ['⑤ HIỆU QUẢ', [
      ['Ads / Doanh Thu Hoa Hồng', function(o, m){ const tuT7 = (year > 2026) || (year === 2026 && m >= 7); if (!tuT7) return ''; return o.dt > 0 ? _soTienLHD(m) / o.dt : 0; }, true],   // = Số tiền LHĐ / Doanh Thu Hoa Hồng (T7+)
      ['Ads / Doanh Thu Thực', function(o, m){ const dxk = _dtXetKPI(o, m); return dxk > 0 ? _soTienLHD(m) / dxk : 0; }, true]
    ]]
  ];
  function baseOfMonth(m){
    const o = { ct: 0, core: 0, apex: 0, gpi: 0, sdt: 0, nong: 0, goi: 0, toiuu: 0, khach: 0, dt: 0, dtMB: 0, bong: 0 };
    adsRows.forEach(function(r){
      const d = toDate(r[0]); if (d.getFullYear() !== year || d.getMonth() + 1 !== m) return;
      if (_loaiKhoiTong(mk, r[2])) return;   // GPI-không-SG của MKT Phạm Ngọc Thắng -> KHÔNG cộng vào KHỐI TỔNG (vẫn hiện ở chi tiết)
      const vf = heSoVATCt(r[4], year, m);
      const s = Number(r[5] || 0) * vf, p = Number(r[6] || 0);
      o.ct += s; o.sdt += p;
      if (r[4] === 'COREMIND') o.core += s; if (r[4] === 'APEX') o.apex += s; if (r[4] === 'GPI') o.gpi += s;
    });
    for (let dd = 1; dd <= nDays; dd++) {
      const key = ('0' + dd).slice(-2) + '/' + ('0' + m).slice(-2) + '/' + year;
      const x = dsMap[key]; if (x) { o.nong += x.nong; o.goi += x.goi; o.toiuu += x.toiuu; o.khach += x.khach; o.dt += x.dt; o.dtMB += (x.dtMB || 0); o.bong += (x.bong || 0); }
    }
    return o;
  }
  function metricVal(o, i){
    switch (i) {
      case 0: return o.ct; case 1: return o.core; case 2: return o.apex; case 3: return o.sdt;
      case 4: return o.sdt > 0 ? Math.round(o.ct / o.sdt) : 0;
      case 5: return o.nong; case 6: return o.goi; case 7: return o.toiuu; case 8: return o.khach;
      case 9: return o.dt;                    // Tổng doanh thu (gồm MB)
      case 10: return o.dt - (o.dtMB || 0);   // ├ Cơ sở HCM
      case 11: return o.dtMB || 0;            // └ HCM chuyển HN (khách MB)
      case 12: return o.khach > 0 ? Math.round(o.dt / o.khach) : 0;  // Bill TB
      case 13: return o.dt > 0 ? o.ct / o.dt : 0;                    // Chi phí Ads / DT
      case 14: return o.gpi;                                          // NS GPI
    }
    return 0;
  }
  const mData = {};
  months.forEach(function(m){ mData[m] = baseOfMonth(m); });

  mark.hdr.push(push(pad(['📊 TỔNG THEO THÁNG — NĂM ' + year])));
  mark.dateHdr.push(push(pad(['Chỉ số'].concat(months.map(function(m){ return 'Tháng ' + m; })))));
  B1_SEC.forEach(function(sec){
    mark.khoiHdr.push(push(pad([sec[0]])));            // tiêu đề mục (nền vàng như Dashboard B)
    const start = out.length + 1;
    sec[1].forEach(function(row){
      const spec = row[1];
      const lbTrim = String(row[0]).trim();
      const vals = months.map(function(m){
        // ĐÓNG BĂNG T4–T6: có số cũ trên bảng -> giữ nguyên (không tính lại). Chỉ T7+ tính lại.
        if (!tinhCaCu && m < 7 && frozenTong[lbTrim] != null &&
            frozenTong[lbTrim][m] != null && frozenTong[lbTrim][m] !== '') {
          return frozenTong[lbTrim][m];
        }
        if (spec == null) return '';
        if (typeof spec === 'function') return spec(mData[m], m);   // DÒNG MỚI: tính theo (o, m)
        return metricVal(mData[m], spec);                           // dòng cũ: theo idx
      });
      const r = push(pad([row[0]].concat(vals)));
      if (row[2]) mark.ratioRows.push(r);              // dòng % -> định dạng phần trăm
    });
    mark.labelRanges.push([start, sec[1].length]);
  });
  push(pad(['']));

  // 3) KHỐI LỌC — SUMIFS theo khoảng Từ ngày / Đến ngày tự chọn
  mark.hdr.push(push(pad(['🔎 LỌC THEO KHOẢNG (tự chọn)'])));
  const rFrom = push(pad(['Từ ngày', '']));
  const rTo   = push(pad(['Đến ngày', '']));
  push(pad(['']));
  emitMetrics('">="&$B$' + rFrom, '"<="&$B$' + rTo, 'Giá trị (theo khoảng)');
  push(pad(['']));

  // 4) CÁC BLOCK THÁNG (giá trị tĩnh đã chốt)
  months.forEach(function(m){
    const monAds = adsRows.filter(function(r){ const d = toDate(r[0]); return d.getFullYear() === year && d.getMonth() + 1 === m; });
    const sumByDay = {}, accs = {};
    for (let d = 1; d <= nDays; d++) sumByDay[d] = { ct: 0, core: 0, apex: 0, gpi: 0, sdt: 0 };
    monAds.forEach(function(r){
      // Báo cáo cá nhân: GIỮ SỐ GỐC khớp Facebook (đối soát kế toán). Phí 11% chỉ ở Dashboard + KHỐI LỌC.
      const nguon = r[4];
      const day = toDate(r[0]).getDate(), ct2 = Number(r[5] || 0), sdt2 = Number(r[6] || 0), id = String(r[1]);
      sumByDay[day].ct += ct2; sumByDay[day].sdt += sdt2;
      if (nguon === 'COREMIND') sumByDay[day].core += ct2;
      if (nguon === 'APEX') sumByDay[day].apex += ct2;
      if (nguon === 'GPI') sumByDay[day].gpi += ct2;
      // GOM THEO ID + TÊN: cùng ID mà đổi tên (đổi pháp nhân) -> TÁCH thành dòng riêng theo tên mới.
      const akey = id + '|' + chuanHoaTxt(r[2]);
      if (!accs[akey]) accs[akey] = { ten: r[2], nguon: _nguonTK(r[2]), id: id, d: {} };
      accs[akey].d[day] = (accs[akey].d[day] || 0) + ct2;
    });
    const dsByDay = {}; for (let d = 1; d <= nDays; d++) {
      const key = ('0' + d).slice(-2) + '/' + ('0' + m).slice(-2) + '/' + year;
      dsByDay[d] = dsMap[key] || { nong: 0, goi: 0, toiuu: 0, khach: 0, dt: 0, dtMB: 0 };
    }
    function days(fn){ const a = []; for (let d = 1; d <= nDays; d++) a.push(fn(d)); return a; }
    function sumDays(fn){ let s = 0; for (let d = 1; d <= nDays; d++) s += fn(d); return s; }

    mark.monthHdr.push(push(pad(['THÁNG ' + m + ' - ' + year])));
    const gStart = out.length + 1;                        // dòng đầu phần chi tiết -> gấp nhóm, chừa lại tiêu đề tháng
    mark.dateHdr.push(push(['Chỉ số', 'TỔNG THÁNG'].concat(dayHdr)));
    push(['Tổng chi tiêu', sumDays(function(d){ return sumByDay[d].ct; })].concat(days(function(d){ return sumByDay[d].ct; })));
    push(['NS COREMIND', sumDays(function(d){ return sumByDay[d].core; })].concat(days(function(d){ return sumByDay[d].core; })));
    push(['NS APEX', sumDays(function(d){ return sumByDay[d].apex; })].concat(days(function(d){ return sumByDay[d].apex; })));
    push(['NS GPI', sumDays(function(d){ return sumByDay[d].gpi; })].concat(days(function(d){ return sumByDay[d].gpi; })));
    push(['Số điện thoại', sumDays(function(d){ return sumByDay[d].sdt; })].concat(days(function(d){ return sumByDay[d].sdt; })));
    const mCt = sumDays(function(d){ return sumByDay[d].ct; }), mSdt = sumDays(function(d){ return sumByDay[d].sdt; });
    push(['Chi phí / SĐT', mSdt > 0 ? Math.round(mCt / mSdt) : 0].concat(days(function(d){ return sumByDay[d].sdt > 0 ? Math.round(sumByDay[d].ct / sumByDay[d].sdt) : 0; })));
    push(['Lịch nóng', sumDays(function(d){ return dsByDay[d].nong; })].concat(days(function(d){ return dsByDay[d].nong; })));
    push(['Lịch gối', sumDays(function(d){ return dsByDay[d].goi; })].concat(days(function(d){ return dsByDay[d].goi; })));
    push(['Lịch tối ưu', sumDays(function(d){ return dsByDay[d].toiuu; })].concat(days(function(d){ return dsByDay[d].toiuu; })));
    const mKhach = sumDays(function(d){ return dsByDay[d].khach; }), mDt = sumDays(function(d){ return dsByDay[d].dt; });
    const mMB = sumDays(function(d){ return dsByDay[d].dtMB || 0; });
    push(['Khách đến cửa', mKhach].concat(days(function(d){ return dsByDay[d].khach; })));
    push(['Tổng doanh thu', mDt].concat(days(function(d){ return dsByDay[d].dt; })));
    push(['   ├ Cơ sở HCM', mDt - mMB].concat(days(function(d){ return dsByDay[d].dt - (dsByDay[d].dtMB || 0); })));
    push(['   └ HCM chuyển HN', mMB].concat(days(function(d){ return dsByDay[d].dtMB || 0; })));
    push(['Bill TB', mKhach > 0 ? Math.round(mDt / mKhach) : 0].concat(days(function(d){ const o = dsByDay[d]; return o.khach > 0 ? Math.round(o.dt / o.khach) : 0; })));
    const rRatio = push(['Chi phí Ads / DT', mDt > 0 ? mCt / mDt : 0].concat(days(function(d){ return dsByDay[d].dt > 0 ? sumByDay[d].ct / dsByDay[d].dt : 0; })));
    mark.ratioRows.push(rRatio);
    push(pad(['']));
    // Khối 2 - chi tiêu từng tài khoản (ID TKQC tách CỘT RIÊNG cạnh cột Tên TK)
    mark.khoiHdr.push(push(pad(['— Chi tiêu theo từng tài khoản · Tháng ' + m + '/' + year + ' —'])));
    // Bố cục: Tên TK (A) | ID TKQC (B) | TỔNG THÁNG (C) | ngày 1..31 (D...)
    mark.dateHdr.push(push(['Tài khoản', 'ID TKQC', 'TỔNG THÁNG'].concat(dayHdr)));
    ['COREMIND', 'APEX', 'GPI', 'Khác'].forEach(function(ng){
      const grp = Object.keys(accs).map(function(id){ return accs[id]; }).filter(function(a){ return a.nguon === ng; });
      if (!grp.length) return;
      grp.sort(function(a, b){ return a.ten.localeCompare(b.ten); });
      grp.forEach(function(a){
        const rAcc = push(['[' + ng + '] ' + a.ten, '', sumDays(function(d){ return a.d[d] || 0; })].concat(days(function(d){ return a.d[d] || 0; })));
        mark.idCells.push({ r: rAcc, id: a.id });         // ô ID (cột B) để TRỐNG khi setValues -> KHÔNG bị ép về số; ghi lại dạng TEXT sau khi ép '@' + flush
      });
      mark.tong.push(push(['▶ Tổng ' + ng, '', sumDays(function(d){ return grp.reduce(function(s, a){ return s + (a.d[d] || 0); }, 0); })].concat(days(function(d){ return grp.reduce(function(s, a){ return s + (a.d[d] || 0); }, 0); }))));
    });
    mark.tong.push(push(['TỔNG NGÀY', '', mCt].concat(days(function(d){ return sumByDay[d].ct; }))));

    // ===== KHỐI ③ LẬP HOÁ ĐƠN THÁNG (Phần B) — đọc kho LICH_SU_LHD, độc lập, bọc try/catch =====
    // Bố cục: Tên TK (A) | ID TKQC (B) | Số Thẻ (C) | Tổng Tháng (D) | ngày 1..31 (E...)
    // Bóc tách theo nguồn Coremind / Apex / Khác, mỗi nguồn có ▶ Tổng, cuối có ▶ TỔNG LHĐ THÁNG.
    try {
      const L3 = lhdByMonth[m];
      if (L3 && Object.keys(L3).length) {
        const accL = Object.keys(L3).map(function(id){ return L3[id]; });
        push(pad(['']));
        mark.khoiHdr.push(push(pad(['③ LẬP HOÁ ĐƠN CHI TIẾT · Tháng ' + m + '/' + year])));
        mark.dateHdr.push(push(['Tên TK', 'ID TKQC', 'Số Thẻ', 'Tổng Tháng'].concat(dayHdr)));
        const gDay = {}; for (let d = 1; d <= nDays; d++) gDay[d] = 0;
        let gGrand = 0;
        ['COREMIND', 'APEX', 'GPI', 'Khác'].forEach(function(ng){
          const grp = accL.filter(function(a){ return a.nguon === ng; });
          if (!grp.length) return;
          const vf = heSoVATLHD(ng, year, m);   // LHĐ VAT Facebook cho Coremind/Apex: T7/2026 +10%, từ T8/2026 +8% (KHÔNG phí thuê)
          grp.sort(function(a, b){ return String(a.ten).localeCompare(String(b.ten)); });
          const nDay = {}; for (let d = 1; d <= nDays; d++) nDay[d] = 0;
          let nGrand = 0;
          grp.forEach(function(a){
            const rr = push(['[' + ng + '] ' + a.ten, '', '', a.tong * vf].concat(days(function(d){ return (a.d[d] || 0) * vf; })));
            mark.lhdCells.push({ r: rr, id: a.id, card: a.card });   // ID (B) + Số Thẻ (C) để TRỐNG khi setValues -> ghi lại dạng TEXT sau
            nGrand += a.tong * vf;
            for (let d = 1; d <= nDays; d++) { const v = (a.d[d] || 0) * vf; nDay[d] += v; gDay[d] += v; }
          });
          gGrand += nGrand;
          mark.tong.push(push(['▶ Tổng ' + ng, '', '', nGrand].concat(days(function(d){ return nDay[d]; }))));
        });
        mark.tong.push(push(['▶ TỔNG LHĐ THÁNG', '', '', gGrand].concat(days(function(d){ return gDay[d]; }))));
      }
    } catch (e) {}

    mark.groups.push({ start: gStart, end: out.length });  // gấp toàn bộ chi tiết tháng (chỉ số ngày + tài khoản + LHĐ)
    push(pad(['']));
  });

  // ===== GHI + ĐỊNH DẠNG =====
  // Chuẩn hoá: mọi dòng đủ nCol ô (dòng ngắn tự thêm ô rỗng) -> không gãy setValues khi nới cột
  out.forEach(function(row){ while (row.length < nCol) row.push(''); });
  const nRow = out.length;
  sh.getRange(1, 1, nRow, nCol).setValues(out);
  // Ghi công thức bằng setFormula (Google tự đổi , -> ; theo ngôn ngữ, hết #ERROR!)
  F.forEach(function(x){ sh.getRange(x.r, x.c).setFormula(x.f); });

  sh.getRange(1, 3, nRow, nCol - 2).setNumberFormat('#,##0');   // cột C..cuối (TỔNG THÁNG + các ngày)
  sh.getRange(1, 2, nRow, 1).setNumberFormat('#,##0');
  // ==== ÉP TEXT CHO ID TKQC / SỐ THẺ — CHỐNG SCIENTIFIC (1,93959E+15) & LỆCH TÀI KHOẢN ====
  // Ô ID/thẻ đã để TRỐNG lúc setValues nên không bị coerce ở bước ghi hàng loạt.
  // Pass 1 — ép '@' toàn bộ ô ID/thẻ  →  Pass 2 — flush + ghi chuỗi.
  mark.idCells.forEach(function(o){ sh.getRange(o.r, 2).setNumberFormat('@').setHorizontalAlignment('left'); });
  mark.lhdCells.forEach(function(o){ sh.getRange(o.r, 2).setNumberFormat('@').setHorizontalAlignment('left'); sh.getRange(o.r, 3).setNumberFormat('@').setHorizontalAlignment('left'); });
  SpreadsheetApp.flush();
  mark.idCells.forEach(function(o){ sh.getRange(o.r, 2).setValue(o.id == null ? '' : String(o.id)); });
  mark.lhdCells.forEach(function(o){
    sh.getRange(o.r, 2).setValue(o.id == null ? '' : String(o.id));
    sh.getRange(o.r, 3).setValue(o.card == null ? '' : String(o.card));
  });
  // Pass 3 — TỰ KIỂM + SỬA: đọc GỘP 1 lượt cột B & C (getDisplayValues tự flush trước khi đọc),
  // chỉ ô nào hiển thị CHƯA đúng chuỗi ID/thẻ (còn scientific / thiếu số) mới ép lại. Ô đúng bỏ qua ngay
  // -> hầu như không tốn thời gian, an toàn với giới hạn 6 phút của lần chạy tự động 8h.
  const _dispB = (nRow > 0) ? sh.getRange(1, 2, nRow, 1).getDisplayValues() : [];
  const _dispC = (nRow > 0) ? sh.getRange(1, 3, nRow, 1).getDisplayValues() : [];
  function _epTextChac(r, c, val, disp){
    val = (val == null ? '' : String(val));
    if (disp[r - 1] && String(disp[r - 1][0]) === val) return;   // đã đúng -> bỏ qua, KHÔNG gọi ô lẻ
    const rg = sh.getRange(r, c);
    for (var t = 0; t < 3 && String(rg.getDisplayValue()) !== val; t++){
      rg.setNumberFormat('@'); rg.setValue(val); SpreadsheetApp.flush();
    }
  }
  mark.idCells.forEach(function(o){ _epTextChac(o.r, 2, o.id, _dispB); });
  mark.lhdCells.forEach(function(o){ _epTextChac(o.r, 2, o.id, _dispB); _epTextChac(o.r, 3, o.card, _dispC); });

  mark.title.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#16233f').setFontColor('#fff').setFontWeight('bold').setFontSize(13); });
  mark.hdr.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#3a5a99').setFontColor('#fff').setFontWeight('bold').setFontSize(11); });
  mark.labelRanges.forEach(function(p){ sh.getRange(p[0], 1, p[1], 1).setFontWeight('bold').setBackground('#eef2f8'); });
  sh.getRange(rFrom, 2, 2, 1).setNumberFormat('dd/MM/yyyy').setBackground('#fff3d6');
  // Tiêu đề KHỐI THÁNG: nền navy đậm + chữ vàng thương hiệu + vạch vàng dưới chân -> nổi bật như dải phân cách
  mark.monthHdr.forEach(function(r){
    sh.getRange(r, 1, 1, nCol).setBackground('#16233f').setFontColor('#ffd966').setFontWeight('bold').setFontSize(13)
      .setBorder(null, null, true, null, null, null, '#f4b400', SpreadsheetApp.BorderStyle.SOLID_THICK);
  });
  // Tiêu đề "— Chi tiêu từng tài khoản —": dải VÀNG chữ navy -> tương phản rõ với khối tháng
  mark.khoiHdr.forEach(function(r){
    sh.getRange(r, 1, 1, nCol).setBackground('#ffd966').setFontColor('#16233f').setFontWeight('bold').setFontSize(11)
      .setBorder(null, null, true, null, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID);
  });
  mark.dateHdr.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#eef2f8').setFontWeight('bold'); sh.getRange(r, 3, 1, nCol - 2).setHorizontalAlignment('center'); });
  mark.tong.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setFontWeight('bold').setBackground('#fff7e6'); });
  mark.ratioRows.forEach(function(r){ sh.getRange(r, 2, 1, 1).setNumberFormat('0.0%'); sh.getRange(r, 3, 1, nDays).setNumberFormat('0.0%'); });
  sh.getRange(1, 1, nRow, 1).setFontWeight('bold');
  // Viền: khung ngoài + NGANG mảnh nhạt; DỌC giữa các cột ĐẬM RÕ để phân tách từng cột (áp cho MỌI khối)
  sh.getRange(1, 1, nRow, nCol).setBorder(true, true, true, true, null, true, '#dbe1ea', SpreadsheetApp.BorderStyle.SOLID);
  sh.getRange(1, 1, nRow, nCol).setBorder(null, null, null, null, true, null, '#8ea3c4', SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  // Vạch DỌC đậm ngăn nhóm nhãn/Tổng với vùng ngày
  sh.getRange(1, 2, nRow, 1).setBorder(null, null, null, true, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID_THICK);
  // Giãn dòng + cột cho thoáng, đồng bộ dashboard
  sh.setRowHeights(1, nRow, 27);
  mark.title.forEach(function(r){ sh.setRowHeight(r, 38); });
  mark.hdr.forEach(function(r){ sh.setRowHeight(r, 30); });
  mark.monthHdr.forEach(function(r){ sh.setRowHeight(r, 36); });
  mark.khoiHdr.forEach(function(r){ sh.setRowHeight(r, 30); });
  mark.dateHdr.forEach(function(r){ sh.setRowHeight(r, 28); });
  sh.setColumnWidth(1, 270); sh.setColumnWidth(2, 175);   // cột nhãn (Tên TK) + cột B (TỔNG/ID TKQC 16 chữ số)
  for (let c = 3; c <= nCol; c++) sh.setColumnWidth(c, 100);  // giãn ĐỀU toàn bộ cột ngày cho thoáng đãng
  sh.setFrozenColumns(2);

  // Đặt lại ngày lọc (giữ giá trị cũ nếu có, mặc định = tháng gần nhất)
  const lastM = months.length ? months[months.length - 1] : (new Date().getMonth() + 1);
  const defFrom = new Date(year, lastM - 1, 1);
  const defTo = new Date(year, lastM - 1, nDays);
  sh.getRange(rFrom, 2).setValue(oldFrom instanceof Date ? oldFrom : defFrom);
  sh.getRange(rTo, 2).setValue(oldTo instanceof Date ? oldTo : defTo);

  // GẤP GỌN: nhóm chi tiết từng tháng (chừa tiêu đề "THÁNG X") + thu gọn sẵn. Bấm [+] ở lề trái để bung.
  try {
    mark.groups.forEach(function(g){
      if (g.end >= g.start) sh.getRange(g.start, 1, g.end - g.start + 1, 1).shiftRowGroupDepth(1);
    });
    sh.collapseAllRowGroups();
  } catch (e) {}
}

// ============================================================================
// VẼ LẠI CHỈ KHỐI "THÁNG HIỆN TẠI" (khối cuối cùng của sheet) + cập nhật cột
// tháng đó ở bảng TỔNG THEO THÁNG. Các tháng cũ phía trên GIỮ NGUYÊN.
// Trả TRUE nếu vẽ nhanh OK; FALSE nếu cần dựng FULL (sheet chưa có / không tìm
// thấy tiêu đề tháng / cấu trúc lạ) -> hàm gọi tự buildSheetYear.
// buildSheetYear (dựng full) KHÔNG bị đụng -> luôn là bản gốc an toàn.
// ⚠️ Nếu sau này sửa BỐ CỤC khối tháng trong buildSheetYear, phải sửa ĐỒNG BỘ ở đây.
// ============================================================================
function veLaiThangHienTai(ss, mk, year, adsRows, dsMap) {
  const name = 'BC - ' + mk + ' - ' + year;
  const sh = ss.getSheetByName(name);
  if (!sh) return false;
  const lastRow = sh.getLastRow();
  if (lastRow < 5) return false;

  const nDays = 31, nCol = 4 + nDays;
  const dayHdr = []; for (let d = 1; d <= nDays; d++) dayHdr.push(d);

  // tháng mới = tháng LỚN NHẤT có dữ liệu trong năm (khớp khối cuối cùng của sheet)
  let m = 0;
  adsRows.forEach(function(r){ const d = toDate(r[0]); if (d.getFullYear() === year) m = Math.max(m, d.getMonth() + 1); });
  Object.keys(dsMap).forEach(function(s){ const p = s.split('/'); if (Number(p[2]) === year) m = Math.max(m, Number(p[1])); });
  if (!m) return false;

  // Tìm dòng tiêu đề "THÁNG m - year"
  const colA = sh.getRange(1, 1, lastRow, 1).getValues();
  const tdThang = 'THÁNG ' + m + ' - ' + year;
  let R = -1;
  for (let i = 0; i < colA.length; i++){ if (String(colA[i][0]).trim() === tdThang) { R = i + 1; break; } }
  if (R < 0) return false;   // tháng chưa có trên sheet (tháng mới) -> full build để thêm khối

  // ---- 1) DỰNG LẠI KHỐI THÁNG m (giống hệt buildSheetYear) ----
  const monAds = adsRows.filter(function(r){ const d = toDate(r[0]); return d.getFullYear() === year && d.getMonth() + 1 === m; });
  const sumByDay = {}, accs = {};
  for (let d = 1; d <= nDays; d++) sumByDay[d] = { ct: 0, core: 0, apex: 0, gpi: 0, sdt: 0 };
  monAds.forEach(function(r){
    const nguon = r[4];
    const day = toDate(r[0]).getDate(), ct2 = Number(r[5] || 0), sdt2 = Number(r[6] || 0), id = String(r[1]);
    sumByDay[day].ct += ct2; sumByDay[day].sdt += sdt2;
    if (nguon === 'COREMIND') sumByDay[day].core += ct2;
    if (nguon === 'APEX') sumByDay[day].apex += ct2;
    if (nguon === 'GPI') sumByDay[day].gpi += ct2;
    // GOM THEO ID + TÊN: cùng ID mà đổi tên -> tách dòng riêng theo tên mới.
    const akey = id + '|' + chuanHoaTxt(r[2]);
    if (!accs[akey]) accs[akey] = { ten: r[2], nguon: _nguonTK(r[2]), id: id, d: {} };
    accs[akey].d[day] = (accs[akey].d[day] || 0) + ct2;
  });
  const dsByDay = {}; for (let d = 1; d <= nDays; d++) { const key = ('0' + d).slice(-2) + '/' + ('0' + m).slice(-2) + '/' + year; dsByDay[d] = dsMap[key] || { nong: 0, goi: 0, toiuu: 0, khach: 0, dt: 0, dtMB: 0 }; }
  function days(fn){ const a = []; for (let d = 1; d <= nDays; d++) a.push(fn(d)); return a; }
  function sumDays(fn){ let s = 0; for (let d = 1; d <= nDays; d++) s += fn(d); return s; }

  const lhdByMonth = (typeof LHD_BAT !== 'undefined' && LHD_BAT) ? _docLHD(ss, mk, year, nDays) : {};

  const out = [];
  const M = { monthHdr: [], khoiHdr: [], dateHdr: [], tong: [], ratioRows: [], idCells: [], lhdCells: [] };
  function push(row){ while (row.length < nCol) row.push(''); out.push(row); return out.length; }   // trả LOCAL 1-based

  M.monthHdr.push(push(['THÁNG ' + m + ' - ' + year]));
  const gStart = out.length + 1;
  M.dateHdr.push(push(['Chỉ số', 'TỔNG THÁNG'].concat(dayHdr)));
  push(['Tổng chi tiêu', sumDays(function(d){ return sumByDay[d].ct; })].concat(days(function(d){ return sumByDay[d].ct; })));
  push(['NS COREMIND', sumDays(function(d){ return sumByDay[d].core; })].concat(days(function(d){ return sumByDay[d].core; })));
  push(['NS APEX', sumDays(function(d){ return sumByDay[d].apex; })].concat(days(function(d){ return sumByDay[d].apex; })));
  push(['NS GPI', sumDays(function(d){ return sumByDay[d].gpi; })].concat(days(function(d){ return sumByDay[d].gpi; })));
  push(['Số điện thoại', sumDays(function(d){ return sumByDay[d].sdt; })].concat(days(function(d){ return sumByDay[d].sdt; })));
  const mCt = sumDays(function(d){ return sumByDay[d].ct; }), mSdt = sumDays(function(d){ return sumByDay[d].sdt; });
  push(['Chi phí / SĐT', mSdt > 0 ? Math.round(mCt / mSdt) : 0].concat(days(function(d){ return sumByDay[d].sdt > 0 ? Math.round(sumByDay[d].ct / sumByDay[d].sdt) : 0; })));
  push(['Lịch nóng', sumDays(function(d){ return dsByDay[d].nong; })].concat(days(function(d){ return dsByDay[d].nong; })));
  push(['Lịch gối', sumDays(function(d){ return dsByDay[d].goi; })].concat(days(function(d){ return dsByDay[d].goi; })));
  push(['Lịch tối ưu', sumDays(function(d){ return dsByDay[d].toiuu; })].concat(days(function(d){ return dsByDay[d].toiuu; })));
  const mKhach = sumDays(function(d){ return dsByDay[d].khach; }), mDt = sumDays(function(d){ return dsByDay[d].dt; });
  const mMB = sumDays(function(d){ return dsByDay[d].dtMB || 0; });
  push(['Khách đến cửa', mKhach].concat(days(function(d){ return dsByDay[d].khach; })));
  push(['Tổng doanh thu', mDt].concat(days(function(d){ return dsByDay[d].dt; })));
  push(['   ├ Cơ sở HCM', mDt - mMB].concat(days(function(d){ return dsByDay[d].dt - (dsByDay[d].dtMB || 0); })));
  push(['   └ HCM chuyển HN', mMB].concat(days(function(d){ return dsByDay[d].dtMB || 0; })));
  push(['Bill TB', mKhach > 0 ? Math.round(mDt / mKhach) : 0].concat(days(function(d){ const o = dsByDay[d]; return o.khach > 0 ? Math.round(o.dt / o.khach) : 0; })));
  M.ratioRows.push(push(['Chi phí Ads / DT', mDt > 0 ? mCt / mDt : 0].concat(days(function(d){ return dsByDay[d].dt > 0 ? sumByDay[d].ct / dsByDay[d].dt : 0; }))));
  push(['']);
  M.khoiHdr.push(push(['— Chi tiêu theo từng tài khoản · Tháng ' + m + '/' + year + ' —']));
  M.dateHdr.push(push(['Tài khoản', 'ID TKQC', 'TỔNG THÁNG'].concat(dayHdr)));
  ['COREMIND', 'APEX', 'GPI', 'Khác'].forEach(function(ng){
    const grp = Object.keys(accs).map(function(id){ return accs[id]; }).filter(function(a){ return a.nguon === ng; });
    if (!grp.length) return;
    grp.sort(function(a, b){ return a.ten.localeCompare(b.ten); });
    grp.forEach(function(a){
      const rAcc = push(['[' + ng + '] ' + a.ten, '', sumDays(function(d){ return a.d[d] || 0; })].concat(days(function(d){ return a.d[d] || 0; })));
      M.idCells.push({ r: rAcc, id: a.id });
    });
    M.tong.push(push(['▶ Tổng ' + ng, '', sumDays(function(d){ return grp.reduce(function(s, a){ return s + (a.d[d] || 0); }, 0); })].concat(days(function(d){ return grp.reduce(function(s, a){ return s + (a.d[d] || 0); }, 0); }))));
  });
  M.tong.push(push(['TỔNG NGÀY', '', mCt].concat(days(function(d){ return sumByDay[d].ct; }))));
  try {
    const L3 = lhdByMonth[m];
    if (L3 && Object.keys(L3).length) {
      const accL = Object.keys(L3).map(function(id){ return L3[id]; });
      push(['']);
      M.khoiHdr.push(push(['③ LẬP HOÁ ĐƠN CHI TIẾT · Tháng ' + m + '/' + year]));
      M.dateHdr.push(push(['Tên TK', 'ID TKQC', 'Số Thẻ', 'Tổng Tháng'].concat(dayHdr)));
      const gDay = {}; for (let d = 1; d <= nDays; d++) gDay[d] = 0;
      let gGrand = 0;
      ['COREMIND', 'APEX', 'GPI', 'Khác'].forEach(function(ng){
        const grp = accL.filter(function(a){ return a.nguon === ng; });
        if (!grp.length) return;
        const vf = heSoVATLHD(ng, year, m);
        grp.sort(function(a, b){ return String(a.ten).localeCompare(String(b.ten)); });
        const nDay = {}; for (let d = 1; d <= nDays; d++) nDay[d] = 0;
        let nGrand = 0;
        grp.forEach(function(a){
          const rr = push(['[' + ng + '] ' + a.ten, '', '', a.tong * vf].concat(days(function(d){ return (a.d[d] || 0) * vf; })));
          M.lhdCells.push({ r: rr, id: a.id, card: a.card });
          nGrand += a.tong * vf;
          for (let d = 1; d <= nDays; d++) { const v = (a.d[d] || 0) * vf; nDay[d] += v; gDay[d] += v; }
        });
        gGrand += nGrand;
        M.tong.push(push(['▶ Tổng ' + ng, '', '', nGrand].concat(days(function(d){ return nDay[d]; }))));
      });
      M.tong.push(push(['▶ TỔNG LHĐ THÁNG', '', '', gGrand].concat(days(function(d){ return gDay[d]; }))));
    }
  } catch (e) {}
  const gEnd = out.length;
  push(['']);
  const nNew = out.length;

  // ---- 2) THAY vùng cũ (R..lastRow) bằng khối mới ----
  try { sh.getRange(R, 1, lastRow - R + 1, 1).shiftRowGroupDepth(-8); } catch (e) {}   // gỡ nhóm gấp khối cũ
  sh.deleteRows(R, lastRow - R + 1);
  const need = R + nNew - 1;
  if (sh.getMaxRows() < need) sh.insertRowsAfter(sh.getMaxRows(), need - sh.getMaxRows());
  sh.getRange(R, 1, nNew, nCol).setValues(out);

  const off = R - 1;   // local row -> absolute = off + local
  sh.getRange(R, 3, nNew, nCol - 2).setNumberFormat('#,##0');
  sh.getRange(R, 2, nNew, 1).setNumberFormat('#,##0');
  // ID/Số Thẻ -> ép TEXT rồi ghi chuỗi (chống scientific)
  M.idCells.forEach(function(o){ sh.getRange(off + o.r, 2).setNumberFormat('@').setHorizontalAlignment('left'); });
  M.lhdCells.forEach(function(o){ sh.getRange(off + o.r, 2).setNumberFormat('@').setHorizontalAlignment('left'); sh.getRange(off + o.r, 3).setNumberFormat('@').setHorizontalAlignment('left'); });
  SpreadsheetApp.flush();
  M.idCells.forEach(function(o){ sh.getRange(off + o.r, 2).setValue(o.id == null ? '' : String(o.id)); });
  M.lhdCells.forEach(function(o){ sh.getRange(off + o.r, 2).setValue(o.id == null ? '' : String(o.id)); sh.getRange(off + o.r, 3).setValue(o.card == null ? '' : String(o.card)); });
  // Nền + tiêu đề (giống buildSheetYear)
  M.monthHdr.forEach(function(o){ sh.getRange(off + o, 1, 1, nCol).setBackground('#16233f').setFontColor('#ffd966').setFontWeight('bold').setFontSize(13).setBorder(null, null, true, null, null, null, '#f4b400', SpreadsheetApp.BorderStyle.SOLID_THICK); });
  M.khoiHdr.forEach(function(o){ sh.getRange(off + o, 1, 1, nCol).setBackground('#ffd966').setFontColor('#16233f').setFontWeight('bold').setFontSize(11).setBorder(null, null, true, null, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID); });
  M.dateHdr.forEach(function(o){ sh.getRange(off + o, 1, 1, nCol).setBackground('#eef2f8').setFontWeight('bold'); sh.getRange(off + o, 3, 1, nCol - 2).setHorizontalAlignment('center'); });
  M.tong.forEach(function(o){ sh.getRange(off + o, 1, 1, nCol).setFontWeight('bold').setBackground('#fff7e6'); });
  M.ratioRows.forEach(function(o){ sh.getRange(off + o, 2, 1, 1).setNumberFormat('0.0%'); sh.getRange(off + o, 3, 1, nDays).setNumberFormat('0.0%'); });
  sh.getRange(R, 1, nNew, 1).setFontWeight('bold');
  // Viền giống full
  sh.getRange(R, 1, nNew, nCol).setBorder(true, true, true, true, null, true, '#dbe1ea', SpreadsheetApp.BorderStyle.SOLID);
  sh.getRange(R, 1, nNew, nCol).setBorder(null, null, null, null, true, null, '#8ea3c4', SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  sh.getRange(R, 2, nNew, 1).setBorder(null, null, null, true, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID_THICK);
  sh.setRowHeights(R, nNew, 27);
  M.monthHdr.forEach(function(o){ sh.setRowHeight(off + o, 36); });
  M.khoiHdr.forEach(function(o){ sh.setRowHeight(off + o, 30); });
  M.dateHdr.forEach(function(o){ sh.setRowHeight(off + o, 28); });
  try { sh.getRange(off + gStart, 1, gEnd - gStart + 1, 1).shiftRowGroupDepth(1); sh.collapseAllRowGroups(); } catch (e) {}

  // ---- 3) CẬP NHẬT CỘT THÁNG m Ở BẢNG TỔNG THEO THÁNG (giá trị tĩnh) ----
  try { _capNhatCotTongThang(sh, year, m, mk, adsRows, dsMap, nDays, lhdByMonth); } catch (e) {}
  return true;
}

// Cập nhật đúng 1 cột "Tháng m" trong bảng TỔNG THEO THÁNG. DÒ THEO NHÃN (khối có mục ①②③④⑤ +
// dòng mới xen giữa nên KHÔNG đếm theo vị trí cố định). Dòng nào có công thức thì điền; dòng mới
// (Số Tiền LHĐ / Doanh Thu Xét KPI / ... ) chưa có công thức -> BỎ QUA, giữ nguyên ô.
function _capNhatCotTongThang(sh, year, m, mk, adsRows, dsMap, nDays, lhdByMonth) {
  const scan = Math.min(sh.getLastRow(), 80);
  const grid = sh.getRange(1, 1, scan, 40).getValues();
  let hdrRow = -1, col = -1;
  for (let i = 0; i < grid.length; i++) {
    if (String(grid[i][0]).trim() === 'Chỉ số') {
      for (let c = 1; c < grid[i].length; c++) {
        if (String(grid[i][c]).trim() === 'Tháng ' + m) { hdrRow = i + 1; col = c + 1; break; }
      }
      if (hdrRow > 0) break;
    }
  }
  if (hdrRow < 0 || col < 0) return;
  const o = { ct: 0, core: 0, apex: 0, gpi: 0, sdt: 0, nong: 0, goi: 0, toiuu: 0, khach: 0, dt: 0, dtMB: 0, bong: 0 };
  adsRows.forEach(function(r){
    const d = toDate(r[0]); if (d.getFullYear() !== year || d.getMonth() + 1 !== m) return;
    if (_loaiKhoiTong(mk, r[2])) return;   // GPI-không-SG của MKT Thắng -> KHÔNG cộng vào KHỐI TỔNG
    const vf = heSoVATCt(r[4], year, m);
    const s = Number(r[5] || 0) * vf, p = Number(r[6] || 0);
    o.ct += s; o.sdt += p;
    if (r[4] === 'COREMIND') o.core += s; if (r[4] === 'APEX') o.apex += s; if (r[4] === 'GPI') o.gpi += s;
  });
  for (let dd = 1; dd <= nDays; dd++) {
    const key = ('0' + dd).slice(-2) + '/' + ('0' + m).slice(-2) + '/' + year;
    const x = dsMap[key]; if (x) { o.nong += x.nong; o.goi += x.goi; o.toiuu += x.toiuu; o.khach += x.khach; o.dt += x.dt; o.dtMB += (x.dtMB || 0); o.bong += (x.bong || 0); }
  }
  // DÒNG MỚI (từ T8/2026): Số tiền LHĐ = TổngLHĐ×1.02; DT Xét KPI = dt×1.08; DT Hoa Hồng = dt; Ads/DT Thực.
  const gop = ((year > 2026) || (year === 2026 && m >= 7)) ? 1.08 : 1;   // T7+ ×1.08 ra số thực đối soát; T4/5/6 ×1
  let lhd = 0;   // = Số Tiền LHĐ: theo từng TK, LHĐ (đã VAT 10%) × phí cụm (1.011 / 1.021)
  if (lhdByMonth && lhdByMonth[m]) { const L = lhdByMonth[m]; Object.keys(L).forEach(function(id){ const a = L[id]; if (_loaiKhoiTong(mk, a.ten)) return; lhd += (a.tong || 0) * heSoVATLHD(a.nguon, year, m) * _phiTheLHD(a.ten); }); }
  const dxk = o.dt * gop;
  // [nhãn, giá trị, %] THEO ĐÚNG THỨ TỰ khối B1_SEC (bỏ tiêu đề mục). Khớp theo LẦN XUẤT HIỆN của nhãn.
  const seq = [
    ['Tổng chi tiêu', o.ct, false], ['NS COREMIND', o.core, false], ['NS APEX', o.apex, false], ['NS GPI', o.gpi, false],
    ['Số Tiền Lập Hoá Đơn', Math.round(lhd), false],
    ['Số điện thoại', o.sdt, false], ['Chi phí / SĐT', o.sdt > 0 ? Math.round(o.ct / o.sdt) : 0, false],
    ['Lịch nóng', o.nong, false], ['Lịch gối', o.goi, false], ['Lịch tối ưu', o.toiuu, false], ['Khách đến cửa', o.khach, false],
    ['Tỷ lệ bong', o.khach > 0 ? o.bong / o.khach : 0, true],
    ['Doanh Thu Xét KPI', Math.round(dxk), false],
    ['├ Doanh thu HCM', Math.round((o.dt - (o.dtMB || 0)) * gop), false],
    ['└ HCM chuyển HN', Math.round((o.dtMB || 0) * gop), false],
    ['Doanh Thu Hoa Hồng', Math.round(o.dt), false],
    ['Bill TB', o.khach > 0 ? Math.round(o.dt / o.khach) : 0, false],
    ['Ads / Doanh Thu Hoa Hồng', ((year > 2026) || (year === 2026 && m >= 7)) ? (o.dt > 0 ? lhd / o.dt : 0) : null, true],
    ['Ads / Doanh Thu Thực', dxk > 0 ? lhd / dxk : 0, true]
  ];
  const byLabel = {};
  seq.forEach(function(e){ (byLabel[e[0]] = byLabel[e[0]] || []).push(e); });
  const dem = {};
  for (let r = hdrRow + 1; r <= scan; r++) {
    const lb = String((grid[r - 1] && grid[r - 1][0]) || '').trim();
    if (lb === '') break;                          // hết khối TỔNG THEO THÁNG
    const arr = byLabel[lb]; if (!arr) continue;    // dòng tiêu đề mục -> bỏ qua
    const k = dem[lb] || 0; dem[lb] = k + 1;        // khớp theo lần xuất hiện
    const e = arr[k]; if (!e || e[1] == null) continue;
    sh.getRange(r, col).setValue(e[1]);
    if (e[2]) sh.getRange(r, col).setNumberFormat('0.0%');
  }
}

// ---- Helpers ----
// Dò dấu phân cách hàm bằng cách THỬ TRỰC TIẾP: cái nào cho ra 2 thì dùng dấu đó
function locaSep(sh) {
  function works(cell, f) {
    try {
      cell.setFormula(f);
      SpreadsheetApp.flush();
      const v = cell.getValue();
      cell.clearContent();
      return v === 2;
    } catch (e) { try { cell.clearContent(); } catch (e2) {} return false; }
  }
  const c = sh.getRange(1, 100);
  if (works(c, '=SUM(1,1)')) return ',';
  if (works(c, '=SUM(1;1)')) return ';';
  return ',';
}
function docDSMKT(ss) {
  const ds = ss.getSheetByName('DANH_SACH_MKT'); const list = [];
  if (ds) { const d = ds.getDataRange().getValues();
    for (let i = 1; i < d.length; i++) {
      const ten = String(d[i][0] || '').trim(); if (!ten) continue;
      const keys = String(d[i][1] || '').split(',').map(function(k){ return chuanHoaTxt(k); }).filter(Boolean);
      keys.push(chuanHoaTxt(ten)); list.push({ ten: ten, keys: keys });
    } }
  return list;
}
function ganMKT(ch, list) {
  let m = []; list.forEach(function(x){ if (x.keys.some(function(k){ return ch.indexOf(k) >= 0; })) m.push(x.ten); });
  return m.length === 1 ? m[0] : (m.length === 0 ? 'Chưa gán' : '⚠️ Trùng');
}
// Hệ số CHI TIÊU: NAY LUÔN = 1 -> chi tiêu để ĐÚNG SỐ GỐC khớp Facebook (dùng chung Dashboard + TỔNG THEO THÁNG).
// Đã BỎ cộng VAT/phí vào chi tiêu — chi phí thật (VAT + phí ngân hàng/thuê TK) được tính qua khối LẬP HOÁ ĐƠN.
function heSoVATCt(nguon, y, m) {
  return 1.0;
}
// Hệ số LẬP HOÁ ĐƠN (khối ③): COREMIND & APEX cộng +10% VAT của Facebook từ T7/2026.
// (ĐÃ BỎ mức 8% — thực tế nhiều tài khoản vẫn bị thu 10%.) Nguồn "Khác" và trước T7 -> hệ số 1.
// NHẬN DIỆN NGUỒN TÀI KHOẢN theo TÊN TK (một quy tắc dùng chung toàn hệ thống).
//   COREMIND / APEX / GPI (tên chứa GPI, GPITECH, GPI TECH đều rơi vào 'GPI') / còn lại 'Khác'.
function _nguonTK(ten) {
  const ch = chuanHoaTxt(String(ten || ''));
  if (ch.indexOf('COREMIND') >= 0) return 'COREMIND';
  if (ch.indexOf('GPI') >= 0) return 'GPI';
  if (ch.indexOf('APEX') >= 0) return 'APEX';
  return 'Khác';
}

// PHÁP NHÂN HỢP LỆ = có nguồn (COREMIND / APEX / GPI). Nguồn 'Khác' = không gán được pháp nhân.
function _laPhapNhanHopLe(ten) { return _nguonTK(ten) !== 'Khác'; }

// TK nguồn GPI mà TÊN KHÔNG chứa "SG".
function _gpiKhongSG(ten) { const ch = chuanHoaTxt(String(ten || '')); return ch.indexOf('GPI') >= 0 && ch.indexOf('SG') < 0; }
// Marketing áp luật "GPI phải có SG mới tính vào tổng" = Phạm Ngọc Thắng.
// Nhận diện: tên marketing CHỨA "Thắng" ở bất kỳ dạng nào (P. THẮNG / NGỌC THẮNG / PHẠM THẮNG / THẮNG),
// không phân biệt hoa thường & dấu (chuanHoaTxt bỏ dấu -> khớp "THANG").
function _laMktLocGPI(mk) { return chuanHoaTxt(String(mk || '')).indexOf('THANG') >= 0; }
// LOẠI KHỎI KHỐI TỔNG khi: TK là GPI-không-SG VÀ thuộc marketing áp luật (Phạm Ngọc Thắng).
// (GPI-không-SG của marketing khác vẫn tính bình thường; khối chi tiết luôn giữ đủ.)
function _loaiKhoiTong(mk, ten) { return _laMktLocGPI(mk) && _gpiKhongSG(ten); }

// NHẬN DIỆN TÀI KHOẢN MỤC ĐÍCH ĐẶC THÙ theo TÊN TK -> 'TUYỂN DỤNG' / 'PR TRUYỀN THÔNG' / '' (không đặc thù).
// Khớp theo TOKEN đứng riêng cho từ ngắn (PR/HR) để không ăn nhầm SPRING/THREAD; cụm dài dùng chứa-chuỗi.
function _mucDichDacThu(ten) {
  const ch = chuanHoaTxt(String(ten || ''));
  if (/(^|[^A-Z0-9])HR([^A-Z0-9]|$)/.test(ch) || ch.indexOf('TUYEN DUNG') >= 0) return 'TUYỂN DỤNG';
  if (/(^|[^A-Z0-9])PR([^A-Z0-9]|$)/.test(ch) || ch.indexOf('TRUYEN THONG') >= 0) return 'PR TRUYỀN THÔNG';
  return '';
}
// Danh sách mục đích đặc thù (thứ tự hiển thị trong tab CHI PHÍ ĐẶC THÙ)
var _DS_MUC_DICH_DACTHU = ['TUYỂN DỤNG', 'PR TRUYỀN THÔNG'];

function heSoVATLHD(nguon, y, m) {
  const tuT7 = (y > 2026) || (y === 2026 && m >= 7);
  const coVAT = (nguon === 'COREMIND' || nguon === 'APEX' || nguon === 'GPI');   // GPI +10% VAT như Coremind/Apex
  return (tuT7 && coVAT) ? 1.10 : 1.0;
}

// CHẠY TAY 1 LẦN — chuẩn hoá lại cột "Nguồn" của LICH_SU theo tên TK (gán GPI cho TK GPI đã lỡ ghi 'Khác').
// Độc lập, không đụng số liệu chi tiêu; chỉ sửa nhãn nguồn -> giúp dòng NS GPI + KHỐI LỌC hiện đúng cho dữ liệu cũ.
function chuanHoaNguonLICHSU() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ls = ss.getSheetByName('LICH_SU');
  if (!ls || ls.getLastRow() < 2) { ss.toast('LICH_SU trống.', 'Bỏ qua', 5); return; }
  const n = ls.getLastRow() - 1;
  const ten = ls.getRange(2, 3, n, 1).getValues();   // cột C: Tên TK
  const cur = ls.getRange(2, 5, n, 1).getValues();    // cột E: Nguồn
  let doi = 0;
  const out = cur.map(function(row, i){ const ng = _nguonTK(ten[i][0]); if (ng !== row[0]) doi++; return [ng]; });
  ls.getRange(2, 5, n, 1).setValues(out);
  ss.toast('Đã chuẩn hoá cột Nguồn LICH_SU: cập nhật ' + doi + ' dòng.', 'Xong', 8);
}
// Phí cộng vào SỐ TIỀN LẬP HOÁ ĐƠN (khối TỔNG THEO THÁNG), theo TÊN tài khoản:
//   • TK chứa "XH-SG-VAT" hoặc "Q-SG": +1,1% phí ngân hàng + 1% phí thuê TK -> ×1.021
//   • TK khác:                         +1,1% phí ngân hàng                   -> ×1.011
function _phiTheLHD(ten) {
  const ch = chuanHoaTxt(String(ten || ''));
  // Cụm chịu +1% phí thuê TK: XH-SG-VAT hoặc XH-VAT-SG (ghi cả 2 cách viết) hoặc Q-SG -> ×1.021; còn lại ×1.011.
  return (ch.indexOf('XH-SG-VAT') >= 0 || ch.indexOf('XH-VAT-SG') >= 0 || ch.indexOf('Q-SG') >= 0) ? 1.021 : 1.011;
}
function dmy(v) {
  if (v instanceof Date) return Utilities.formatDate(v, 'Asia/Ho_Chi_Minh', 'dd/MM/yyyy');
  const p = String(v).trim().split('/');
  if (p.length === 3) return ('0' + p[0].trim()).slice(-2) + '/' + ('0' + p[1].trim()).slice(-2) + '/' + p[2].trim();
  return String(v).trim();
}
function toDate(v) {
  let d;
  if (v instanceof Date) d = v;
  else {
    const p = String(v).trim().split('/');
    d = (p.length === 3) ? new Date(Number(p[2]), Number(p[1]) - 1, Number(p[0])) : new Date(v);
  }
  if (!(d instanceof Date) || isNaN(d.getTime())) return d;
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()); // cắt giờ, về 00:00
}
function parseNum(v) { if (typeof v === 'number') return v; const s = String(v).replace(/[^\d]/g, ''); return s ? Number(s) : 0; }

// ============================================================================
// TAB "CHI PHÍ ĐẶC THÙ" — tách theo mục đích (Tuyển dụng / PR truyền thông),
// mỗi mục đích có KHỐI CHI TIÊU (theo ngày) + KHỐI LẬP HOÁ ĐƠN (theo tháng).
// Nguồn: LICH_SU_DACTHU (mới) + TK đặc thù còn sót trong LICH_SU (dedup) cho chi tiêu;
//        LICH_SU_LHD lọc TK đặc thù cho lập hoá đơn (đã VAT + phí thẻ như báo cáo chính).
// KHÔNG tính vào tổng cá nhân / Dashboard / khung giờ. Tự dựng lại mỗi lần dựng báo cáo cá nhân.
// ============================================================================
function dungChiPhiDacThu() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const nDays = 31;

  // 1) CHI TIÊU đặc thù -> ct[mụcĐích][y-m][id|tên] = {ten,id,d:{ngày:tiền}}
  const ct = {}, seen = {};
  function napCT(dObj, id, ten, spend, md) {
    if (!md) return;
    const y = dObj.getFullYear(), m = dObj.getMonth() + 1, ym = y + '-' + m;
    if (!ct[md]) ct[md] = {}; if (!ct[md][ym]) ct[md][ym] = {};
    const key = String(id) + '|' + chuanHoaTxt(ten);
    if (!ct[md][ym][key]) ct[md][ym][key] = { ten: ten, id: String(id), d: {} };
    ct[md][ym][key].d[dObj.getDate()] = (ct[md][ym][key].d[dObj.getDate()] || 0) + Number(spend || 0);
  }
  const dtS = ss.getSheetByName('LICH_SU_DACTHU');
  if (dtS && dtS.getLastRow() > 1) {
    dtS.getRange(2, 1, dtS.getLastRow() - 1, 8).getValues().forEach(function(r){
      const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      const md = String(r[7] || '') || _mucDichDacThu(r[2]); if (!md) return;
      seen[dmy(r[0]) + '|' + String(r[1]) + '|' + chuanHoaTxt(r[2])] = true;
      napCT(d, r[1], r[2], r[5], md);
    });
  }
  const ls = ss.getSheetByName('LICH_SU');
  if (ls && ls.getLastRow() > 1) {
    ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues().forEach(function(r){
      const md = _mucDichDacThu(r[2]); if (!md) return;
      const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      if (seen[dmy(r[0]) + '|' + String(r[1]) + '|' + chuanHoaTxt(r[2])]) return;   // đã có ở kho đặc thù -> khỏi cộng đôi
      napCT(d, r[1], r[2], r[5], md);
    });
  }

  // 2) LẬP HOÁ ĐƠN đặc thù -> lhd[mụcĐích][y-m][id|tên] = {ten,id,card,tong(đã VAT+phí)}
  const lhd = {};
  const lhS = ss.getSheetByName('LICH_SU_LHD');
  if (lhS && lhS.getLastRow() > 1) {
    lhS.getRange(2, 1, lhS.getLastRow() - 1, 6).getValues().forEach(function(r){
      const md = _mucDichDacThu(r[2]); if (!md) return;
      const d = toDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      const y = d.getFullYear(), m = d.getMonth() + 1, ym = y + '-' + m;
      const tien = Number(r[5] || 0) * heSoVATLHD(_nguonTK(r[2]), y, m) * _phiTheLHD(r[2]);
      if (!lhd[md]) lhd[md] = {}; if (!lhd[md][ym]) lhd[md][ym] = {};
      const key = String(r[1]) + '|' + chuanHoaTxt(r[2]);
      if (!lhd[md][ym][key]) lhd[md][ym][key] = { ten: r[2], id: String(r[1]), card: String(r[4] || ''), tong: 0 };
      lhd[md][ym][key].tong += tien;
    });
  }

  // 3) DỰNG SHEET
  const nCol = 3 + nDays;   // Tên | ID | TỔNG | ngày 1..31
  const dayHdr = []; for (let d = 1; d <= nDays; d++) dayHdr.push(d);
  const out = [];
  const mark = { title: [], sec: [], blk: [], colHead: [], tong: [], idCells: [], cardCells: [] };
  function push(row){ while (row.length < nCol) row.push(''); out.push(row); return out.length; }
  function rangeDays(o){ const a = []; for (let d = 1; d <= nDays; d++) a.push(o[d] || 0); return a; }
  function byTen(A, B, o){ return String(o[A].ten).localeCompare(String(o[B].ten)); }

  mark.title.push(push(['📌 CHI PHÍ ĐẶC THÙ — Tuyển dụng / PR truyền thông  (tách riêng, KHÔNG tính vào tổng cá nhân · Dashboard · khung giờ)']));
  push(['']);

  let coData = false;
  _DS_MUC_DICH_DACTHU.forEach(function(md){
    const hasCT = ct[md] && Object.keys(ct[md]).length;
    const hasLHD = lhd[md] && Object.keys(lhd[md]).length;
    if (!hasCT && !hasLHD) return;
    coData = true;
    mark.sec.push(push(['▛ ' + md + ' ▟']));
    const yms = {};
    if (ct[md]) Object.keys(ct[md]).forEach(function(k){ yms[k] = 1; });
    if (lhd[md]) Object.keys(lhd[md]).forEach(function(k){ yms[k] = 1; });
    const ymList = Object.keys(yms).sort(function(a, b){ const pa = a.split('-').map(Number), pb = b.split('-').map(Number); return (pa[0] - pb[0]) || (pa[1] - pb[1]); });
    ymList.forEach(function(ym){
      const p = ym.split('-'); const y = Number(p[0]), m = Number(p[1]);
      // KHỐI CHI TIÊU (theo ngày)
      const cobj = (ct[md] && ct[md][ym]) ? ct[md][ym] : {};
      if (Object.keys(cobj).length) {
        mark.blk.push(push([md + ' · CHI TIÊU · Tháng ' + m + '/' + y]));
        mark.colHead.push(push(['Tên TK', 'ID TKQC', 'TỔNG THÁNG'].concat(dayHdr)));
        const gTot = {}; for (let d = 1; d <= nDays; d++) gTot[d] = 0; let gGrand = 0;
        Object.keys(cobj).sort(function(a, b){ return byTen(a, b, cobj); }).forEach(function(k){
          const a = cobj[k]; let tot = 0; const days = [];
          for (let d = 1; d <= nDays; d++) { const v = a.d[d] || 0; days.push(v); tot += v; gTot[d] += v; }
          const r = push([a.ten, '', tot].concat(days)); mark.idCells.push({ r: r, id: a.id }); gGrand += tot;
        });
        mark.tong.push(push(['▶ Tổng chi tiêu', '', gGrand].concat(rangeDays(gTot))));
      }
      // KHỐI LẬP HOÁ ĐƠN (theo tháng)
      const lobj = (lhd[md] && lhd[md][ym]) ? lhd[md][ym] : {};
      if (Object.keys(lobj).length) {
        mark.blk.push(push([md + ' · LẬP HOÁ ĐƠN · Tháng ' + m + '/' + y]));
        mark.colHead.push(push(['Tên TK', 'ID TKQC', 'Số Thẻ', 'TỔNG THÁNG (đã VAT+phí)']));
        let grand = 0;
        Object.keys(lobj).sort(function(a, b){ return byTen(a, b, lobj); }).forEach(function(k){
          const a = lobj[k]; const r = push([a.ten, '', '', Math.round(a.tong)]);
          mark.idCells.push({ r: r, id: a.id }); mark.cardCells.push({ r: r, card: a.card }); grand += a.tong;
        });
        mark.tong.push(push(['▶ Tổng lập hoá đơn', '', '', Math.round(grand)]));
      }
    });
    push(['']);
  });
  if (!coData) push(['(Chưa có tài khoản đặc thù nào — sẽ hiện khi có TK tên chứa Tuyển dụng/HR hoặc PR/Truyền thông)']);

  // Ghi + định dạng
  let sh = ss.getSheetByName('CHI PHÍ ĐẶC THÙ'); if (!sh) sh = ss.insertSheet('CHI PHÍ ĐẶC THÙ');
  sh.clear();
  const nRow = out.length;
  sh.getRange(1, 1, nRow, nCol).setValues(out);
  sh.getRange(1, 1, nRow, nCol).setFontFamily('Arial').setVerticalAlignment('middle');
  sh.getRange(1, 3, nRow, nCol - 2).setNumberFormat('#,##0');
  // ID (cột B) + Số Thẻ (cột C) ghi dạng TEXT để không bị scientific
  mark.idCells.forEach(function(x){ sh.getRange(x.r, 2).setNumberFormat('@').setValue(String(x.id)); });
  mark.cardCells.forEach(function(x){ if (x.card) sh.getRange(x.r, 3).setNumberFormat('@').setValue(String(x.card)); });
  mark.title.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#5a2d82').setFontColor('#fff').setFontWeight('bold').setFontSize(12); sh.setRowHeight(r, 36); });
  mark.sec.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#b388d9').setFontColor('#1f1330').setFontWeight('bold'); });
  mark.blk.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#eadcf5').setFontWeight('bold'); });
  mark.colHead.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#eef2f8').setFontWeight('bold'); });
  mark.tong.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setFontWeight('bold').setBackground('#fff7e6'); });
  sh.getRange(1, 1, nRow, nCol).setBorder(true, true, true, true, true, true, '#d9dee7', SpreadsheetApp.BorderStyle.SOLID);
  sh.setColumnWidth(1, 300); sh.setColumnWidth(2, 170); sh.setColumnWidth(3, 130);
  for (let c = 4; c <= nCol; c++) sh.setColumnWidth(c, 78);
  sh.setFrozenColumns(3); sh.setFrozenRows(1);
  ss.toast('Đã dựng tab CHI PHÍ ĐẶC THÙ', 'OK', 5);
}

// ============================================================================
// CHẠY TAY 1 LẦN — DỌN KHO CŨ theo luật mới:
//   • Chuyển các dòng TK đặc thù đang nằm trong LICH_SU -> LICH_SU_DACTHU (gỡ khỏi LICH_SU).
//   • Gỡ các dòng TK không pháp nhân (nguồn 'Khác') khỏi LICH_SU.
// KHÔNG đụng T4–T6 đã đóng băng trên bảng (chỉ dọn kho gốc). Sau khi chạy: dựng lại báo cáo.
// ============================================================================
function donKhoDacThu() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ls = ss.getSheetByName('LICH_SU');
  if (!ls || ls.getLastRow() < 2) { ss.toast('LICH_SU trống.', 'Bỏ qua', 5); return; }
  const data = ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues();
  const giu = [], sangDT = [];
  let boKhac = 0;
  data.forEach(function(r){
    const md = _mucDichDacThu(r[2]);
    if (md) { sangDT.push([r[0], String(r[1]), r[2], r[3], r[4], r[5], r[6], md]); return; }   // đặc thù -> kho riêng
    if (_nguonTK(r[2]) === 'Khác') { boKhac++; return; }                                        // không pháp nhân -> bỏ
    giu.push([r[0], String(r[1]), r[2], r[3], r[4], r[5], r[6]]);                               // hợp lệ -> giữ
  });
  // Ghi lại LICH_SU (chỉ TK hợp lệ)
  ls.clear();
  ls.getRange(1, 1, 1, 7).setValues([['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT']]);
  if (giu.length) {
    ls.getRange(2, 2, giu.length, 1).setNumberFormat('@');
    ls.getRange(2, 1, giu.length, 1).setNumberFormat('dd/MM/yyyy');
    SpreadsheetApp.flush();
    ls.getRange(2, 1, giu.length, 7).setValues(giu);
  }
  // Gộp phần đặc thù vào LICH_SU_DACTHU (giữ dữ liệu đặc thù đã có, thêm phần chuyển sang; dedup theo Ngày|ID|Tên)
  let lsDT = ss.getSheetByName('LICH_SU_DACTHU');
  const cu = (lsDT && lsDT.getLastRow() > 1) ? lsDT.getRange(2, 1, lsDT.getLastRow() - 1, 8).getValues() : [];
  const daCo = {}; cu.forEach(function(r){ daCo[dmy(r[0]) + '|' + String(r[1]) + '|' + chuanHoaTxt(r[2])] = true; });
  const gop = cu.map(function(r){ return [r[0], String(r[1]), r[2], r[3], r[4], r[5], r[6], r[7]]; });
  let themDT = 0;
  sangDT.forEach(function(r){
    const k = dmy(r[0]) + '|' + String(r[1]) + '|' + chuanHoaTxt(r[2]);
    if (daCo[k]) return; daCo[k] = true; gop.push(r); themDT++;
  });
  if (lsDT || gop.length) {
    if (!lsDT) lsDT = ss.insertSheet('LICH_SU_DACTHU');
    lsDT.clear();
    lsDT.getRange(1, 1, 1, 8).setValues([['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT', 'Mục đích']]);
    if (gop.length) {
      lsDT.getRange(2, 2, gop.length, 1).setNumberFormat('@');
      lsDT.getRange(2, 1, gop.length, 1).setNumberFormat('dd/MM/yyyy');
      SpreadsheetApp.flush();
      lsDT.getRange(2, 1, gop.length, 8).setValues(gop);
    }
  }
  ss.toast('Dọn kho: chuyển ' + themDT + ' dòng đặc thù · bỏ ' + boKhac + ' dòng không pháp nhân. Hãy dựng lại báo cáo.', 'Xong', 12);
}