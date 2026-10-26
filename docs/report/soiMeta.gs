// ===== SOI META v2 — CHẨN ĐOÁN + THỬ CÁCH GỌI TỐI ƯU (field expansion) =====
// AN TOÀN: chỉ ĐỌC + Logger.log. KHÔNG ghi sheet, KHÔNG đụng trigger/tự động, KHÔNG cần token.
// Chạy ĐÚNG LÚC ĐANG LỖI (chiều/tối, khi khung giờ về rỗng) để so sánh 2 cách gọi.
// Thêm file mới -> Lưu -> chạy soiMeta() -> mở Nhật ký thực thi, chụp gửi Claude.
function soiMeta() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  Logger.log('==== SOI META v2 — bắt đầu ====');

  // 1) Danh sách tài khoản
  let accsAll;
  try {
    accsAll = goiAPI('https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' +
      encodeURIComponent(ACCESS_TOKEN));
  } catch (e) { Logger.log('LỖI /me/adaccounts: ' + e.message); return; }
  let dem1 = 0; accsAll.forEach(function(a){ if (a.account_status === 1) dem1++; });
  Logger.log('Tổng TK: ' + accsAll.length + ' | đang hoạt động: ' + dem1);
  Logger.log('Danh sách CHỐT (rộng): ' + dsTKCanQuet(ss, accsAll, 14, false).length + ' TK');
  const leanList = dsTKCanQuet(ss, accsAll, 5, true);
  Logger.log('Danh sách SNAPSHOT (gọn): ' + leanList.length + ' TK');

  // 2) CÁCH HIỆN TẠI — batch per-account (5 TK từ danh sách snapshot). Xem giờ này còn bị chặn không.
  Logger.log('---- [A] Cách hiện tại: batch per-account (5 TK) ----');
  var t0 = new Date().getTime();
  try {
    const nhom = leanList.slice(0, 5);
    const batch = nhom.map(function(a){
      return { method: 'GET', relative_url: 'act_' + a.account_id + '/insights?level=account&fields=spend&date_preset=today' };
    });
    const resp = UrlFetchApp.fetch('https://graph.facebook.com/' + API_VERSION + '/', {
      method: 'post', payload: { access_token: ACCESS_TOKEN, batch: JSON.stringify(batch) }, muteHttpExceptions: true
    });
    const arr = JSON.parse(resp.getContentText());
    const codes = Array.isArray(arr) ? arr.map(function(x){ return x ? x.code : 'null'; }) : arr;
    Logger.log('  code từng sub-request: ' + JSON.stringify(codes) + ' | ' + Math.round((new Date().getTime()-t0)/1000) + 's');
    if (Array.isArray(arr)) { let sp=0; arr.forEach(function(x){ try{ var b=JSON.parse(x.body); if(b.data&&b.data.length) sp+=Number(b.data[0].spend||0);}catch(e){} }); Logger.log('  tổng spend 5 TK: ' + Math.round(sp)); }
  } catch (e) { Logger.log('  LỖI: ' + e.message); }

  // 3) CÁCH TỐI ƯU — field expansion: lấy chi tiêu LỒNG trong adaccounts, 1 lệnh cho 50 TK.
  //    Nếu cách này RA SỐ ngay lúc [A] đang bị chặn -> né được throttle -> là bản viết lại nên dùng.
  Logger.log('---- [B] Cách tối ưu: field expansion (1 lệnh / 50 TK) ----');
  var t1 = new Date().getTime();
  try {
    const url = 'https://graph.facebook.com/' + API_VERSION +
      '/me/adaccounts?fields=account_id,name,insights.date_preset(today)%7Bspend%2Cactions%7D&limit=50&access_token=' +
      encodeURIComponent(ACCESS_TOKEN);
    const resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    Logger.log('  HTTP code: ' + resp.getResponseCode() + ' | thời gian: ' + Math.round((new Date().getTime()-t1)/1000) + 's');
    const data = JSON.parse(resp.getContentText());
    if (data.error) { Logger.log('  Lỗi Meta: ' + JSON.stringify(data.error)); }
    else {
      let coIns = 0, tongSpend = 0;
      (data.data || []).forEach(function(a){
        if (a.insights && a.insights.data && a.insights.data.length) { coIns++; tongSpend += Number(a.insights.data[0].spend || 0); }
      });
      Logger.log('  Trả về ' + (data.data || []).length + ' TK | ' + coIns + ' TK CÓ chi tiêu hôm nay | tổng spend trang này: ' + Math.round(tongSpend));
    }
  } catch (e) { Logger.log('  LỖI field-expansion: ' + e.message); }

  Logger.log('==== XONG ====');
}