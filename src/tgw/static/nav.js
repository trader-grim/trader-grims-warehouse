(function() {
  var nav = document.createElement('nav');
  nav.className = 'tgw-nav';
  nav.innerHTML =
    '<a class="brand" href="/form/home">TGW</a>' +
    '<a href="/form/home">Home</a>' +
    '<a href="/form/items">Inventory</a>' +
    '<a href="/docs">Docs</a>' +
    '<a href="/form/links">Links</a>' +
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
})();
