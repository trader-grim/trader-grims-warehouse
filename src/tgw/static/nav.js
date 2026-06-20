(function() {
  var nav = document.createElement('nav');
  nav.className = 'tgw-nav';
  nav.innerHTML =
    '<a class="brand" href="/form/home">TGW</a>' +
    '<a href="/form/home">Home</a>' +
    '<a href="/form/intake">Intake</a>' +
    '<a href="/form/items">Inventory</a>' +
    '<a href="/form/needs-review">Blocked <span id="nav-blocked-count" class="nav-count"></span></a>' +
    '<a href="/form/drafts">Drafts <span id="nav-review-count" class="nav-count"></span></a>' +
    '<a href="/form/pipeline">Pipeline</a>' +
    '<a href="/form/system">System</a>' +
    '<a href="/docs">Docs</a>' +
    '<a href="/form/links">Links</a>' +
    '<a href="/form/offers">Offers</a>' +
    '<a href="/form/revisions">Revisions</a>' +
    '<div class="nav-dropdown">' +
      '<button class="nav-dropdown-toggle">Operations &#9662;</button>' +
      '<div class="nav-dropdown-menu">' +
        '<a href="/form/bulk">Bulk Edit</a>' +
        '<a href="/form/suggest">Suggest</a>' +
        '<a href="/form/pm-chat">PM Chat</a>' +
      '</div>' +
    '</div>' +
    '<div class="nav-dropdown">' +
      '<button class="nav-dropdown-toggle">Admin &#9662;</button>' +
      '<div class="nav-dropdown-menu">' +
        '<a href="/form/todos">Todos</a>' +
      '</div>' +
    '</div>';

  document.body.prepend(nav);

  nav.querySelectorAll('.nav-dropdown-toggle').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var menu = btn.nextElementSibling;
      var isOpen = menu.classList.contains('open');
      document.querySelectorAll('.nav-dropdown-menu').forEach(function(m) {
        m.classList.remove('open');
      });
      if (!isOpen) menu.classList.add('open');
    });
  });

  document.addEventListener('click', function() {
    document.querySelectorAll('.nav-dropdown-menu').forEach(function(m) {
      m.classList.remove('open');
    });
  });

  // Suggest popup — intercept the "Suggest" nav link, show an inline overlay instead.
  var _sgOverlay = document.createElement('div');
  _sgOverlay.className = 'sg-overlay';
  _sgOverlay.innerHTML =
    '<div class="sg-box">' +
      '<h3>Add Suggestion</h3>' +
      '<textarea id="sg-text" placeholder="idea, task, note ..."></textarea>' +
      '<div class="sg-btns">' +
        '<button class="sg-submit" id="sg-submit">Add Suggestion</button>' +
        '<button class="sg-cancel" id="sg-cancel">Cancel</button>' +
        '<span class="sg-msg" id="sg-msg"></span>' +
      '</div>' +
    '</div>';
  document.body.appendChild(_sgOverlay);

  function _sgShow() {
    var txt = document.getElementById('sg-text');
    var prefix = '[Context: ' + document.title + ' — ' + window.location.href + ']\n';
    txt.value = prefix;
    document.getElementById('sg-msg').className = 'sg-msg';
    document.getElementById('sg-msg').textContent = '';
    _sgOverlay.classList.add('open');
    setTimeout(function() { txt.focus(); txt.setSelectionRange(txt.value.length, txt.value.length); }, 0);
  }
  function _sgHide() { _sgOverlay.classList.remove('open'); }

  document.getElementById('sg-cancel').addEventListener('click', _sgHide);
  _sgOverlay.addEventListener('click', function(e) { if (e.target === _sgOverlay) _sgHide(); });

  document.getElementById('sg-submit').addEventListener('click', function() {
    var text = (document.getElementById('sg-text').value || '').trim();
    var msg = document.getElementById('sg-msg');
    if (!text) { msg.className = 'sg-msg err'; msg.textContent = 'Nothing to add.'; return; }
    var btn = document.getElementById('sg-submit');
    btn.disabled = true; btn.textContent = 'Adding…';
    fetch('/api/suggest', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: text})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      btn.disabled = false; btn.textContent = 'Add Suggestion';
      if (d.ok) {
        msg.className = 'sg-msg ok'; msg.textContent = 'Added!';
        setTimeout(_sgHide, 1400);
      } else {
        msg.className = 'sg-msg err'; msg.textContent = d.detail || 'Error writing suggestion';
      }
    })
    .catch(function() {
      btn.disabled = false; btn.textContent = 'Add Suggestion';
      msg.className = 'sg-msg err'; msg.textContent = 'Network error';
    });
  });

  document.addEventListener('keydown', function(e) { if (e.key === 'Escape') { _sgHide(); _pmHide(); } });

  var _sgLink = nav.querySelector('a[href="/form/suggest"]');
  if (_sgLink) {
    _sgLink.addEventListener('click', function(e) {
      e.preventDefault();
      document.querySelectorAll('.nav-dropdown-menu').forEach(function(m) { m.classList.remove('open'); });
      _sgShow();
    });
  }

  // ---------------------------------------------------------------------------
  // PM Chat popup — intercept the "PM Chat" nav link, show a modal overlay.
  // ---------------------------------------------------------------------------
  var _pmOverlay = document.createElement('div');
  _pmOverlay.className = 'pm-overlay';
  _pmOverlay.innerHTML =
    '<div class="pm-modal">' +
      '<div class="pm-modal-header">' +
        '<span>PM Chat</span>' +
        '<button class="pm-modal-close" id="pm-modal-close" title="Close">&#10005;</button>' +
      '</div>' +
      '<div class="pm-modal-messages" id="pm-modal-messages"></div>' +
      '<div class="pm-modal-typing" id="pm-modal-typing">PM is thinking…</div>' +
      '<div class="pm-modal-input-row">' +
        '<input id="pm-modal-input" type="text" placeholder="Ask the PM…" autocomplete="off">' +
        '<button class="pm-modal-send" id="pm-modal-send">Send</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(_pmOverlay);

  var PM_HK = 'tgw-pm-h-nav';
  var _pmHistory = [];

  function _pmLoad() {
    try { var h = sessionStorage.getItem(PM_HK); if (h) _pmHistory = JSON.parse(h); } catch(e) {}
    _pmRender();
  }
  function _pmSave() {
    try { sessionStorage.setItem(PM_HK, JSON.stringify(_pmHistory.slice(-20))); } catch(e) {}
  }
  function _pmRender() {
    var el = document.getElementById('pm-modal-messages');
    if (!el) return;
    if (!_pmHistory.length) {
      el.innerHTML = '<div style="color:#444;font-size:.82em;text-align:center;padding:18px 6px">' +
        'Ask: what needs doing? how many dead letters? how many items staged?</div>';
      return;
    }
    var html = '';
    _pmHistory.forEach(function(m) {
      html += '<div class="pm-modal-msg ' + (m.role === 'user' ? 'user' : 'assistant') + '">' +
        escapeHtml(m.content) + '</div>';
    });
    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;
  }

  function _pmShow() {
    _pmLoad();
    _pmOverlay.classList.add('open');
    setTimeout(function() {
      var inp = document.getElementById('pm-modal-input');
      if (inp) inp.focus();
    }, 0);
  }
  function _pmHide() { _pmOverlay.classList.remove('open'); }

  async function _pmSend() {
    var inp = document.getElementById('pm-modal-input');
    var msg = (inp.value || '').trim();
    if (!msg) return;
    inp.value = '';
    _pmHistory.push({role: 'user', content: msg});
    _pmRender(); _pmSave();
    var typingEl = document.getElementById('pm-modal-typing');
    var btn = document.getElementById('pm-modal-send');
    if (typingEl) typingEl.style.display = '';
    if (btn) btn.disabled = true;
    try {
      var r = await fetch('/api/pm/chat', {
        method: 'POST',
        headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
        body: JSON.stringify({message: msg, history: _pmHistory.slice(-9, -1)}),
      });
      var d = await r.json().catch(function() { return {}; });
      var txt = d.message || d.detail || '(no response)';
      _pmHistory.push({role: 'assistant', content: txt});
      _pmRender(); _pmSave();
      if (d.actions) d.actions.forEach(function(a) { if (a.type && a.type !== 'none') _pmToast(a); });
    } catch(e) {
      _pmHistory.push({role: 'assistant', content: 'Error: ' + e.message});
      _pmRender(); _pmSave();
    } finally {
      if (typingEl) typingEl.style.display = 'none';
      if (btn) btn.disabled = false;
      var msgs = document.getElementById('pm-modal-messages');
      if (msgs) msgs.scrollTop = 9999;
    }
  }

  function _pmToast(action) {
    var el = document.createElement('div');
    el.className = 'pm-toast';
    var label = action.type === 'add_todo' ? 'Add Todo' : action.type === 'add_suggestion' ? 'Add Suggestion' : 'Action';
    var body = action.type === 'add_todo'
      ? '[' + escapeHtml(action.agent || '?') + ' p' + (action.priority || 50) + '] ' + escapeHtml(action.body || '')
      : escapeHtml(action.text || '');
    el.innerHTML = '<div class="tlabel">' + label + '</div>' +
      '<div class="tbody">' + body + '</div>' +
      '<div class="tbtns">' +
      '<button class="btn-ok" onclick="_pmConfirm(this)">Confirm</button>' +
      '<button class="btn-no" onclick="this.closest(\'.pm-toast\').remove()">Dismiss</button>' +
      '</div>';
    el.dataset.action = JSON.stringify(action);
    document.body.appendChild(el);
    setTimeout(function() { if (el.parentNode) el.remove(); }, 30000);
  }

  window._pmConfirm = async function(btn) {
    var toast = btn.closest('.pm-toast');
    var action;
    try { action = JSON.parse(toast.dataset.action); } catch(e) { toast.remove(); return; }
    btn.disabled = true; btn.textContent = '…';
    try {
      var r = await fetch('/api/pm/action', {
        method: 'POST',
        headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
        body: JSON.stringify(action),
      });
      var d = await r.json().catch(function() { return {}; });
      if (d.ok) {
        toast.innerHTML = '<div style="color:#7f7;font-size:.85em">' + (d.message || 'Done') + '</div>';
      } else {
        toast.innerHTML = '<div style="color:#f77;font-size:.85em">Error: ' + escapeHtml(d.detail || 'failed') + '</div>';
      }
      setTimeout(function() { if (toast.parentNode) toast.remove(); }, 4000);
    } catch(e) {
      toast.innerHTML = '<div style="color:#f77;font-size:.85em">Network error</div>';
      setTimeout(function() { if (toast.parentNode) toast.remove(); }, 4000);
    }
  };

  document.getElementById('pm-modal-close').addEventListener('click', _pmHide);
  _pmOverlay.addEventListener('click', function(e) { if (e.target === _pmOverlay) _pmHide(); });
  document.getElementById('pm-modal-send').addEventListener('click', _pmSend);
  document.getElementById('pm-modal-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _pmSend(); }
  });

  var _pmLink = nav.querySelector('a[href="/form/pm-chat"]');
  if (_pmLink) {
    _pmLink.addEventListener('click', function(e) {
      e.preventDefault();
      document.querySelectorAll('.nav-dropdown-menu').forEach(function(m) { m.classList.remove('open'); });
      _pmShow();
    });
  }

  // Async badge: fetch review-queue count if an API key is available on the page.
  function updateReviewBadge() {
    var key = window.TGW_API_KEY;
    if (!key) return;
    fetch('/api/items/review-queue', {headers: {Authorization: 'Bearer ' + key}})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var badge = document.getElementById('nav-review-count');
        if (badge) badge.textContent = (d && d.count > 0) ? String(d.count) : '';
      })
      .catch(function() {});
  }
  // Run after the page has set window.TGW_API_KEY (scripts execute sequentially).
  setTimeout(updateReviewBadge, 0);
})();
