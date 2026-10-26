// ===== MẪU GIAO DIỆN "KHỐI TỔNG NĂM" (chỉ xem bố cục, chưa gắn dữ liệu) =====
// Chạy hàm mauKhoiTong() -> tạo sheet "MẪU - KHỐI TỔNG" để Sếp duyệt thứ tự chỉ số
function mauKhoiTong() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const name = 'MẪU - KHỐI TỔNG';
  let sh = ss.getSheetByName(name); if (!sh) sh = ss.insertSheet(name); sh.clear();

  const months = ['Tháng 7', 'Tháng 8', 'Tháng 9', 'Tháng 10', 'Tháng 11', 'Tháng 12'];
  const nCol = 1 + months.length;

  // Sắp xếp theo PHỄU: chi phí -> lead -> khách -> doanh thu -> hiệu quả -> cơ cấu
  // [type] money | count | pct  (chỉ để định dạng ô, dữ liệu gắn sau)
  const SECTIONS = [
    ['① CHI PHÍ QUẢNG CÁO', [
      ['CHI PHÍ ADS', 'money']
    ]],
    ['② LEAD (SỐ ĐIỆN THOẠI)', [
      ['SĐT', 'count'],
      ['CHI PHÍ SĐT', 'money']
    ]],
    ['③ KHÁCH ĐẾN CỬA & CHẤT LƯỢNG CHỐT', [
      ['KHÁCH ĐẾN CỬA', 'count'],
      ['TỶ LỆ CHỐT', 'pct'],
      ['TỈ TRỌNG KHÁCH NÓNG', 'pct'],
      ['TỶ TRỌNG KHÁCH GỐI', 'pct'],
      ['TỈ TRỌNG KHÁCH TỐI ƯU', 'pct'],
      ['TỶ LỆ CHỐT NÓNG', 'pct'],
      ['TỶ LỆ BONG', 'pct']
    ]],
    ['④ DOANH THU', [
      ['DOANH THU', 'money'],
      ['CƠ SỞ HCM', 'money'],
      ['HN CHUYỂN HCM', 'money'],
      ['BILL TRUNG BÌNH', 'money']
    ]],
    ['⑤ HIỆU QUẢ', [
      ['ADS / DOANH THU', 'pct']
    ]]
  ];

  const out = []; const mark = { title: [], colHead: [], sec: [], money: [], pct: [], lbl: [] };
  function push(row){ while (row.length < nCol) row.push(''); out.push(row); return out.length; }

  mark.title.push(push(['', '📊 KHỐI TỔNG NĂM (MẪU BỐ CỤC) — sắp theo phễu kinh doanh']));
  push(['']);
  mark.colHead.push(push(['CHỈ SỐ'].concat(months)));

  SECTIONS.forEach(function(s){
    mark.sec.push(push([s[0]]));
    s[1].forEach(function(m){
      const r = push([m[0]]);
      mark.lbl.push(r);
      if (m[1] === 'pct') mark.pct.push(r); else mark.money.push(r);
    });
  });

  const nRow = out.length;
  sh.getRange(1, 1, nRow, nCol).setValues(out);
  sh.getRange(1, 1, nRow, nCol).setFontFamily('Arial').setVerticalAlignment('middle');
  // Lưới nền mảnh (đặt TRƯỚC để không đè mất dải vàng)
  sh.getRange(1, 1, nRow, nCol).setBorder(true, true, true, true, true, true, '#e3e8f0', SpreadsheetApp.BorderStyle.SOLID);

  // Sọc ngựa vằn nhẹ cho hàng chỉ số
  mark.lbl.forEach(function(r, i){
    sh.getRange(r, 1, 1, nCol).setBackground(i % 2 === 0 ? '#ffffff' : '#f5f8fc');
    sh.getRange(r, 1, 1, 1).setFontWeight('bold').setFontColor('#1f2a44');
    sh.setRowHeight(r, 26);
  });
  // Tiêu đề nhóm: nền xanh nhạt + dải vàng thương hiệu bên trái
  mark.sec.forEach(function(r){
    sh.getRange(r, 1, 1, nCol).setBackground('#ffd966').setFontColor('#16233f').setFontWeight('bold').setFontSize(11);
    sh.getRange(r, 1, 1, 1).setBorder(null, true, null, null, null, null, '#16233f', SpreadsheetApp.BorderStyle.SOLID_THICK);
    sh.setRowHeight(r, 30);
  });
  // Cột tiêu đề tháng
  mark.colHead.forEach(function(r){ sh.getRange(r, 1, 1, nCol).setBackground('#3a5a99').setFontColor('#fff').setFontWeight('bold').setHorizontalAlignment('center'); sh.setRowHeight(r, 30); });
  // Tiêu đề chính + vạch vàng dưới chân
  mark.title.forEach(function(r){
    sh.getRange(r, 1, 1, nCol).setBackground('#16233f');
    sh.getRange(r, 2, 1, nCol - 1).merge().setFontColor('#fff').setFontWeight('bold').setFontSize(13).setHorizontalAlignment('center');
    sh.getRange(r, 1, 1, nCol).setBorder(null, null, true, null, null, null, '#f4b400', SpreadsheetApp.BorderStyle.SOLID_THICK);
    sh.setRowHeight(r, 42);
  });
  mark.money.forEach(function(r){ sh.getRange(r, 2, 1, nCol - 1).setNumberFormat('#,##0'); });
  mark.pct.forEach(function(r){ sh.getRange(r, 2, 1, nCol - 1).setNumberFormat('0.0%'); });

  sh.setColumnWidth(1, 220);
  for (let c = 2; c <= nCol; c++) sh.setColumnWidth(c, 120);
  sh.setFrozenColumns(1); sh.setFrozenRows(1);
  ss.toast('Đã tạo sheet MẪU - KHỐI TỔNG', 'OK', 5);
}