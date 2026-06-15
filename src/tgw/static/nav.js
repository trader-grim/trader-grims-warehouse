(function() {
  var nav = document.createElement('nav');
  nav.className = 'tgw-nav';
  nav.innerHTML =
    '<a class="brand" href="/form/home">TGW</a>' +
    '<a href="/form/home">Home</a>' +
    '<a href="/form/intake">Intake</a>' +
    '<a href="/form/items">Inventory</a>' +
    '<a href="/form/review">Review <span id="nav-review-count" class="nav-count"></span></a>' +
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

  document.addEventListener('keydown', function(e) { if (e.key === 'Escape') _sgHide(); });

  var _sgLink = nav.querySelector('a[href="/form/suggest"]');
  if (_sgLink) {
    _sgLink.addEventListener('click', function(e) {
      e.preventDefault();
      document.querySelectorAll('.nav-dropdown-menu').forEach(function(m) { m.classList.remove('open'); });
      _sgShow();
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
