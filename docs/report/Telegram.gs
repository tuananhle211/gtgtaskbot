// ===== GỬI BÁO CÁO KHUNG GIỜ VÀO TELEGRAM (dạng PDF) =====
// B1: Nhắn @BotFather -> /newbot -> lấy BOT TOKEN, dán vào dưới
// B2: Tạo group, thêm bot vào, nhắn 1 tin bất kỳ trong group
// B3: Chạy hàm xemChatId() -> xem Log lấy số "id" (group là số ÂM) -> dán vào TELE_CHAT_ID
const TELE_TOKEN   = '8869223989:AAGgzCX9ynGwBhr2gO7_sgwfd1ef3d64ewk';
const TELE_CHAT_ID = '-4534878279';

const TELE_SHEET = 'BÁO CÁO KHUNG GIỜ';

// Gửi PDF bảng khung giờ vào Telegram (tự bỏ qua nếu chưa cấu hình)
function guiTelegram() {
  if (String(TELE_TOKEN).indexOf('DÁN') >= 0 || String(TELE_CHAT_ID).indexOf('DÁN') >= 0 || !TELE_CHAT_ID) return;
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(TELE_SHEET);
  if (!sh) return;
  SpreadsheetApp.flush();

  const url = 'https://docs.google.com/spreadsheets/d/' + ss.getId() + '/export?format=pdf' +
    '&gid=' + sh.getSheetId() +
    '&portrait=false&fitw=true&scale=4&gridlines=false&sheetnames=false&printtitle=false&fzr=false' +
    '&top_margin=0.25&bottom_margin=0.25&left_margin=0.25&right_margin=0.25';
  let blob;
  try {
    blob = UrlFetchApp.fetch(url, { headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }, muteHttpExceptions: true })
      .getBlob().setName('BaoCao_KhungGio.pdf');
  } catch (e) { return; }

  const now = Utilities.formatDate(new Date(), 'Asia/Ho_Chi_Minh', 'HH:mm - dd/MM/yyyy');
  UrlFetchApp.fetch('https://api.telegram.org/bot' + TELE_TOKEN + '/sendDocument', {
    method: 'post',
    payload: { chat_id: String(TELE_CHAT_ID), caption: '📊 BÁO CÁO ADS SG • ' + now, document: blob },
    muteHttpExceptions: true
  });
}

// Chạy tay để test gửi thử ngay
function testGuiTelegram() { guiTelegram(); }

// Chạy để lấy CHAT ID: mở Log (Ctrl+Enter) tìm "chat":{"id": ...}
function xemChatId() {
  const res = UrlFetchApp.fetch('https://api.telegram.org/bot' + TELE_TOKEN + '/getUpdates', { muteHttpExceptions: true });
  Logger.log(res.getContentText());
}