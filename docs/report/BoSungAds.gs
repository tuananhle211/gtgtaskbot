// ===== BỔ SUNG (BACKFILL) ADS THEO KHOẢNG NGÀY TỪ FACEBOOK =====
// Kéo lịch sử ads theo TỪNG NGÀY trong 1 khoảng vào LICH_SU (bù ngày chưa chốt).
// Chạy ĐỘC LẬP: chỉ ghi đè dòng NẰM TRONG khoảng ngày + của tài khoản kéo được;
// ngày ngoài khoảng & tháng khác GIỮ NGUYÊN. Không đụng đối soát/khung giờ/Telegram.
//
// 2 KIỂU:
//   - bonSungAdsKhoang     : chỉ tài khoản ĐANG ACTIVE (nhẹ, cho tháng hiện tại)
//   - bonSungAdsKhoangFull : KỂ CẢ tài khoản ĐÃ TẮT (đủ, cho số lịch sử ổn định)

// ===== NÚT BẤM =====
// Tháng 7 (đã chạy, active): 01-23
function bonSungAds_0107_2307() { bonSungAdsKhoang('2026-07-01', '2026-07-23'); }

// Tháng 4-5-6 — BẢN ĐẦY ĐỦ (kể cả tài khoản đã tắt), số ổn định:
function bonSungAds_Thang4den6_Full() { bonSungAdsKhoangFull('2026-04-01', '2026-06-30'); }
// Nếu chạy cả 3 tháng lâu/hết giờ, chạy TỪNG THÁNG:
function bonSungAds_Thang4_Full() { bonSungAdsKhoangFull('2026-04-01', '2026-04-30'); }
function bonSungAds_Thang5_Full() { bonSungAdsKhoangFull('2026-05-01', '2026-05-31'); }
function bonSungAds_Thang6_Full() { bonSungAdsKhoangFull('2026-06-01', '2026-06-30'); }

// ===== 2 CỬA VÀO =====
function bonSungAdsKhoang(sinceStr, untilStr) {
  _backfillAds(sinceStr, untilStr, _tkActive());
}
function bonSungAdsKhoangFull(sinceStr, untilStr) {
  SpreadsheetApp.getActiveSpreadsheet().toast('Đang dò tài khoản có chi tiêu (kể cả đã tắt)...', 'Chờ chút', 10);
  _backfillAds(sinceStr, untilStr, _tkCoChiTieu(sinceStr, untilStr));
}

// ===== LẤY DANH SÁCH TÀI KHOẢN =====
// Chỉ tài khoản đang bật
function _tkActive() {
  return goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' +
      encodeURIComponent(ACCESS_TOKEN))
    .filter(function(a){ return a.account_status === 1; });
}
// Tất cả tài khoản (kể cả đã tắt) NHƯNG chỉ giữ cái CÓ CHI TIÊU trong kỳ (lượt 1 - nhẹ)
function _tkCoChiTieu(sinceStr, untilStr) {
  const all = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id&limit=500&access_token=' +
      encodeURIComponent(ACCESS_TOKEN));
  const out = [];
  for (let i = 0; i < all.length; i += 50) {
    const nhom = all.slice(i, i + 50);
    const batch = nhom.map(function(a){
      return { method: 'GET', relative_url: 'act_' + a.account_id +
        '/insights?level=account&fields=spend' +
        '&time_range=' + encodeURIComponent(JSON.stringify({ since: sinceStr, until: untilStr })) +
        '&limit=1' };
    });
    const res = goiBatch(batch);
    res.forEach(function(r, idx){
      if (!r || r.code !== 200 || !r.body) return;
      let body; try { body = JSON.parse(r.body); } catch (e) { return; }
      if (body.data && body.data.length && Number(body.data[0].spend || 0) > 0) out.push(nhom[idx]);
    });
  }
  return out;
}

// ===== LÕI: kéo theo NGÀY + ghi đè AN TOÀN =====
function _backfillAds(sinceStr, untilStr, accts) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sp = sinceStr.split('-'), up = untilStr.split('-');
  const dSince = new Date(Number(sp[0]), Number(sp[1]) - 1, Number(sp[2]));
  const dUntil = new Date(Number(up[0]), Number(up[1]) - 1, Number(up[2]));
  const mktList = docDSMKT(ss);
  ss.toast('Đang kéo ads ' + sinceStr + ' → ' + untilStr + ' (' + accts.length + ' tài khoản)...', 'Chờ chút', 10);

  const byKey = {}; // 'id|dd/MM/yyyy' -> {id, ten, d, chiTieu, sdt}
  for (let i = 0; i < accts.length; i += 50) {
    const nhom = accts.slice(i, i + 50);
    const batch = nhom.map(function(a){
      return { method: 'GET', relative_url: 'act_' + a.account_id +
        '/insights?level=account&fields=spend,actions&time_increment=1' +
        '&time_range=' + encodeURIComponent(JSON.stringify({ since: sinceStr, until: untilStr })) +
        '&limit=500' };
    });
    const res = goiBatch(batch);
    res.forEach(function(r, idx){
      if (!r || r.code !== 200 || !r.body) return;
      let body; try { body = JSON.parse(r.body); } catch (e) { return; }
      if (!body.data || !body.data.length) return;
      const id = nhom[idx].account_id, ten = nhom[idx].name;
      body.data.forEach(function(row){
        const ds = String(row.date_start || '');
        const pr = ds.split('-'); if (pr.length !== 3) return;
        const dstr = pr[2] + '/' + pr[1] + '/' + pr[0];
        const chiTieu = Number(row.spend || 0);
        const sdt = layGiaTriAction(row.actions, ACTION_MUA);
        const key = id + '|' + dstr;
        if (!byKey[key]) byKey[key] = { id: id, ten: ten, d: dstr, chiTieu: 0, sdt: 0 };
        byKey[key].chiTieu += chiTieu; byKey[key].sdt += sdt;
      });
    });
  }

  // AN TOÀN: không kéo được gì -> GIỮ NGUYÊN LICH_SU
  if (Object.keys(byKey).length === 0) {
    ss.toast('Không kéo được ads (không có chi tiêu / Facebook nghẽn) — GIỮ NGUYÊN dữ liệu', 'Dừng an toàn', 10);
    return;
  }

  const bfAccts = {};
  Object.keys(byKey).forEach(function(k){ bfAccts[byKey[k].id] = true; });

  let ls = ss.getSheetByName('LICH_SU'); if (!ls) ls = ss.insertSheet('LICH_SU');
  const header = ['Ngày', 'ID', 'Tên TK', 'Marketing', 'Nguồn', 'Chi tiêu', 'Số ĐT'];
  const old = ls.getLastRow() > 1 ? ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues() : [];
  const keep = old.filter(function(r){
    const d = toDate(r[0]);
    if (!(d instanceof Date) || isNaN(d.getTime())) return true;
    const inRange = (d.getTime() >= dSince.getTime() && d.getTime() <= dUntil.getTime());
    if (!inRange) return true;                        // ngoài khoảng -> giữ
    return !bfAccts[String(r[1])];                    // trong khoảng: chỉ bỏ tài khoản backfill
  }).map(function(r){ r[0] = toDate(r[0]); return r; });

  Object.keys(byKey).forEach(function(k){
    const o = byKey[k]; const ch = chuanHoaTxt(o.ten);
    const nguon = (typeof _nguonTK === 'function') ? _nguonTK(o.ten)
      : (ch.indexOf('COREMIND') >= 0 ? 'COREMIND' : (ch.indexOf('GPI') >= 0 ? 'GPI' : (ch.indexOf('APEX') >= 0 ? 'APEX' : 'Khác')));
    keep.push([toDate(o.d), o.id, o.ten, ganMKT(ch, mktList), nguon, o.chiTieu, o.sdt]);
  });

  ls.clear();
  ls.getRange(1, 1, 1, 7).setValues([header]);
  if (keep.length) {
    ls.getRange(2, 1, keep.length, 7).setValues(keep);
    ls.getRange(2, 1, keep.length, 1).setNumberFormat('dd/MM/yyyy');
    ls.getRange(2, 2, keep.length, 1).setNumberFormat('@');
  }

  dungBaoCaoCaNhan();
  if (typeof dungDashboard === 'function') dungDashboard();
  ss.toast('Đã bổ sung ads ' + sinceStr + ' → ' + untilStr + ' (' + Object.keys(byKey).length + ' dòng ngày·tài khoản)', 'Xong', 8);
}