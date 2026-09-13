/* Shared runtime for the Tenfold tablet screens.
   1. loads tally.svg once and clones an expression (plus accessories) into every [data-tally] host,
      keeping the named groups (.t-eyes, .t-mouth-group, .t-finger.*) as real nodes so they animate;
   2. injects icons.svg as a hidden sprite so pages can write <use href="#icon-lesson">;
   3. keeps the fixed 1194 x 834 tablet box scaled to the window. */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var sprite = null;

  function load() {
    if (sprite) return sprite;
    sprite = fetch('tally.svg')
      .then(function (r) { return r.text(); })
      .then(function (t) { return new DOMParser().parseFromString(t, 'image/svg+xml'); });
    return sprite;
  }

  function strip(node) {
    node.removeAttribute('id');
    Array.prototype.forEach.call(node.querySelectorAll('[id]'), function (n) { n.removeAttribute('id'); });
    return node;
  }

  function render(host, doc) {
    var expr = host.getAttribute('data-tally') || 'ready';
    var accs = (host.getAttribute('data-accessories') || '').split(/\s+/).filter(Boolean);
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 200 220');
    svg.setAttribute('class', 'tally-art');
    svg.setAttribute('aria-hidden', 'true');

    var back = [], front = [];
    accs.forEach(function (name) {
      var g = doc.getElementById('acc-' + name);
      if (!g) return;
      var clone = strip(g.cloneNode(true));
      (clone.getAttribute('data-layer') === 'back' ? back : front).push(clone);
    });
    var base = doc.getElementById('tally-' + expr);

    back.forEach(function (n) { svg.appendChild(n); });
    if (base) svg.appendChild(strip(base.cloneNode(true)));
    front.forEach(function (n) { svg.appendChild(n); });

    host.textContent = '';
    host.appendChild(svg);
  }

  window.Tally = {
    mount: function (root) {
      return load().then(function (doc) {
        Array.prototype.forEach.call((root || document).querySelectorAll('[data-tally]'), function (h) {
          render(h, doc);
        });
      });
    },
    set: function (host, expr, accessories) {
      if (!host) return;
      if (expr) host.setAttribute('data-tally', expr);
      if (accessories != null) host.setAttribute('data-accessories', accessories);
      return load().then(function (doc) { render(host, doc); });
    }
  };

  /* icons.svg is inlined rather than referenced: the symbol contents are copied into each
     <use href="#icon-x"> slot so currentColor resolves against the page, not a shadow tree. */
  var iconDoc = null;
  function loadIcons() {
    if (iconDoc) return iconDoc;
    iconDoc = fetch('icons.svg')
      .then(function (r) { return r.text(); })
      .then(function (t) { return new DOMParser().parseFromString(t, 'image/svg+xml'); });
    return iconDoc;
  }

  function expand(root, doc) {
    Array.prototype.forEach.call((root || document).querySelectorAll('use'), function (u) {
      var ref = u.getAttribute('href') || u.getAttribute('xlink:href') || '';
      if (ref.indexOf('#icon-') !== 0) return;
      var sym = doc.getElementById(ref.slice(1));
      if (!sym) return;
      var parent = u.parentNode;
      var owner = u.ownerSVGElement;
      if (owner && !owner.getAttribute('viewBox') && sym.getAttribute('viewBox')) {
        owner.setAttribute('viewBox', sym.getAttribute('viewBox'));
      }
      var g = document.createElementNS(NS, 'g');
      Array.prototype.forEach.call(sym.children, function (n) {
        g.appendChild(strip(document.importNode(n, true)));
      });
      parent.replaceChild(g, u);
    });
  }

  window.Icons = {
    expand: function (root) { return loadIcons().then(function (doc) { expand(root, doc); }); }
  };

  function icons() { return window.Icons.expand(document); }

  function fit() {
    var s = Math.min(1, window.innerWidth / 1194, window.innerHeight / 834);
    document.documentElement.style.setProperty('--fit', s);
  }
  window.addEventListener('resize', fit);
  fit();

  function start() {
    window.Tally.mount();
    icons();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
