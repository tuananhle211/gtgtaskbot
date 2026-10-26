// ===== DASHBOARD TỔNG TEAM — THEO NĂM =====
// Nguồn: LICH_SU (ads) + LICH_SU_DS (đối soát). Dùng chung helper file BaoCaoCaNhan:
// docDSMKT, ganMKT, chuanHoaTxt, toDate
// Mỗi năm dựng 2 sheet: "DASHBOARD A - [năm]" (bổ theo marketing) + "DASHBOARD B - [năm]" (bổ theo ngày)

// 12 chỉ số: [nhãn, hàm tính từ base, loại định dạng]
function DASH_METS() {
  return [
    ['Tổng chi tiêu', function(o){ return o.ct; }, 'num'],
    ['NS COREMIND',   function(o){ return o.core; }, 'num'],
    ['NS APEX',       function(o){ return o.apex; }, 'num'],
    ['NS GPI',        function(o){ return o.gpi || 0; }, 'num'],
    ['Số điện thoại', function(o){ return o.sdt; }, 'num'],
    ['Chi phí / SĐT', function(o){ return o.sdt > 0 ? Math.round(o.ct / o.sdt) : 0; }, 'num'],
    ['Lịch nóng',     function(o){ return o.nong; }, 'num'],
    ['Lịch gối',      function(o){ return o.goi; }, 'num'],
    ['Lịch tối ưu',   function(o){ return o.toiuu; }, 'num'],
    ['Khách đến cửa', function(o){ return o.khach; }, 'num'],
    ['Tổng doanh thu',function(o){ return o.dt; }, 'num'],
    ['Bill TB',       function(o){ return o.khach > 0 ? Math.round(o.dt / o.khach) : 0; }, 'num'],
    ['Chi phí Ads/DT',function(o){ return o.dt > 0 ? o.ct / o.dt : 0; }, 'pct']
  ];
}
function DASH_B(){ return { ct: 0, core: 0, apex: 0, gpi: 0, sdt: 0, nong: 0, goi: 0, toiuu: 0, khach: 0, dt: 0, bong: 0, dtHN: 0 }; }

// tinhCaCu = true -> tính lại CẢ T4–T6 (dùng khi "chốt" số 1 lần). Mặc định false: đóng băng T4–T6.
function dungDashboard(tinhCaCu) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ls = ss.getSheetByName('LICH_SU');
  const ads = (ls && ls.getLastRow() > 1) ? ls.getRange(2, 1, ls.getLastRow() - 1, 7).getValues() : [];
  const dsS = ss.getSheetByName('LICH_SU_DS');
  const dsR = (dsS && dsS.getLastRow() > 1) ? dsS.getRange(2, 1, dsS.getLastRow() - 1, dsS.getLastColumn()).getValues() : [];

  const team = {}, mkt = {}, day = {}; // [year][month]...
  function ny(s, y){ return (s[y] = s[y] || {}); }
  function nm(o, m){ return (o[m] = o[m] || DASH_B()); }

  ads.forEach(function(r){
    if (typeof _mucDichDacThu === 'function' && _mucDichDacThu(r[2])) return;   // TK đặc thù -> KHÔNG tính vào Dashboard
    if (typeof _loaiKhoiTong === 'function' && _loaiKhoiTong(r[3], r[2])) return;   // GPI-không-SG của MKT Thắng -> KHÔNG tính vào Dashboard
    const mk = r[3]; if (!mk || mk === 'Chưa gán' || String(mk).indexOf('⚠️') >= 0) return;
    const d = dashToDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
    const y = d.getFullYear(); if (y < 2000 || y > 2100) return; // bỏ ngày rác (1970...)
    const m = d.getMonth() + 1, dd = d.getDate();
    const nguon = r[4], vf = heSoVATCt(nguon, y, m);        // chi tiêu = số gốc (heSoVATCt = 1.0)
    const ct = Number(r[5] || 0) * vf, sdt = Number(r[6] || 0);
    function addAds(o){ o.ct += ct; o.sdt += sdt; if (nguon === 'COREMIND') o.core += ct; if (nguon === 'APEX') o.apex += ct; if (nguon === 'GPI') o.gpi += ct; }
    addAds(nm(ny(team, y), m));
    const my = ny(mkt, y); my[m] = my[m] || {}; my[m][mk] = my[m][mk] || DASH_B(); addAds(my[m][mk]);
    const dy = ny(day, y); dy[m] = dy[m] || {}; dy[m][dd] = dy[m][dd] || DASH_B(); addAds(dy[m][dd]);
  });
  dsR.forEach(function(r){
    const mk = r[1]; if (!mk) return;
    const d = dashToDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
    const y = d.getFullYear(); if (y < 2000 || y > 2100) return; // bỏ ngày rác (1970...)
    const m = d.getMonth() + 1, dd = d.getDate();
    const nong = Number(r[2] || 0), goi = Number(r[3] || 0), toiuu = Number(r[4] || 0), khach = Number(r[5] || 0), dt = Number(r[6] || 0), bong = Number(r[7] || 0), dtHN = Number(r[8] || 0), dtMB = Number(r[9] || 0);
    // Cá nhân (Dashboard A theo marketing): giữ ĐỦ doanh thu, kể cả phần khách data SG làm ở HN (MB)
    function addMkt(o){ o.nong += nong; o.goi += goi; o.toiuu += toiuu; o.khach += khach; o.dt += dt; o.bong += bong; o.dtHN += dtHN; }
    // Team + theo ngày: TRỪ doanh thu MB (khách data SG làm ở HN không tính cho team)
    function addTeam(o){ o.nong += nong; o.goi += goi; o.toiuu += toiuu; o.khach += khach; o.dt += (dt - dtMB); o.bong += bong; o.dtHN += dtHN; }
    addTeam(nm(ny(team, y), m));
    const my = ny(mkt, y); my[m] = my[m] || {}; my[m][mk] = my[m][mk] || DASH_B(); addMkt(my[m][mk]);
    const dy = ny(day, y); dy[m] = dy[m] || {}; dy[m][dd] = dy[m][dd] || DASH_B(); addTeam(dy[m][dd]);
  });

  // Kho HN riêng (khách HN chưa gán MKT) -> cộng vào TỔNG team/ngày + hiện thành 1 dòng ở Dashboard A
  const hnS = ss.getSheetByName('LICH_SU_HN');
  const hnR = (hnS && hnS.getLastRow() > 1) ? hnS.getRange(2, 1, hnS.getLastRow() - 1, hnS.getLastColumn()).getValues() : [];
  hnR.forEach(function(r){
    const d = dashToDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
    const y = d.getFullYear(); if (y < 2000 || y > 2100) return;
    const m = d.getMonth() + 1, dd = d.getDate();
    const khach = Number(r[1] || 0), nong = Number(r[2] || 0), goi = Number(r[3] || 0), toiuu = Number(r[4] || 0), bong = Number(r[5] || 0), dt = Number(r[6] || 0);
    function addHN(o){ o.khach += khach; o.nong += nong; o.goi += goi; o.toiuu += toiuu; o.bong += bong; o.dt += dt; o.dtHN += dt; }
    addHN(nm(ny(team, y), m));
    const dy = ny(day, y); dy[m] = dy[m] || {}; dy[m][dd] = dy[m][dd] || DASH_B(); addHN(dy[m][dd]);
    const my = ny(mkt, y); my[m] = my[m] || {}; my[m]['◆ HN chưa gán'] = my[m]['◆ HN chưa gán'] || DASH_B(); addHN(my[m]['◆ HN chưa gán']);
  });

  // Kho NGUỒN team (Hotline...) -> cộng vào TỔNG team + theo ngày (phần Cơ sở HCM, KHÔNG phải HN) + hiện 1 dòng riêng ở Dashboard A
  const ngS = ss.getSheetByName('LICH_SU_NGUON');
  const ngR = (ngS && ngS.getLastRow() > 1) ? ngS.getRange(2, 1, ngS.getLastRow() - 1, ngS.getLastColumn()).getValues() : [];
  ngR.forEach(function(r){
    const d = dashToDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
    const y = d.getFullYear(); if (y < 2000 || y > 2100) return;
    const m = d.getMonth() + 1, dd = d.getDate();
    const nguon = String(r[1] || 'Nguồn khác').trim();
    const khach = Number(r[2] || 0), nong = Number(r[3] || 0), goi = Number(r[4] || 0), toiuu = Number(r[5] || 0), bong = Number(r[6] || 0), dt = Number(r[7] || 0);
    function addNg(o){ o.khach += khach; o.nong += nong; o.goi += goi; o.toiuu += toiuu; o.bong += bong; o.dt += dt; } // Cơ sở HCM: KHÔNG cộng dtHN
    addNg(nm(ny(team, y), m));
    const dy = ny(day, y); dy[m] = dy[m] || {}; dy[m][dd] = dy[m][dd] || DASH_B(); addNg(dy[m][dd]);
    const my = ny(mkt, y); my[m] = my[m] || {}; const lbl = '◆ ' + nguon; my[m][lbl] = my[m][lbl] || DASH_B(); addNg(my[m][lbl]);
  });

  // LHĐ (đã VAT 8/10% + phí thẻ theo cụm TK): gom theo THÁNG cho team + theo TỪNG MARKETING.
  //   lhdTeam[y][m]        -> "Số Tiền Lập Hoá Đơn" ở khối tổng Dashboard B
  //   lhdMkt[y][m][mkChuẩn]-> cột "Tiền Lập Hoá Đơn" theo từng marketing ở Dashboard A
  const lhdTeam = {};
  const lhdMkt = {};
  const lhS = ss.getSheetByName('LICH_SU_LHD');
  if (lhS && lhS.getLastRow() > 1) {
    const lhR = lhS.getRange(2, 1, lhS.getLastRow() - 1, 6).getValues();
    lhR.forEach(function(r){
      if (typeof _mucDichDacThu === 'function' && _mucDichDacThu(r[2])) return;   // TK đặc thù -> KHÔNG tính vào Số Tiền LHĐ Dashboard
      if (typeof _loaiKhoiTong === 'function' && _loaiKhoiTong(r[3], r[2])) return;   // GPI-không-SG của MKT Thắng -> KHÔNG tính vào Số Tiền LHĐ Dashboard
      const d = dashToDate(r[0]); if (!(d instanceof Date) || isNaN(d.getTime())) return;
      const y = d.getFullYear(); if (y < 2000 || y > 2100) return;
      const m = d.getMonth() + 1;
      const nguon = (typeof _nguonTK === 'function') ? _nguonTK(r[2])
        : ((typeof chuanHoaTxt === 'function' ? chuanHoaTxt(String(r[2] || '')) : String(r[2] || '').toUpperCase()).indexOf('COREMIND') >= 0 ? 'COREMIND' : 'Khác');
      const phi = (typeof _phiTheLHD === 'function') ? _phiTheLHD(r[2]) : 1.011;   // phí theo cụm TK (XH-SG-VAT/Q-SG: 1.021; khác: 1.011)
      const tien = Number(r[5] || 0) * heSoVATLHD(nguon, y, m) * phi;
      if (!lhdTeam[y]) lhdTeam[y] = {};
      lhdTeam[y][m] = (lhdTeam[y][m] || 0) + tien;
      // theo marketing (cột 4 kho LHĐ) -> chuẩn hoá tên để khớp với marketing bên ads
      const mkKey = (typeof chuanHoaTxt === 'function') ? chuanHoaTxt(String(r[3] || '')) : String(r[3] || '').toUpperCase();
      if (mkKey) {
        if (!lhdMkt[y]) lhdMkt[y] = {};
        if (!lhdMkt[y][m]) lhdMkt[y][m] = {};
        lhdMkt[y][m][mkKey] = (lhdMkt[y][m][mkKey] || 0) + tien;
      }
    });
  }

  const yearsSet = {};
  [team, mkt, day].forEach(function(s){ Object.keys(s).forEach(function(y){ yearsSet[y] = 1; }); });

  // Dọn sheet dashboard của năm không còn hợp lệ (VD sheet 1970 do dữ liệu rác cũ)
  ss.getSheets().forEach(function(sh){
    const mt = String(sh.getName()).match(/^DASHBOARD [AB] - (\d+)$/);
    if (mt && !yearsSet[mt[1]]) ss.deleteSheet(sh);
  });

  let n = 0;
  Object.keys(yearsSet).sort().forEach(function(yStr){
    const y = Number(yStr);
    buildDashA(ss, y, team[y] || {}, mkt[y] || {}, tinhCaCu, lhdMkt[y] || {}, lhdTeam[y] || {});
    buildDashB(ss, y, team[y] || {}, day[y] || {}, lhdTeam[y] || {}, tinhCaCu);
    n++;
  });
  ss.toast('Đã dựng dashboard ' + n + ' năm (A theo MKT + B theo ngày)', 'OK', 6);
}

// Hàm ngày RIÊNG cho Dashboard (độc lập, không phụ thuộc file khác)
function dashToDate(v) {
  let d;
  if (v instanceof Date) d = v;
  else {
    const p = String(v).trim().split('/');
    d = (p.length === 3) ? new Date(Number(p[2]), Number(p[1]) - 1, Number(p[0])) : new Date(v);
  }
  if (!(d instanceof Date) || isNaN(d.getTime())) return d;
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

// KHỐI 1 dùng chung: 12 chỉ số (hàng) x các tháng (cột) — tổng cả team
function dashKhoi1(teamY, months, mark, push, pad) {
  const METS = DASH_METS();
  mark.hdr.push(push(pad(['📊 TỔNG THEO THÁNG (cả team)'])));
  mark.colHead.push(push(pad(['Chỉ số'].concat(months.map(function(m){ return 'Tháng ' + m; })))));
  let start = 0;
  METS.forEach(function(mt, i){
    const row = [mt[0]].concat(months.map(function(m){ return mt[1](teamY[m] || DASH_B()); }));
    const r = push(pad(row));
    if (i === 0) start = r;
    if (mt[2] === 'pct') mark.pctRow.push(r);
  });
  mark.lblCol.push([start, METS.length]);
  push(pad(['']));
}

// KHỐI TỔNG (form đã chốt): các chỉ số theo nhóm, cột = tháng, cả team
// Chỉ số đã có dữ liệu -> điền số; chỉ số CHƯA có nguồn -> để trống chờ Sếp định nghĩa
function dashKhoiTong(teamY, months, mark, push, pad, year, lhdTeamY, oldKPI, SEP, tinhCaCu, frozenTong) {
  SEP = SEP || ';';
  frozenTong = frozenTong || {};
  function tsum(o){ return o.nong + o.goi + o.toiuu; }
  function gopVAT(m){ return ((year > 2026) || (year === 2026 && m >= 7)) ? 1.08 : 1; }   // T7+ ×1.08 ra số thực; T4/5/6 ×1
  function lhdM(m){ return (lhdTeamY && lhdTeamY[m]) ? lhdTeamY[m] : 0; }
  function dtXetKPI(o, m){ return o.dt * gopVAT(m); }
  // Dòng đặc biệt: {kpi:true} = điền tay (giữ số cũ); {ratioKPI:true} = công thức sống Doanh Thu Xét KPI / KPI.
  const SEC = [
    ['① CHI PHÍ QUẢNG CÁO', [
      ['CHI PHÍ ADS', function(o){ return o.ct; }, 'money'],
      ['SỐ TIỀN LẬP HOÁ ĐƠN', function(o, m){ return Math.round(lhdM(m)); }, 'money']
    ]],
    ['② LEAD (SỐ ĐIỆN THOẠI)', [
      ['SĐT', function(o){ return o.sdt; }, 'num'],
      ['CHI PHÍ SĐT', function(o){ return o.sdt > 0 ? Math.round(o.ct / o.sdt) : 0; }, 'money']
    ]],
    ['③ KHÁCH ĐẾN CỬA & CHẤT LƯỢNG CHỐT', [
      ['KHÁCH ĐẾN CỬA', function(o){ return o.khach; }, 'num'],
      ['TỶ LỆ CHỐT', function(o){ return o.sdt > 0 ? o.khach / o.sdt : 0; }, 'pct'],
      ['TỈ TRỌNG KHÁCH NÓNG', function(o){ return tsum(o) > 0 ? o.nong / tsum(o) : 0; }, 'pct'],
      ['TỶ TRỌNG KHÁCH GỐI', function(o){ return tsum(o) > 0 ? o.goi / tsum(o) : 0; }, 'pct'],
      ['TỈ TRỌNG KHÁCH TỐI ƯU', function(o){ return tsum(o) > 0 ? o.toiuu / tsum(o) : 0; }, 'pct'],
      ['TỶ LỆ BONG', function(o){ return o.khach > 0 ? o.bong / o.khach : 0; }, 'pct']
    ]],
    ['④ DOANH THU', [
      ['KPI', { kpi: true }, 'money'],
      ['DOANH THU XÉT KPI', function(o, m){ return Math.round(dtXetKPI(o, m)); }, 'money'],
      ['   ├ CƠ SỞ HCM', function(o, m){ return Math.round((o.dt - o.dtHN) * gopVAT(m)); }, 'money'],
      ['   └ HN CHUYỂN HCM', function(o, m){ return Math.round(o.dtHN * gopVAT(m)); }, 'money'],
      ['DOANH THU HOA HỒNG', function(o){ return Math.round(o.dt); }, 'money'],
      ['TỈ LỆ HOÀN THÀNH KPI', { ratioKPI: true }, 'pct'],
      ['BILL TRUNG BÌNH', function(o){ return o.khach > 0 ? Math.round(o.dt / o.khach) : 0; }, 'money']
    ]],
    ['⑤ HIỆU QUẢ', [
      ['ADS / DOANH THU HOA HỒNG', function(o, m){ const tuT7 = (year > 2026) || (year === 2026 && m >= 7); if (!tuT7) return ''; return o.dt > 0 ? lhdM(m) / o.dt : 0; }, 'pct'],   // Số tiền LHĐ / Doanh Thu Hoa Hồng (T7+)
      ['ADS / DOANH THU THỰC', function(o, m){ const d = dtXetKPI(o, m); return d > 0 ? lhdM(m) / d : 0; }, 'pct']
    ]]
  ];
  mark.hdr.push(push(pad(['📊 KHỐI TỔNG NĂM (cả team) — theo tháng'])));
  mark.colHead.push(push(pad(['CHỈ SỐ'].concat(months.map(function(m){ return 'Tháng ' + m; })))));
  mark.formulas = mark.formulas || [];
  let rowKPI = 0, rowDXK = 0;
  SEC.forEach(function(s){
    mark.sec.push(push(pad([s[0]])));
    s[1].forEach(function(mt){
      const spec = mt[1];
      let r;
      if (spec && spec.kpi) {
        // KPI điền tay: giữ số cũ nếu đã gõ (map theo tháng), chưa có -> để trống
        r = push(pad(['KPI'].concat(months.map(function(m){ return (oldKPI && oldKPI[m] != null && oldKPI[m] !== '') ? oldKPI[m] : ''; }))));
        rowKPI = r; mark.kpiRow = r;
      } else if (spec && spec.ratioKPI) {
        // Tỉ lệ hoàn thành KPI = Doanh Thu Xét KPI / KPI (công thức sống theo từng cột)
        r = push(pad([mt[0]].concat(months.map(function(){ return ''; }))));
        months.forEach(function(m, i){
          const c = 2 + i, cl = _colLetter(c);
          mark.formulas.push({ r: r, c: c, f: '=IFERROR(' + cl + rowDXK + '/' + cl + rowKPI + SEP + '0)' });
        });
      } else {
        const lbTrim = String(mt[0]).trim();
        r = push(pad([mt[0]].concat(months.map(function(m){
          // ĐÓNG BĂNG T4–T6: có số cũ trên bảng -> giữ nguyên. Chỉ T7+ tính lại.
          if (!tinhCaCu && m < 7 && frozenTong[lbTrim] != null &&
              frozenTong[lbTrim][m] != null && frozenTong[lbTrim][m] !== '') {
            return frozenTong[lbTrim][m];
          }
          const o = teamY[m] || DASH_B(); return spec ? spec(o, m) : '';
        }))));
        if (mt[0] === 'DOANH THU XÉT KPI') rowDXK = r;
      }
      if (mt[2] === 'pct') mark.pctRow.push(r);
    });
  });
  push(pad(['']));
}

// Số cột -> chữ cái cột (A, B, ... AA) cho công thức
function _colLetter(c) { let s = ''; while (c > 0) { const r = (c - 1) % 26; s = String.fromCharCode(65 + r) + s; c = Math.floor((c - 1) / 26); } return s; }

// Dò dấu phân cách hàm theo ngôn ngữ bảng (VN dùng ";"). Thử =SUM(1,1): ra 2 -> ","; không -> ";".
function _dashSep(sh) {
  try {
    const c = sh.getRange(1, 1);
    c.setFormula('=SUM(1,1)'); SpreadsheetApp.flush();
    const ok = (c.getValue() === 2);
    c.clearContent();
    return ok ? ',' : ';';
  } catch (e) { try { sh.getRange(1, 1).clearContent(); } catch (e2) {} return ';'; }
}

// Đọc dòng "KPI" cũ ở Dashboard B (map tháng -> giá trị) để GIỮ khi dựng lại. Không có -> {}.
function _docKPICu(sh) {
  const res = {};
  if (!sh || sh.getLastRow() < 2) return res;
  const scan = Math.min(sh.getLastRow(), 80), nc = Math.min(sh.getLastColumn(), 40);
  const grid = sh.getRange(1, 1, scan, nc).getValues();
  let hdrRow = -1;
  for (let i = 0; i < grid.length; i++) { if (String(grid[i][0]).trim() === 'CHỈ SỐ') { hdrRow = i; break; } }
  if (hdrRow < 0) return res;
  const colMonth = {};
  for (let c = 1; c < grid[hdrRow].length; c++) { const mt = String(grid[hdrRow][c]).match(/Tháng\s*(\d+)/); if (mt) colMonth[c] = Number(mt[1]); }
  for (let i = hdrRow + 1; i < grid.length; i++) {
    if (String(grid[i][0]).trim() === 'KPI') {
      Object.keys(colMonth).forEach(function(c){ const v = grid[i][c]; if (v !== '' && v != null) res[colMonth[c]] = v; });
      break;
    }
  }
  return res;
}

// ĐỌC LẠI khối "KHỐI TỔNG NĂM" cũ ở Dashboard B -> { nhãn(trim): { tháng: giá trị } }.
// Dùng để ĐÓNG BĂNG T4–T6. Chỉ đọc vùng từ dòng "KHỐI TỔNG NĂM" tới trước khối "THÁNG m - year".
function _docTongCuDash(sh) {
  const res = {};
  if (!sh || sh.getLastRow() < 2) return res;
  const lastR = sh.getLastRow(), lastC = Math.min(sh.getLastColumn(), 40);
  const grid = sh.getRange(1, 1, lastR, lastC).getValues();
  let hdrRow = -1;
  for (let i = 0; i < grid.length; i++) {
    if (String(grid[i][0]).indexOf('KHỐI TỔNG NĂM') >= 0) { hdrRow = i; break; }
  }
  if (hdrRow < 0 || hdrRow + 1 >= grid.length) return res;
  const dateRow = hdrRow + 1;   // "CHỈ SỐ | Tháng 4 | Tháng 5 | ..."
  const colThang = {};
  for (let c = 1; c < lastC; c++) {
    const mt = String(grid[dateRow][c]).match(/Tháng\s*(\d+)/);
    if (mt) colThang[c] = Number(mt[1]);
  }
  for (let r = dateRow + 1; r < grid.length; r++) {
    const lb = String(grid[r][0]).trim();
    if (/^THÁNG\s+\d+\s*-\s*\d+$/.test(lb)) break;   // đã sang khối 2B (theo ngày)
    if (!lb) continue;
    for (const c in colThang) {
      const v = grid[r][c];
      if (v === '' || v == null) continue;
      if (!res[lb]) res[lb] = {};
      res[lb][colThang[c]] = v;
    }
  }
  return res;
}

// ĐỌC LẠI các khối tháng cũ ở Dashboard A -> { tháng: [ {label, map:{tênCột: giá trị}} ] }.
// Đọc kèm dòng tiêu đề cột cũ để map giá trị THEO TÊN CỘT -> đóng băng T4–T6 không lệch dù layout cột đổi.
function _docDashACu(sh, year) {
  const res = {};
  if (!sh || sh.getLastRow() < 2) return res;
  const grid = sh.getRange(1, 1, sh.getLastRow(), sh.getLastColumn()).getValues();
  for (let i = 0; i < grid.length; i++) {
    const mt = String(grid[i][0]).trim().match(/^THÁNG\s+(\d+)\s*-\s*(\d+)$/);
    if (!mt) continue;
    if (Number(mt[2]) !== year || i + 1 >= grid.length) continue;
    const m = Number(mt[1]);
    const headerOld = grid[i + 1];                         // dòng "Marketing | Tổng chi tiêu | ..."
    const rows = [];
    for (let r = i + 2; r < grid.length; r++) {
      const lb = String(grid[r][0]).trim();
      if (lb === '') break;                                // hết khối
      if (/^THÁNG\s+\d+\s*-\s*\d+$/.test(lb)) break;        // sang khối kế
      const map = {};
      for (let c = 1; c < headerOld.length; c++) {
        const nm = String(headerOld[c]).trim();
        if (nm) map[nm] = grid[r][c];
      }
      rows.push({ label: grid[r][0], map: map });
      if (lb === 'TỔNG TEAM') break;                       // TỔNG TEAM là dòng cuối khối
    }
    if (rows.length) res[m] = rows;
  }
  return res;
}

function buildDashA(ss, year, teamY, mktY, tinhCaCu, lhdMktY, lhdTeamY) {
  lhdMktY = lhdMktY || {}; lhdTeamY = lhdTeamY || {};
  const METS = DASH_METS();
  // Cột Dashboard A = "Marketing" + 12 chỉ số, CHÈN "Tiền Lập Hoá Đơn" ngay sau "NS APEX".
  const LHD_LBL = 'Tiền Lập Hoá Đơn';
  const colNames = ['Marketing'];
  // Chèn "Tiền Lập Hoá Đơn" ngay sau nhóm nguồn (sau NS GPI nếu có, không thì sau NS APEX).
  const anchorLHD = (METS.some(function(x){ return x[0] === 'NS GPI'; })) ? 'NS GPI' : 'NS APEX';
  METS.forEach(function(x){ colNames.push(x[0]); if (x[0] === anchorLHD) colNames.push(LHD_LBL); });
  const pctIdx = colNames.length - 1;   // cột % (Chi phí Ads/DT) luôn là cột cuối

  const monthsSet = {};
  Object.keys(teamY).forEach(function(m){ monthsSet[m] = 1; });
  Object.keys(mktY).forEach(function(m){ monthsSet[m] = 1; });
  const months = Object.keys(monthsSet).map(Number).sort(function(a, b){ return a - b; });
  const nCol = Math.max(colNames.length, months.length + 1);

  const name = 'DASHBOARD A - ' + year;
  let sh = ss.getSheetByName(name);
  const frozenA = (!tinhCaCu && sh) ? _docDashACu(sh, year) : {};   // ĐÓNG BĂNG T4–T6 (đọc trước khi xoá)
  if (!sh) sh = ss.insertSheet(name); sh.clear();
  const out = [];
  const mark = { title: [], hdr: [], colHead: [], pctRow: [], lblCol: [], mHdr: [], tong: [], pctColA: [], sec: [] };
  function push(row){ out.push(row); return out.length; }
  function pad(a){ while (a.length < nCol) a.push(''); return a; }
  // dựng 1 hàng theo thứ tự cột: metric lấy từ obj; cột LHĐ lấy giá trị lhdVal truyền vào
  function rowTinh(label, obj, lhdVal){
    return colNames.map(function(nm){
      if (nm === 'Marketing') return label;
      if (nm === LHD_LBL) return Math.round(lhdVal || 0);
      for (let i = 0; i < METS.length; i++) if (METS[i][0] === nm) return METS[i][1](obj);
      return '';
    });
  }

  mark.title.push(push(pad(['📊 DASHBOARD A — TEAM ADS SG — NĂM ' + year + '  (hiệu suất từng Marketing theo tháng)'])));
  push(pad(['']));

  // KHỐI 2A: mỗi tháng 1 khối, hàng = marketing, cột = 13 chỉ số (có Tiền Lập Hoá Đơn), + hàng TỔNG TEAM
  months.forEach(function(m){
    mark.mHdr.push(push(pad(['THÁNG ' + m + ' - ' + year])));
    mark.colHead.push(push(pad(colNames.slice())));
    const cStart = out.length + 1;
    // ĐÓNG BĂNG T4–T6: dựng lại từ số cũ, map THEO TÊN CỘT (cột LHĐ mới -> trống ở tháng cũ).
    if (!tinhCaCu && m < 7 && frozenA[m] && frozenA[m].length) {
      let mktCount = 0;
      frozenA[m].forEach(function(e){
        const row = colNames.map(function(nm){
          if (nm === 'Marketing') return e.label;
          return (e.map[nm] !== undefined && e.map[nm] !== '') ? e.map[nm] : '';
        });
        const r = push(pad(row));
        if (String(e.label).trim() === 'TỔNG TEAM') { mark.tong.push(r); mark.pctColA.push([r, 1]); }
        else mktCount++;
      });
      if (mktCount > 0) mark.pctColA.push([cStart, mktCount]);
      push(pad(['']));
      return;
    }
    const lhdM = lhdMktY[m] || {};
    const mkObj = mktY[m] || {};
    const names = Object.keys(mkObj).sort(function(a, b){ return mkObj[b].ct - mkObj[a].ct; });
    names.forEach(function(mk){
      const key = (typeof chuanHoaTxt === 'function') ? chuanHoaTxt(mk) : String(mk).toUpperCase();
      push(pad(rowTinh(mk, mkObj[mk], lhdM[key] || 0)));
    });
    if (names.length) mark.pctColA.push([cStart, names.length]);
    const tongR = push(pad(rowTinh('TỔNG TEAM', teamY[m] || DASH_B(), lhdTeamY[m] || 0)));
    mark.tong.push(tongR);
    mark.pctColA.push([tongR, 1]);
    push(pad(['']));
  });

  writeAndFormat(sh, out, nCol, mark, 'A', null, pctIdx + 1);   // pctIdx+1 = số cột (1-based) của cột %
}

function buildDashB(ss, year, teamY, dayY, lhdTeamY, tinhCaCu) {
  const METS = DASH_METS();
  const nDays = 31;
  const monthsSet = {};
  Object.keys(teamY).forEach(function(m){ monthsSet[m] = 1; });
  Object.keys(dayY).forEach(function(m){ monthsSet[m] = 1; });
  const months = Object.keys(monthsSet).map(Number).sort(function(a, b){ return a - b; });
  const nCol = 2 + nDays;
  const dayHdr = []; for (let d = 1; d <= nDays; d++) dayHdr.push(d);

  const name = 'DASHBOARD B - ' + year;
  let sh = ss.getSheetByName(name);
  const oldKPI = _docKPICu(sh);                    // GIỮ KPI điền tay (đọc trước khi xoá)
  const frozenTong = (!tinhCaCu && sh) ? _docTongCuDash(sh) : {};   // ĐÓNG BĂNG T4–T6 (đọc trước khi xoá)
  if (!sh) sh = ss.insertSheet(name); sh.clear();
  const SEP = _dashSep(sh);                        // dấu phân cách hàm theo ngôn ngữ bảng ( , hoặc ; )
  const out = [];
  const mark = { title: [], hdr: [], colHead: [], pctRow: [], lblCol: [], mHdr: [], tong: [], pctColA: [], sec: [] };
  function push(row){ out.push(row); return out.length; }
  function pad(a){ while (a.length < nCol) a.push(''); return a; }

  mark.title.push(push(pad(['📊 DASHBOARD B — TEAM ADS SG — NĂM ' + year + '  (khối tổng theo tháng + diễn biến theo ngày, cả team)'])));
  push(pad(['']));
  dashKhoiTong(teamY, months, mark, push, pad, year, lhdTeamY, oldKPI, SEP, tinhCaCu, frozenTong);

  // KHỐI 2B: mỗi tháng 1 khối, hàng = 12 chỉ số, cột = TỔNG THÁNG + ngày 1..31 (cả team)
  months.forEach(function(m){
    const dObj = dayY[m] || {};
    mark.mHdr.push(push(pad(['THÁNG ' + m + ' - ' + year])));
    mark.colHead.push(push(['Chỉ số', 'TỔNG THÁNG'].concat(dayHdr)));
    METS.forEach(function(mt){
      const monthTotal = mt[1](teamY[m] || DASH_B());
      const days = []; for (let d = 1; d <= nDays; d++) days.push(mt[1](dObj[d] || DASH_B()));
      const r = push(['' + mt[0], monthTotal].concat(days));
      if (mt[2] === 'pct') mark.pctRow.push(r);
    });
    push(pad(['']));
  });

  writeAndFormat(sh, out, nCol, mark, 'B', months.length);
}

function writeAndFormat(sh, out, nCol, mark, kind, monthsCount, pctColNum) {
  const nRow = out.length;
  const pctCol = pctColNum || nCol;   // cột định dạng % của Dashboard A (mặc định cột cuối)
  sh.getRange(1, 1, nRow, nCol).setValues(out);
  (mark.formulas || []).forEach(function(x){ sh.getRange(x.r, x.c).setFormula(x.f); });   // công thức sống (Tỉ lệ hoàn thành KPI)
  sh.getRange(1, 1, nRow, nCol).setFontFamily('Arial').setVerticalAlignment('middle');
  sh.getRange(1, 2, nRow, nCol - 1).setNumberFormat('#,##0');

  mark.title.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#16233f').setFontColor('#fff').setFontWeight('bold').setFontSize(13); sh.setRowHeight(r, 38); });
  mark.hdr.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#3a5a99').setFontColor('#fff').setFontWeight('bold'); });
  mark.mHdr.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#2f4a7a').setFontColor('#fff').setFontWeight('bold'); });
  (mark.sec || []).forEach(function(r){
    sh.getRange(r, 1, 1, nCol).setBackground('#ffd966').setFontColor('#16233f').setFontWeight('bold');
    sh.getRange(r, 1, 1, 1).setBorder(null, true, null, null, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID_THICK);
  });
  mark.colHead.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#eef2f8').setFontWeight('bold'); sh.getRange(r, 2, 1, nCol - 1).setHorizontalAlignment('center'); });
  (mark.lblCol || []).forEach(function(p){ sh.getRange(p[0], 1, p[1], 1).setBackground('#eef2f8').setFontWeight('bold'); });
  mark.tong.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setFontWeight('bold').setBackground('#fff7e6'); });
  mark.pctRow.forEach(function(r){ sh.getRange(r, 2, 1, nCol - 1).setNumberFormat('0.0%'); });
  (mark.pctColA || []).forEach(function(p){ sh.getRange(p[0], pctCol, p[1], 1).setNumberFormat('0.0%'); });
  if (mark.kpiRow) sh.getRange(mark.kpiRow, 2, 1, nCol - 1).setBackground('#fff3d6');   // dòng KPI điền tay: nền vàng nhạt

  sh.getRange(1, 1, nRow, 1).setFontWeight('bold');
  sh.getRange(1, 1, nRow, nCol).setBorder(true, true, true, true, true, true, '#d9dee7', SpreadsheetApp.BorderStyle.SOLID);

  // Giãn dòng cho thoáng
  sh.setRowHeights(1, nRow, 26);
  mark.title.forEach(function(r){ sh.setRowHeight(r, 40); });
  mark.hdr.forEach(function(r){ sh.setRowHeight(r, 32); });
  mark.mHdr.forEach(function(r){ sh.setRowHeight(r, 30); });
  mark.colHead.forEach(function(r){ sh.setRowHeight(r, 28); });

  // Giãn cột cho dễ nhìn
  if (kind === 'B') {
    sh.setColumnWidth(1, 175); sh.setColumnWidth(2, 140);         // Chỉ số + TỔNG THÁNG
    for (let c = 3; c <= nCol; c++) sh.setColumnWidth(c, 95);      // cột ngày (số/ngày)
    const wideEnd = 1 + (monthsCount || 0);                        // các cột THÁNG của Khối 1 (số tỷ)
    for (let c = 3; c <= wideEnd; c++) sh.setColumnWidth(c, 132);
    sh.setFrozenColumns(2);
  } else {
    sh.setColumnWidth(1, 200);
    for (let c = 2; c <= nCol; c++) sh.setColumnWidth(c, 124);
    sh.setFrozenColumns(1);
  }
  sh.setFrozenRows(1);
}

// Chạy tay để xem thử
function testDashboard() { dungDashboard(); }