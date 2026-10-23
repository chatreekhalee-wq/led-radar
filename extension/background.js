// LED Radar extension
//   กดไอคอน           = เก็บหน้าที่เปิดอยู่ (เฉพาะ led.go.th)
//   โหมดเก็บอัตโนมัติ = เก็บทุกหน้าของ led.go.th ที่ "คุณเปิดเอง" ทันทีที่โหลดเสร็จ
// ไม่เปิดหน้าอื่นเอง ไม่กดเปลี่ยนหน้าเอง ไม่ทำงานเมื่อไม่ได้เปิดเว็บกรมบังคับคดี

const LED_RE = /^https?:\/\/([a-z0-9-]+\.)*led\.go\.th\//i;

function collect() {
  var MAX = 1500000;
  var ds = [document];
  document.querySelectorAll('iframe,frame').forEach(function (f) {
    try { if (f.contentDocument && f.contentDocument.body) ds.push(f.contentDocument); } catch (e) {}
  });
  var tables = [], text = [], links = [], imgs = [], rows = 0;
  var SKIP = /logo|icon|banner|header|footer|qr|sprite|button|btn|cookie|facebook|line\.|youtube|garuda|emblem/i;
  function addImg(src) {
    if (!src || imgs.length >= 40 || SKIP.test(src) || imgs.indexOf(src) >= 0) return;
    if (/^data:/.test(src)) return;
    imgs.push(src);
  }
  ds.forEach(function (d) {
    d.querySelectorAll('table').forEach(function (t) {
      if (!t.parentElement || !t.parentElement.closest('table')) {
        if ((t.innerText || '').trim().length > 20) {
          tables.push(t.outerHTML);
          rows += t.querySelectorAll('tr').length;
        }
      }
    });
    text.push(d.body ? d.body.innerText : '');
    d.querySelectorAll('a[href]').forEach(function (a) {
      if (links.length < 3000) links.push({ text: (a.innerText || '').trim().slice(0, 200), href: a.href });
      if (/\.(jpe?g|png|webp|gif)(\?|$)/i.test(a.href)) addImg(a.href);     // ลิงก์ไปภาพขนาดเต็ม
    });
    d.querySelectorAll('img').forEach(function (im) {
      var src = im.currentSrc || im.src || im.getAttribute('data-src');
      var w = im.naturalWidth || im.width;
      if (w >= 160 || /\.(jpe?g|webp)(\?|$)/i.test(src || '')) addImg(src);
    });
  });
  var pageText = text.join('\n\n').slice(0, MAX);
  var kind = /ราคาประเมิน/.test(pageText) && /นัดที่\s*1/.test(pageText) && tables.length < 8 ? 'detail'
           : (tables.length ? 'list' : 'unknown');
  return {
    p_url: location.href, p_title: document.title, p_kind: kind,
    p_tables_html: tables.join('\n<!--table-->\n').slice(0, MAX),
    p_page_text: pageText, p_links: links, p_images: imgs,
    _rows: rows, _imgs: imgs.length
  };
}

function toast(msg, ok) {
  var old = document.getElementById('__ledradar_toast'); if (old) old.remove();
  var d = document.createElement('div');
  d.id = '__ledradar_toast';
  d.textContent = msg;
  d.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:380px;padding:10px 14px;' +
    'border-radius:10px;font:13px/1.5 Tahoma,sans-serif;color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.25);background:' +
    (ok ? '#2f7d6d' : '#b4452f');
  document.body.appendChild(d);
  setTimeout(function () { d.remove(); }, 5000);
}

async function show(tabId, msg, ok) {
  try { await chrome.scripting.executeScript({ target: { tabId }, func: toast, args: [msg, ok] }); } catch (e) {}
}

function hash(s) { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return String(h); }

async function seenBefore(h) {
  const { seen = [] } = await chrome.storage.session.get('seen');
  if (seen.includes(h)) return true;
  seen.push(h); if (seen.length > 500) seen.shift();
  await chrome.storage.session.set({ seen });
  return false;
}

async function capture(tab, auto) {
  const cfg = await chrome.storage.local.get(['url', 'key', 'token']);
  if (!cfg.url || !cfg.key || !cfg.token) { if (!auto) chrome.runtime.openOptionsPage(); return; }
  if (!LED_RE.test(tab.url || '')) {
    if (!auto) await show(tab.id, 'ใช้ได้เฉพาะหน้าเว็บกรมบังคับคดี (led.go.th)', false);
    return;
  }
  let payload;
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: collect });
    payload = res.result;
  } catch (e) {
    if (!auto) await show(tab.id, 'อ่านหน้านี้ไม่ได้: ' + e.message, false);
    return;
  }
  if (auto && payload.p_kind === 'unknown') return;                 // หน้าทั่วไป ไม่ใช่ประกาศ
  const h = hash(payload.p_tables_html + payload.p_page_text.slice(0, 20000));
  if (auto && await seenBefore(h)) return;                           // หน้าเดิม ไม่ส่งซ้ำ
  const { _rows: rows, _imgs: nimg } = payload; delete payload._rows; delete payload._imgs;
  try {
    const r = await fetch(cfg.url.replace(/\/$/, '') + '/rest/v1/rpc/submit_capture', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json', apikey: cfg.key },
        cfg.key.startsWith('eyJ') ? { Authorization: 'Bearer ' + cfg.key } : {}),
      body: JSON.stringify({ p_token: cfg.token, ...payload })
    });
    const body = await r.text();
    if (!r.ok) {
      const hint = /invalid capture token/.test(body) ? ' — capture_token ไม่ตรง ตรวจในหน้าตั้งค่า'
                 : r.status === 401 ? ' — anon key ไม่ถูกต้อง' : '';
      throw new Error(r.status + hint);
    }
    const what = payload.p_kind === 'detail' ? `หน้ารายละเอียด${nimg ? ` + รูป ${nimg} ภาพ` : ''}` : `~${Math.max(0, rows - 2)} แถว`;
    await show(tab.id, `${auto ? 'เก็บอัตโนมัติ' : 'เก็บแล้ว'} ✓ ${what} (#${body})`, true);
    const { total = 0 } = await chrome.storage.local.get('total');
    await chrome.storage.local.set({ total: total + 1 });
  } catch (e) {
    await show(tab.id, 'ส่งไม่สำเร็จ: ' + e.message, false);
  }
}

async function refreshBadge() {
  const { auto } = await chrome.storage.local.get('auto');
  await chrome.action.setBadgeText({ text: auto ? 'AUTO' : '' });
  await chrome.action.setBadgeBackgroundColor({ color: '#2f7d6d' });
}

chrome.action.onClicked.addListener((tab) => capture(tab, false));

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== 'complete' || !LED_RE.test(tab.url || '')) return;
  const { auto } = await chrome.storage.local.get('auto');
  if (!auto) return;
  setTimeout(() => capture(tab, true), 1200);                         // รอตาราง/รูปโหลดครบ
});

chrome.storage.onChanged.addListener((c) => { if (c.auto) refreshBadge(); });
chrome.runtime.onStartup.addListener(refreshBadge);
chrome.runtime.onInstalled.addListener(refreshBadge);
