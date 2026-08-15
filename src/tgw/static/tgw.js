function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

function authHeaders(extra) {
  const h = window.TGW_API_KEY ? {Authorization: 'Bearer ' + window.TGW_API_KEY} : {};
  return extra ? Object.assign(h, extra) : h;
}

// Reflect view state (filters/sort/page-size/etc.) into the URL query
// string via replaceState, so a bookmarked/pasted URL — or the browser's
// own Back button after navigating to a detail page and returning —
// restores the same view (Dave, 2026-07-17: clicking a filter chip,
// opening an item, then hitting Back used to always land back on the
// unfiltered default view).
function syncURLParam(key, value) {
  const u = new URL(window.location);
  if (value) u.searchParams.set(key, value); else u.searchParams.delete(key);
  history.replaceState(null, '', u);
}
function getURLParam(key) {
  return new URLSearchParams(window.location.search).get(key) || '';
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
