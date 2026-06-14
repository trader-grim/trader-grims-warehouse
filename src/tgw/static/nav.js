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
