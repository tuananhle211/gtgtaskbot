// APM Hộp chat CSKH (0102): link a company Zalo nick by QR (unofficial library zca-js 2.2.0).
// Ported from hop-chat-apexmed/zalo-bridge/bridge.mjs (10/11/2026).
//
// * ONLY LISTENS: no function that sends a Zalo message is ever called.
// * Pushes every message / status to APM (PUSH_URL = http://api:8000/api/cskh/zalo/bridge)
//   in Salework's shape {type, zaloAccountId, source:"zca", data:{msgs|groupMsgs|status|info}},
//   with the header x-bridge-secret.
// * APM asks it (same header): GET /health, GET /accounts, POST /qr/start,
//   GET /qr/status?session=, POST /logout. Reached only inside the compose network.
// * A nick's session (Zalo Web cookie) is kept ENCRYPTED on the volume
//   (AES-256-GCM, key derived from BRIDGE_SECRET): SESS_DIR/<uid>.enc.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { createCipheriv, createDecipheriv, createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { Zalo, LoginQRCallbackEventType as QE, ThreadType } from "zca-js";

const PORT = Number(process.env.BRIDGE_PORT || 8091);
const HOST = process.env.BRIDGE_HOST || "0.0.0.0";
const SECRET = process.env.BRIDGE_SECRET || "";
const PUSH = process.env.PUSH_URL || "";
const DIR = process.env.SESS_DIR || path.resolve("sessions");
const HIST_PAGES = Number(process.env.HIST_PAGES || 30);

const log = (...a) => console.log(new Date().toISOString(), ...a);

if (!SECRET || !PUSH) {
  // No secret configured on this server: stay up doing nothing (no restart loop).
  log("zalo-bridge: chua co ZALO_BRIDGE_SECRET / PUSH_URL - khong chay");
  setInterval(() => {}, 1 << 30);
} else {
  start();
}

function start() {
  fs.mkdirSync(DIR, { recursive: true, mode: 0o700 });
  const KEY = createHash("sha256").update("apm-zalo-session:" + SECRET).digest();
  const accounts = new Map(); // id -> {api, id, name, phone, avatar, connected, reason, since}
  const qrs = new Map(); // session -> {state, image, name, avatar, accountId, error, ts}

  const seal = (obj) => {
    const iv = randomBytes(12);
    const c = createCipheriv("aes-256-gcm", KEY, iv);
    const body = Buffer.concat([c.update(JSON.stringify(obj), "utf8"), c.final()]);
    return Buffer.concat([iv, c.getAuthTag(), body]).toString("base64");
  };
  const open = (text) => {
    const raw = Buffer.from(text, "base64");
    const d = createDecipheriv("aes-256-gcm", KEY, raw.subarray(0, 12));
    d.setAuthTag(raw.subarray(12, 28));
    return JSON.parse(Buffer.concat([d.update(raw.subarray(28)), d.final()]).toString("utf8"));
  };
  const safeId = (id) => String(id).replace(/[^0-9A-Za-z_-]/g, "");

  async function push(accId, data, type = "Notification") {
    try {
      await fetch(PUSH, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-bridge-secret": SECRET },
        body: JSON.stringify({ type, zaloAccountId: accId, source: "zca", data }),
      });
    } catch (e) {
      log("Day tin ve APM loi:", e.message);
    }
  }

  // Salework's shape: uidFrom "0" = the logged-in nick wrote; idTo = the receiver / group.
  function toMsg(m, accId) {
    const d = m.data || {};
    const group = m.type === ThreadType.Group;
    return {
      msgId: String(d.msgId || d.cliMsgId || ""),
      cliMsgId: d.cliMsgId,
      msgType: d.msgType,
      uidFrom: m.isSelf ? "0" : String(d.uidFrom || m.threadId || ""),
      idTo: group ? String(m.threadId) : m.isSelf ? String(m.threadId) : accId,
      dName: d.dName || "",
      content: d.content,
      ts: String(d.ts || Date.now()),
    };
  }

  // The other person's name + avatar (cached per nick) - reads the profile only.
  async function peerInfo(st, uid) {
    if (!uid || uid === "0") return null;
    st.peers ??= new Map();
    if (st.peers.has(uid)) return st.peers.get(uid);
    let info = null;
    try {
      const r = await st.api.getUserInfo(uid);
      const p = r?.changed_profiles?.[uid] || Object.values(r?.changed_profiles || {})[0];
      if (p) info = { name: p.displayName || p.zaloName || "", avatar: p.avatar || "" };
    } catch (e) {
      log("Khong lay duoc ho so", uid, e.message);
    }
    st.peers.set(uid, info);
    return info;
  }

  function closeReason(code, reason) {
    if (code === 3000) return "Nick đang mở Zalo Web ở nơi khác (trùng kết nối) – đóng Zalo Web rồi quét lại";
    if (code === 3003) return "Zalo đã ngắt kết nối phiên này – cần quét QR lại";
    if (code === 1000) return "Đã dừng";
    return `Mất kết nối (${code}${reason ? " " + reason : ""})`;
  }

  async function attach(api, creds) {
    const id = String(api.getOwnId());
    let name = "", phone = "", avatar = "";
    try {
      const info = await api.fetchAccountInfo();
      const p = info?.profile || info || {};
      name = p.displayName || p.zaloName || "";
      phone = p.phoneNumber || "";
      avatar = p.avatar || "";
    } catch (e) {
      log("Khong lay duoc thong tin nick", id, e.message);
    }
    if (creds) fs.writeFileSync(path.join(DIR, safeId(id) + ".enc"), seal(creds), { mode: 0o600 });
    const old = accounts.get(id);
    if (old?.api) {
      try { old.api.listener.stop(); } catch {}
    }
    const st = { api, id, name, phone, avatar, connected: false, reason: "Đang kết nối…", since: Date.now() };
    accounts.set(id, st);
    const L = api.listener;
    L.on("connected", () => {
      st.connected = true;
      st.reason = "";
      log("Nick", id, "da ket noi");
      push(id, { status: "connected", info: { displayName: name, phoneNumber: phone, avatar } }, "Status");
      if (!st.hist) {
        // First connection: ask for recent 1-1 history (read only).
        st.hist = { pages: 0, total: 0, minId: null };
        setTimeout(() => {
          try { L.requestOldMessages(ThreadType.User, null); } catch (e) { log("Khong keo duoc lich su", e.message); }
        }, 1500);
      }
    });
    L.on("old_messages", async (msgs, type) => {
      if (type !== ThreadType.User || !st.hist) return;
      const list = [];
      for (const m of msgs || []) {
        const o = toMsg(m, id);
        if (!o.msgId) continue;
        const p = await peerInfo(st, String(m.threadId));
        if (p) o.peer = p;
        list.push(o);
      }
      st.hist.pages++;
      st.hist.total += list.length;
      for (let i = 0; i < list.length; i += 100) push(id, { msgs: list.slice(i, i + 100), history: true });
      let min = null;
      for (const o of list) {
        try { const v = BigInt(o.msgId); if (min === null || v < min) min = v; } catch {}
      }
      if (list.length && min !== null && (st.hist.minId === null || min < st.hist.minId) && st.hist.pages < HIST_PAGES) {
        st.hist.minId = min;
        setTimeout(() => { try { L.requestOldMessages(ThreadType.User, String(min)); } catch {} }, 800);
      }
    });
    L.on("closed", (code, reason) => {
      st.connected = false;
      st.reason = closeReason(code, reason);
      log("Nick", id, "dong ket noi", code);
      push(id, { status: "closed", reason: st.reason }, "Status");
    });
    L.on("error", (e) => {
      st.reason = "Lỗi: " + (e?.message || e);
      log("Nick", id, "loi", e?.message || e);
    });
    L.on("message", async (m) => {
      try {
        const o = toMsg(m, id);
        if (m.type !== ThreadType.Group) {
          const p = await peerInfo(st, String(m.threadId));
          if (p) o.peer = p;
        }
        push(id, m.type === ThreadType.Group ? { groupMsgs: [o] } : { msgs: [o] });
      } catch (e) {
        log("Loi doc tin", e.message);
      }
    });
    L.start({ retryOnClose: true });
    return st;
  }

  const newZalo = () => new Zalo({ selfListen: true, checkUpdate: false, logging: false });

  function startQR() {
    const sid = randomUUID();
    const s = { state: "starting", image: "", ts: Date.now() };
    qrs.set(sid, s);
    let creds = null;
    newZalo()
      .loginQR({}, (ev) => {
        switch (ev.type) {
          case QE.QRCodeGenerated: s.state = "qr"; s.image = ev.data?.image || ""; s.ts = Date.now(); break;
          case QE.QRCodeExpired: s.state = "expired"; s.image = ""; try { ev.actions?.abort(); } catch {} break;
          case QE.QRCodeScanned: s.state = "scanned"; s.image = ""; s.name = ev.data?.display_name || ""; s.avatar = ev.data?.avatar || ""; break;
          case QE.QRCodeDeclined: s.state = "declined"; s.image = ""; try { ev.actions?.abort(); } catch {} break;
          case QE.GotLoginInfo: creds = ev.data; break;
        }
      })
      .then(async (api) => {
        if (!api) { if (!["expired", "declined"].includes(s.state)) s.state = "error"; return; }
        const st = await attach(api, creds);
        Object.assign(s, { state: "ok", accountId: st.id, name: st.name, phone: st.phone, avatar: st.avatar, image: "" });
      })
      .catch((e) => {
        if (!["expired", "declined"].includes(s.state)) { s.state = "error"; s.error = String(e?.message || e); }
        log("QR loi:", s.error || s.state);
      });
    return sid;
  }

  async function restoreAll() {
    for (const f of fs.readdirSync(DIR).filter((x) => x.endsWith(".enc"))) {
      const id = f.slice(0, -4);
      try {
        const creds = open(fs.readFileSync(path.join(DIR, f), "utf8"));
        const api = await newZalo().login(creds);
        await attach(api, null);
        log("Dang nhap lai nick", id, "OK");
      } catch (e) {
        accounts.set(id, { id, connected: false, reason: "Phiên đăng nhập hết hạn – cần quét QR lại", since: Date.now() });
        log("Dang nhap lai nick", id, "loi:", e.message);
        push(id, { status: "expired", reason: "Phiên đăng nhập hết hạn – cần quét QR lại" }, "Status");
      }
    }
  }

  function secretOk(req) {
    const a = Buffer.from(String(req.headers["x-bridge-secret"] || ""));
    const b = Buffer.from(SECRET);
    return a.length === b.length && timingSafeEqual(a, b);
  }
  const send = (res, code, obj) => {
    res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(obj));
  };
  const body = (req) =>
    new Promise((ok) => {
      let s = "";
      req.on("data", (c) => { s += c; if (s.length > 100000) req.destroy(); });
      req.on("end", () => { try { ok(JSON.parse(s || "{}")); } catch { ok({}); } });
    });

  http
    .createServer(async (req, res) => {
      const u = new URL(req.url, "http://x");
      if (!secretOk(req)) return send(res, 403, { error: "forbidden" });
      try {
        if (u.pathname === "/health") return send(res, 200, { ok: true, accounts: accounts.size });
        if (u.pathname === "/accounts")
          return send(res, 200, {
            accounts: [...accounts.values()].map(({ id, name, phone, avatar, connected, reason, since }) => ({
              id, name, phone, avatar, connected, reason, since,
            })),
          });
        if (u.pathname === "/qr/start" && req.method === "POST") {
          await body(req);
          const sid = startQR();
          for (let i = 0; i < 40 && qrs.get(sid).state === "starting"; i++) await new Promise((r) => setTimeout(r, 250));
          const s = qrs.get(sid);
          return send(res, 200, { session: sid, state: s.state, image: s.image, error: s.error });
        }
        if (u.pathname === "/qr/status") {
          const s = qrs.get(u.searchParams.get("session") || "");
          if (!s) return send(res, 404, { error: "Phiên QR không còn, bấm quét lại" });
          const { state, image, name, phone, avatar, accountId, error } = s;
          return send(res, 200, { state, image, name, phone, avatar, accountId, error });
        }
        if (u.pathname === "/logout" && req.method === "POST") {
          const { id } = await body(req);
          const st = accounts.get(String(id));
          if (st?.api) { try { st.api.listener.stop(); } catch {} }
          accounts.delete(String(id));
          try { fs.unlinkSync(path.join(DIR, safeId(id) + ".enc")); } catch {}
          log("Ngat ket noi nick", id);
          return send(res, 200, { ok: true });
        }
        send(res, 404, { error: "not found" });
      } catch (e) {
        send(res, 500, { error: String(e?.message || e) });
      }
    })
    .listen(PORT, HOST, () => {
      log("Zalo bridge chay tai", HOST + ":" + PORT);
      restoreAll();
    });

  setInterval(() => {
    const t = Date.now() - 10 * 60e3;
    for (const [k, v] of qrs) if (v.ts < t) qrs.delete(k);
  }, 60e3);
}
