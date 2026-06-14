function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

function authHeaders(extra) {
  const h = {Authorization: 'Bearer ' + (window.TGW_API_KEY || '')};
  return extra ? Object.assign(h, extra) : h;
}

function initChips(containerSel, onSelect) {
  const el = typeof containerSel === 'string'
    ? document.querySelector(containerSel)
    : containerSel;
  if (!el) return;
  el.querySelectorAll('.chip').forEach(c => {
    c.addEventListener('click', () => {
      el.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
      c.classList.add('active');
      if (onSelect) onSelect(c);
    });
  });
}
