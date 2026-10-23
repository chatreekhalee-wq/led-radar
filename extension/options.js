const $ = (id) => document.getElementById(id);
chrome.storage.local.get(['url', 'key', 'token', 'auto', 'total']).then((c) => {
  $('url').value = c.url || ''; $('key').value = c.key || ''; $('token').value = c.token || '';
  $('auto').checked = !!c.auto; $('total').textContent = c.total ? `เก็บไปแล้ว ${c.total} หน้า` : '';
});
$('save').onclick = async () => {
  let url = $('url').value.trim(), key = $('key').value.trim();
  if (/^(eyJ|sb_)/.test(url) && key.startsWith('https://')) [url, key] = [key, url];  // สลับให้ถ้าใส่ผิดช่อง
  const token = $('token').value.trim();
  if (!/^https:\/\/.+\.supabase\.co\/?$/.test(url)) { $('msg').textContent = 'URL ต้องเป็น https://....supabase.co'; return; }
  if (!/^(eyJ|sb_publishable_)/.test(key) || !token) { $('msg').textContent = 'กรอกให้ครบทั้ง 3 ช่อง'; return; }
  await chrome.storage.local.set({ url, key, token, auto: $('auto').checked });
  $('url').value = url; $('key').value = key;
  $('msg').textContent = 'บันทึกแล้ว ✓';
};
