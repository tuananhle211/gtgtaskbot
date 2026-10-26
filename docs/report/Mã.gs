// ==== DÁN TOKEN VÀO DÒNG DƯỚI (giữ nguyên dấu nháy) ====
const ACCESS_TOKEN = 'EAAptX4zkDOYBSJ9CY0yOwYZAmQxYsowWLpZCu4ss5To0JyOjqXxClIQbQoSuxqBAwR5IoPHSupRoZCGQ5oKLjIhZBKvMyl0ZBy8BbcnoHzp4MsQQu0x56mWWCNn9497Xew0AMnw4yJnUr0hhdX0ARAZAKdM7ckFApMR8XyeiVBhbTIeMnzCevFZA6sDSlak2QZDZD';

const API_VERSION = 'v25.0';
const ACTION_MUA = 'omni_purchase';
const ACTION_MESS = 'onsite_conversion.messaging_conversation_started_7d';
const CAP_NOIDUNG = 400;

// ⭐ LỌC TÀI KHOẢN CẦN LẤY DỮ LIỆU — công tắc DUY NHẤT cho toàn hệ thống.
// HIỆN: lấy TẤT CẢ tài khoản (kể cả TK die/khoá/chưa thanh toán) -> KHÔNG SÓT số của TK chạy giữa kỳ rồi die.
// TK không phát sinh chi tiêu/hoá đơn sẽ tự không tạo dòng, nên lấy hết vẫn an toàn.
// ĐƯỜNG LUI: muốn quay lại chỉ lấy TK đang hoạt động -> đổi thân hàm thành:
//            return (accs||[]).filter(function(a){ return a.account_status === 1; });
function locTKCanLay(accs) {
    return accs || [];
}

// ⭐ DANH SÁCH TÀI KHOẢN CẦN QUÉT (nhẹ, chống timeout): TK đang hoạt động + TK ĐÃ CÓ chi tiêu/hoá đơn
// trong ~SO_NGAY ngày gần đây (đọc từ LICH_SU + LICH_SU_LHD, KHÔNG tốn lệnh Meta). Nhờ vậy bắt được
// cả TK "đang chạy xong die" mà KHÔNG phải quét hàng trăm TK chết lâu -> lượt kéo nhanh, không quá 6 phút.
// chiDaTieu=false (mặc định, dùng cho CHỐT): quét TK đang hoạt động HOẶC vừa tiêu/hoá đơn trong soNgay ngày.
// chiDaTieu=true (dùng cho SNAPSHOT): CHỈ quét TK vừa tiêu/hoá đơn gần đây (bỏ TK active-nhưng-không-tiêu)
//   -> danh sách gọn hẳn (từ ~330 xuống ~80-150) -> ít lệnh gọi Meta -> nhanh, không chạm trần hạn mức.
function dsTKCanQuet(ss, accsAll, soNgay, chiDaTieu, soNgayMoi) {
    soNgay = soNgay || 14;
    const gan = {};
    const moc = new Date(new Date().getTime() - soNgay * 24 * 3600 * 1000);
    const mocMoi = soNgayMoi ? new Date(new Date().getTime() - soNgayMoi * 24 * 3600 * 1000) : null;
    function nap(sheet) {
        const sh = ss.getSheetByName(sheet);
        if (!sh || sh.getLastRow() < 2) return;
        const d = sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues();   // cột Ngày | ID
        d.forEach(function (r) {
            const dt = (typeof toDate === 'function') ? toDate(r[0]) : new Date(r[0]);
            if (dt instanceof Date && !isNaN(dt.getTime()) && dt >= moc) gan[String(r[1]).trim()] = true;
        });
    }
    nap('LICH_SU'); nap('LICH_SU_LHD');
    const seen = {};
    return (accsAll || []).filter(function (a) {
        const id = String(a.account_id).trim();
        if (seen[id]) return false; seen[id] = true;
        // TK MỚI TẠO gần đây -> LUÔN quét (bắt TK vừa mở lần đầu, dù chưa có dòng nào trong kho)
        if (mocMoi && a.created_time) { const ct = new Date(a.created_time); if (!isNaN(ct.getTime()) && ct >= mocMoi) return true; }
        if (chiDaTieu) return !!gan[id];                 // CHẶT: chỉ TK vừa tiêu/hoá đơn 14 ngày (+ TK mới ở trên)
        return a.account_status === 1 || gan[id];        // RỘNG: đang hoạt động HOẶC vừa tiêu 14 ngày
    });
}

// 8h: full hôm qua, KHÔNG nội dung (nhẹ) | 12h/17h15/23h: hôm nay CÓ nội dung
function chayHomQua() { layDuLieu('yesterday', 'DATA_HOMQUA', false); }
function chayHomNay() { layDuLieu('today', 'DATA_TONG', false); }   // BỎ lấy nội dung QC -> luồng nhẹ, nhanh, hết lỗi chập chờn khi kéo nội dung
// ⚙️ CHẠY TAY khi vừa THÊM nhiều TK / TK cũ nghỉ lâu chạy lại: kéo RỘNG (cả active) rồi chốt hôm qua.
// Chậm hơn (quét nhiều TK) nhưng chỉ chạy khi cần bù -> KHÔNG đặt lịch tự động.
function chayHomQuaFull() {
    layDuLieu('yesterday', 'DATA_HOMQUA', false, true);
    const hq = new Date(new Date().getTime() - 24 * 3600 * 1000);
    if (typeof chotNgay === 'function') chotNgay('DATA_HOMQUA', hq);
}
function capNhatBaoCao() { chayHomNay(); }

function layDuLieu(datePreset, sheetName, layND, rong) {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(sheetName);
    if (!sh) sh = ss.insertSheet(sheetName);

    const now = Utilities.formatDate(new Date(), 'Asia/Ho_Chi_Minh', 'dd/MM/yyyy HH:mm');
    const headers = ['Tên tài khoản', 'ID tài khoản', 'Chi tiêu', 'Số Mess', 'Giá Mess', 'Lượt Mua', 'Chi phí / lượt mua'];
    if (layND) headers.push('Nội dung quảng cáo');

    // ⏱️ NGÂN SÁCH THỜI GIAN CỨNG = 150s (luôn còn hơn 3 phút đệm dưới trần 6 phút).
    const NGAN_SACH_MS = 150000;
    const batDauKeo = new Date().getTime();
    function daChay() { return new Date().getTime() - batDauKeo; }

    // 1) LẤY DANH SÁCH TÀI KHOẢN (nhẹ, KHÔNG kèm insights) rồi LỌC gọn nhóm cần quét.
    //    MẶC ĐỊNH (rong=false): CHỈ TK vừa tiêu/hoá đơn trong 14 ngày -> gọn nhất, nhanh cả giờ cao điểm.
    //    BỎ tiêu chí "đang active" (~331 active phần lớn nằm im -> quét dư -> chậm/timeout giờ cao điểm).
    //    TK mới / TK cũ nghỉ lâu chạy lại: bắt qua kho hoá đơn khung 7h, hoặc chạy tay chayHomQuaFull (rong=true) để bù.
    let accsAll = [];
    try {
        accsAll = goiAPI('https://graph.facebook.com/' + API_VERSION +
            '/me/adaccounts?fields=name,account_id,account_status&limit=500&access_token=' +
            encodeURIComponent(ACCESS_TOKEN));
    } catch (e) { Logger.log('Lỗi lấy DS tài khoản: ' + e.message); }
    const canQuet = rong ? dsTKCanQuet(ss, accsAll, 14, false)   // RỘNG (chạy tay bù): cả active
        : dsTKCanQuet(ss, accsAll, 14, true);   // CHẶT (mặc định): chỉ TK vừa tiêu/hoá đơn 14 ngày
    const tenMap = {};
    accsAll.forEach(function (a) { tenMap[String(a.account_id).trim()] = a.name; });

    // 2) KÉO CHI TIÊU chỉ cho NHÓM CẦN QUÉT — batch 50 TK/lượt, có ngân sách 150s.
    //    Một lượt batch hỏng -> bỏ lượt đó, lượt sau vẫn chạy (goiBatchNhe: 2 lần thử, nghỉ ngắn).
    const rows = [];
    for (let i = 0; i < canQuet.length && daChay() < NGAN_SACH_MS; i += 50) {
        const nhom = canQuet.slice(i, i + 50);
        const batch = nhom.map(function (a) {
            return {
                method: 'GET', relative_url: 'act_' + a.account_id +
                    '/insights?level=account&fields=spend,actions,cost_per_action_type&date_preset=' + datePreset
            };
        });
        const res = goiBatchNhe(batch);
        if (!res) continue;
        res.forEach(function (r, idx) {
            if (!r || r.code !== 200 || !r.body) return;
            let b; try { b = JSON.parse(r.body); } catch (e) { return; }
            if (!b.data || !b.data.length) return;                  // TK không chi tiêu -> bỏ
            const it = b.data[0];
            const id = String(nhom[idx].account_id).trim();
            const chiTieu = Number(it.spend || 0);
            const luotMua = layGiaTriAction(it.actions, ACTION_MUA);
            let cpMua = layGiaTriAction(it.cost_per_action_type, ACTION_MUA);
            if (!cpMua && luotMua > 0) cpMua = Math.round(chiTieu / luotMua);
            rows.push([
                tenMap[id] || ('act_' + id), id, chiTieu,
                layGiaTriAction(it.actions, ACTION_MESS),
                layGiaTriAction(it.cost_per_action_type, ACTION_MESS),
                luotMua, cpMua
            ]);
        });
    }
    Logger.log('Pull ' + sheetName + ': quét ' + canQuet.length + '/' + accsAll.length +
        ' TK · ra ' + rows.length + ' dòng · ' + Math.round(daChay() / 1000) + 's');

    sh.clearContents();
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
    if (rows.length) {
        sh.getRange(2, 2, rows.length, 1).setNumberFormat('@');
        sh.getRange(2, 1, rows.length, headers.length).setValues(rows);
        for (let c = 3; c <= 7; c++) sh.getRange(2, c, rows.length, 1).setNumberFormat('#,##0');
    }
    const nhan = (datePreset === 'yesterday') ? 'Số ngày hôm qua' : 'Số hôm nay';
    sh.getRange(rows.length + 3, 1).setValue(nhan + ' — cập nhật lúc: ' + now);

    const head = sh.getRange(1, 1, 1, headers.length);
    head.setFontWeight('bold').setBackground('#1f2a44').setFontColor('#ffffff');
    sh.setFrozenRows(1);
    ss.toast('Xong ' + sheetName + '! ' + rows.length + ' dòng', 'OK', 6);
}

// ===== GỌI CÓ RETRY (chống lỗi mạng tạm thời + "too much traffic") =====
// Thử tối đa 5 lần, chờ TĂNG DẦN 3→15s để Google/Meta hạ throttle rồi mới gọi lại.
function fetchRetry(url, options) {
    options = options || { muteHttpExceptions: true };
    let lastErr;
    for (let i = 0; i < 5; i++) {
        try { return UrlFetchApp.fetch(url, options); }
        catch (e) { lastErr = e; Utilities.sleep(3000 * (i + 1)); }
    }
    throw lastErr;
}

function goiBatch(batch) {
    const resp = fetchRetry('https://graph.facebook.com/' + API_VERSION + '/', {
        method: 'post',
        payload: { access_token: ACCESS_TOKEN, batch: JSON.stringify(batch) },
        muteHttpExceptions: true
    });
    const data = JSON.parse(resp.getContentText());
    if (data.error) throw new Error('Batch lỗi: ' + data.error.message);
    return data;
}

// ⚡ GỌI BATCH "NHẸ" — dùng riêng cho khâu kéo chi tiêu (layDuLieu), KHÔNG đụng goiBatch của LHĐ.
// Khác goiBatch ở chỗ: thử tối đa 2 lần, nghỉ NGẮN 2s (thay vì fetchRetry nghỉ 3→15s). Nhờ vậy khi
// Meta chặn tạm giờ cao điểm thì 1 batch hỏng chỉ tốn ~4s rồi trả null (để lượt quét sau thử lại),
// KHÔNG kẹt 45–90s làm đội thời gian tới 361s rồi bị Google giết. Trả về mảng batch, hoặc null nếu hỏng.
function goiBatchNhe(batch) {
    for (let t = 0; t < 2; t++) {
        try {
            const resp = UrlFetchApp.fetch('https://graph.facebook.com/' + API_VERSION + '/', {
                method: 'post',
                payload: { access_token: ACCESS_TOKEN, batch: JSON.stringify(batch) },
                muteHttpExceptions: true
            });
            const data = JSON.parse(resp.getContentText());
            if (data && data.error) { Utilities.sleep(2000); continue; }   // lỗi tổng (rate limit) -> nghỉ ngắn thử lại
            if (Array.isArray(data)) return data;
            return null;
        } catch (e) { Utilities.sleep(2000); }
    }
    return null;
}

function goiAPI(url) {
    let results = [], next = url, guard = 0;
    while (next && guard < 300) {
        guard++;
        const data = JSON.parse(fetchRetry(next, { muteHttpExceptions: true }).getContentText());
        if (data.error) throw new Error('Meta API lỗi: ' + data.error.message);
        if (data.data && data.data.length) results = results.concat(data.data);
        next = (data.paging && data.paging.next) ? data.paging.next : null;
    }
    return results;
}

function layNoiDungCache(ss, adIds) {
    let cache = ss.getSheetByName('CACHE_ND');
    if (!cache) cache = ss.insertSheet('CACHE_ND');
    const map = {};
    const cData = cache.getDataRange().getValues();
    for (let i = 0; i < cData.length; i++) {
        if (cData[i][0] !== '' && cData[i][0] !== null) map[String(cData[i][0])] = cData[i][1];
    }
    const uniq = {};
    adIds.forEach(function (id) { if (id) uniq[String(id)] = true; });
    const missing = Object.keys(uniq).filter(function (id) { return map[id] === undefined; });
    const toFetch = missing.slice(0, CAP_NOIDUNG);
    if (toFetch.length) {
        const fetched = layNoiDungTheoAdIds(toFetch);
        const newRows = [];
        toFetch.forEach(function (id) {
            const t = fetched[id] || '';
            map[id] = t;
            newRows.push([id, t]);
        });
        if (newRows.length) cache.getRange(cache.getLastRow() + 1, 1, newRows.length, 2).setValues(newRows);
    }
    return map;
}

function layNoiDungTheoAdIds(adIds) {
    const map = {};
    const needPost = {};
    for (let i = 0; i < adIds.length; i += 50) {
        const chunk = adIds.slice(i, i + 50);
        const url = 'https://graph.facebook.com/' + API_VERSION + '/?ids=' + chunk.join(',') +
            '&fields=creative%7Bbody,asset_feed_spec,object_story_spec,effective_object_story_id%7D' +
            '&access_token=' + encodeURIComponent(ACCESS_TOKEN);
        try {
            const data = JSON.parse(fetchRetry(url, { muteHttpExceptions: true }).getContentText());
            if (data.error) continue;
            Object.keys(data).forEach(function (id) {
                const c = (data[id] && data[id].creative) || {};
                let t = c.body || '';
                if (!t && c.asset_feed_spec && c.asset_feed_spec.bodies && c.asset_feed_spec.bodies.length)
                    t = c.asset_feed_spec.bodies[0].text || '';
                if (!t && c.object_story_spec) {
                    const s = c.object_story_spec;
                    if (s.link_data && s.link_data.message) t = s.link_data.message;
                    else if (s.video_data && s.video_data.message) t = s.video_data.message;
                    else if (s.photo_data && s.photo_data.caption) t = s.photo_data.caption;
                }
                if (!t && c.effective_object_story_id) needPost[id] = c.effective_object_story_id;
                map[id] = t;
            });
        } catch (e) { }
    }
    const adByPost = {}, postIds = [];
    Object.keys(needPost).forEach(function (adId) {
        const pid = needPost[adId];
        if (!adByPost[pid]) { adByPost[pid] = []; postIds.push(pid); }
        adByPost[pid].push(adId);
    });
    for (let i = 0; i < postIds.length; i += 50) {
        const chunk = postIds.slice(i, i + 50);
        const url = 'https://graph.facebook.com/' + API_VERSION + '/?ids=' + chunk.join(',') +
            '&fields=message&access_token=' + encodeURIComponent(ACCESS_TOKEN);
        try {
            const data = JSON.parse(fetchRetry(url, { muteHttpExceptions: true }).getContentText());
            if (data.error) continue;
            Object.keys(data).forEach(function (pid) {
                const msg = (data[pid] && data[pid].message) || '';
                if (msg) (adByPost[pid] || []).forEach(function (adId) { map[adId] = msg; });
            });
        } catch (e) { }
    }
    return map;
}

function layGiaTriAction(arr, type) {
    if (!arr || !arr.length) return 0;
    for (let i = 0; i < arr.length; i++)
        if (arr[i].action_type === type) return Number(arr[i].value || 0);
    return 0;
}