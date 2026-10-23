/* LED Radar — ปุ่มเก็บข้อมูล (bookmarklet source)
 * ทำงานเฉพาะตอนผู้ใช้กดเอง บนหน้าที่ผู้ใช้เปิดอยู่ ไม่ไล่เปิดหน้าอื่น ไม่ crawl
 * __CFG__ จะถูกแทนด้วย {url, key, token} จากหน้า install.html
 */
(function () {
  var CFG = __CFG__;
  var MAX_TEXT = 1500000;

  function toast(msg, ok) {
    var d = document.createElement('div');
    d.textContent = msg;
    d.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:360px;' +
      'padding:12px 16px;border-radius:10px;font:14px/1.5 Tahoma,sans-serif;color:#fff;' +
      'box-shadow:0 6px 24px rgba(0,0,0,.25);background:' + (ok ? '#2f7d6d' : '#b4452f');
    document.body.appendChild(d);
    setTimeout(function () { d.remove(); }, 6000);
  }

  // รวม document หลัก + iframe/frame ที่อยู่โดเมนเดียวกัน (เว็บราชการหลายแห่งยังใช้ frame)
  function docs() {
    var out = [document];
    var frames = document.querySelectorAll('iframe,frame');
    for (var i = 0; i < frames.length; i++) {
      try { if (frames[i].contentDocument && frames[i].contentDocument.body) out.push(frames[i].contentDocument); }
      catch (e) { /* ต่างโดเมน ข้าม */ }
    }
    return out;
  }

  var ds = docs(), tables = [], text = [], links = [], imgs = [];
  ds.forEach(function (d) {
    d.querySelectorAll('table').forEach(function (t) {
      // เก็บเฉพาะตารางชั้นนอกสุดที่มีข้อความ เพื่อไม่ซ้ำตารางซ้อน
      if (!t.parentElement || !t.parentElement.closest('table')) {
        if ((t.innerText || '').trim().length > 20) tables.push(t.outerHTML);
      }
    });
    text.push(d.body ? d.body.innerText : '');
    d.querySelectorAll('a[href]').forEach(function (a) {
      if (links.length < 3000) links.push({ text: (a.innerText || '').trim().slice(0, 200), href: a.href });
    });
    d.querySelectorAll('img[src]').forEach(function (im) {
      if (imgs.length < 60 && im.naturalWidth > 120) imgs.push(im.src);
    });
  });

  var pageText = text.join('\n\n').slice(0, MAX_TEXT);
  var tablesHtml = tables.join('\n<!--table-->\n').slice(0, MAX_TEXT);
  var kind = /ราคาประเมิน/.test(pageText) && /นัดที่\s*1/.test(pageText) && tables.length < 8 ? 'detail'
           : (tables.length ? 'list' : 'unknown');

  var payload = {
    p_token: CFG.token, p_url: location.href, p_title: document.title, p_kind: kind,
    p_tables_html: tablesHtml, p_page_text: pageText, p_links: links, p_images: imgs
  };

  function downloadFallback(reason) {
    var blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'led-capture-' + Date.now() + '.json';
    document.body.appendChild(a); a.click(); a.remove();
    toast('ส่งตรงไม่ได้ (' + reason + ') — ดาวน์โหลดไฟล์ไว้แล้ว นำเข้าทีหลังได้', false);
  }

  try {
    fetch(CFG.url.replace(/\/$/, '') + '/rest/v1/rpc/submit_capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', apikey: CFG.key, Authorization: 'Bearer ' + CFG.key },
      body: JSON.stringify(payload)
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(r.status + ' ' + t.slice(0, 120)); });
      return r.json();
    }).then(function (id) {
      toast('เก็บแล้ว ✓ ' + tables.length + ' ตาราง (#' + id + ') — ระบบจะคิดคะแนนรอบถัดไป', true);
    }).catch(function (e) { downloadFallback(String(e.message || e)); });
  } catch (e) { downloadFallback('CSP'); }
})();
