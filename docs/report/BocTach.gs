// ===== BÓC TÁCH THEO MARKETING =====
// Đọc DANH_SACH_MKT + DATA_TONG -> tạo tab TONG_THEO_MKT
// (Muốn bóc số hôm qua: đổi 'DATA_TONG' ở dưới thành 'DATA_HOMQUA')
function bocTachTheoMKT() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // 1) Danh sách marketing
  const ds = ss.getSheetByName('DANH_SACH_MKT');
  if (!ds) throw new Error('Chưa có tab DANH_SACH_MKT');
  const dsRows = ds.getDataRange().getValues();
  const mktList = [];
  for (let i = 1; i < dsRows.length; i++) {
    const ten = String(dsRows[i][0] || '').trim();
    if (!ten) continue;
    const keys = String(dsRows[i][1] || '').split(',').map(function(k){ return chuanHoa(k); }).filter(Boolean);
    mktList.push({ ten: ten, keys: keys });
  }

  // 2) Các nguồn cần tách (dài đứng trước để ưu tiên đúng)
  const NGUON = ['APEXMED','COREMIND','GPI','GTG','XH','APEX','TINA'];

  // 3) Đọc data nguồn
  const src = ss.getSheetByName('DATA_TONG');
  if (!src) throw new Error('Chưa có tab DATA_TONG');
  const data = src.getDataRange().getValues();

  // 4) Gộp theo tài khoản (theo ID)
  const acc = {};
  for (let i = 1; i < data.length; i++) {
    const id = String(data[i][1] || '').trim();
    if (!id) continue;
    if (!acc[id]) acc[id] = { ten: data[i][0], id: id, chiTieu: 0, soMess: 0, luotMua: 0 };
    acc[id].chiTieu += Number(data[i][2] || 0);
    acc[id].soMess  += Number(data[i][3] || 0);
    acc[id].luotMua += Number(data[i][5] || 0);
  }

  // 5) Gán marketer + nguồn
  const detail = [];
  Object.keys(acc).forEach(function(id){
    const a = acc[id];
    const ch = chuanHoa(a.ten);
    let matched = [];
    mktList.forEach(function(m){
      if (m.keys.some(function(k){ return ch.indexOf(k) >= 0; })) matched.push(m.ten);
    });
    const marketer = matched.length === 0 ? 'Chưa gán'
                   : matched.length === 1 ? matched[0]
                   : '⚠️ Trùng: ' + matched.join(' / ');
    let nguon = 'Khác';
    for (let j = 0; j < NGUON.length; j++) { if (ch.indexOf(chuanHoa(NGUON[j])) >= 0) { nguon = NGUON[j]; break; } }
    const giaMess = a.soMess > 0 ? Math.round(a.chiTieu / a.soMess) : 0;
    const cpMua   = a.luotMua > 0 ? Math.round(a.chiTieu / a.luotMua) : 0;
    detail.push([marketer, nguon, a.ten, id, a.chiTieu, a.soMess, giaMess, a.luotMua, cpMua]);
  });
  detail.sort(function(x, y){
    return (x[0] + '|' + x[1] + '|' + x[2]).localeCompare(y[0] + '|' + y[1] + '|' + y[2]);
  });

  // 6) Tổng theo marketing
  const tong = {};
  detail.forEach(function(d){
    if (!tong[d[0]]) tong[d[0]] = { chiTieu: 0, soMess: 0, luotMua: 0 };
    tong[d[0]].chiTieu += d[4]; tong[d[0]].soMess += d[5]; tong[d[0]].luotMua += d[7];
  });

  // 7) Ghi ra tab TONG_THEO_MKT
  let sh = ss.getSheetByName('TONG_THEO_MKT');
  if (!sh) sh = ss.insertSheet('TONG_THEO_MKT');
  sh.clearContents();

  const out = [];
  out.push(['TỔNG THEO MARKETING','','','','Chi tiêu','Số Mess','Giá Mess','Lượt Mua','CP/Mua']);
  Object.keys(tong).sort().forEach(function(m){
    const t = tong[m];
    out.push([m,'','','', t.chiTieu, t.soMess,
      t.soMess>0?Math.round(t.chiTieu/t.soMess):0, t.luotMua,
      t.luotMua>0?Math.round(t.chiTieu/t.luotMua):0]);
  });
  out.push(['','','','','','','','','']);
  const rowHeaderDetail = out.length + 1;
  out.push(['CHI TIẾT THEO TÀI KHOẢN','Nguồn','Tên tài khoản','ID','Chi tiêu','Số Mess','Giá Mess','Lượt Mua','CP/Mua']);
  detail.forEach(function(d){ out.push(d); });

  sh.getRange(1,4,out.length,1).setNumberFormat('@');       // ID dạng text
  sh.getRange(1,1,out.length,9).setValues(out);
  sh.getRange(1,5,out.length,5).setNumberFormat('#,##0');   // các cột số

  sh.getRange(1,1,1,9).setFontWeight('bold').setBackground('#1f2a44').setFontColor('#ffffff');
  sh.getRange(rowHeaderDetail,1,1,9).setFontWeight('bold').setBackground('#2b3a5e').setFontColor('#ffffff');
  sh.setFrozenRows(1);
  for (let c = 1; c <= 9; c++) sh.autoResizeColumn(c);
  ss.toast('Xong! Mở tab TONG_THEO_MKT', 'OK', 6);
}

// Chuẩn hóa: viết hoa + bỏ dấu tiếng Việt (để dò cho khớp mọi kiểu gõ)
function chuanHoa(s) {
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