/* Shared runtime for the Tenfold tablet screens.
   1. draws Tally into every [data-tally] host from the 3D renders in art/, with the
      accessories of [data-accessories] layered on top (the cape behind him);
   2. injects icons.svg as a hidden sprite so pages can write <use href="#icon-lesson">;
   3. keeps the fixed 1194 x 834 tablet box scaled to the window.
   The public surface is window.Tally.mount and window.Tally.set, unchanged. */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var ART = 'art/';

  /* The one mapping table: the six expressions the page asks for, and the render
     each one is drawn with. There are four faces for six expressions, so two of
     them share: "thinking" is drawn with the ready face and "squint" with the
     almost face. Nothing else in the page knows about this. */
  var FACES = {
    ready: 'tally-ready.png',
    thinking: 'tally-ready.png',
    almost: 'mascot.png',
    squint: 'mascot.png',
    happy: 'tally.png',
    hands: 'hands.png'
  };
  /* A host with the avatar class is the small Tally of the level boxes: one
     tight crop for every expression, because it is never larger than 46px. */
  var AVATAR = 'tally-avatar.png';

  /* Where things sit on each render, in percent of its box: the eyes, the brow
     line above them, the middle and top of the head, and where a cape hangs
     from. Every accessory is placed from these four numbers, so a new render
     needs one row here and nothing else. */
  var ANCHORS = {
    'tally.png': { eyeX: 47, eyeY: 50, faceW: 34, browY: 35, headX: 47, topY: 3, capeY: 50 },
    'tally-ready.png': { eyeX: 56, eyeY: 58, faceW: 38, browY: 45, headX: 53, topY: 1, capeY: 60 },
    'mascot.png': { eyeX: 53, eyeY: 48, faceW: 36, browY: 34, headX: 51, topY: 1, capeY: 54 },
    'tally-avatar.png': { eyeX: 52, eyeY: 52, faceW: 34, browY: 38, headX: 50, topY: 4, capeY: 56 },
    'hands.png': { eyeX: 50, eyeY: 46, faceW: 30, browY: 32, headX: 50, topY: 4, capeY: 52 }
  };

  /* Each accessory, as a rule read off those anchors: where its middle goes, how
     wide it is next to the face, whether it hangs from its top edge, and whether
     it is drawn behind Tally. The cape is the only one behind him. */
  var ACCESSORIES = {
    glasses: { x: 'eyeX', y: 'eyeY', scale: 1.12, from: 'middle', layer: 'front' },
    headband: { x: 'eyeX', y: 'browY', scale: 1.5, from: 'middle', layer: 'front' },
    hat: { x: 'headX', y: 'topY', scale: 1.35, from: 'top', layer: 'front' },
    crown: { x: 'headX', y: 'topY', scale: 1.0, from: 'top', layer: 'front' },
    cape: { x: 'headX', y: 'capeY', scale: 2.2, from: 'top', layer: 'back' }
  };

  function faceFor(host) {
    var expression = host.getAttribute('data-tally') || 'ready';
    if (host.classList.contains('avatar')) return AVATAR;
    return FACES[expression] || FACES.ready;
  }

  function accessory(name, anchor) {
    var rule = ACCESSORIES[name];
    if (!rule) return null;
    var img = document.createElement('img');
    img.className = 't-acc t-acc-' + name + (rule.layer === 'back' ? ' t-acc-back' : '');
    img.src = ART + 'acc-' + name + '.png';
    img.alt = '';
    img.style.left = anchor[rule.x] + '%';
    img.style.top = anchor[rule.y] + '%';
    img.style.width = (anchor.faceW * rule.scale) + '%';
    img.style.transform = rule.from === 'top' ? 'translate(-50%, 0)' : 'translate(-50%, -50%)';
    return img;
  }

  function render(host) {
    var face = faceFor(host);
    var anchor = ANCHORS[face] || ANCHORS['tally.png'];
    var names = (host.getAttribute('data-accessories') || '').split(/\s+/).filter(Boolean);

    var art = document.createElement('span');
    art.className = 'tally-art';
    var base = document.createElement('img');
    base.className = 't-base';
    base.src = ART + face;
    base.alt = '';

    // the cape goes in first so Tally covers it, everything else on top of him
    names.forEach(function (name) {
      var node = ACCESSORIES[name] && ACCESSORIES[name].layer === 'back' ? accessory(name, anchor) : null;
      if (node) art.appendChild(node);
    });
    art.appendChild(base);
    names.forEach(function (name) {
      var node = ACCESSORIES[name] && ACCESSORIES[name].layer !== 'back' ? accessory(name, anchor) : null;
      if (node) art.appendChild(node);
    });

    host.textContent = '';
    host.setAttribute('aria-hidden', host.getAttribute('aria-hidden') || 'true');
    host.appendChild(art);
  }

  window.Tally = {
    mount: function (root) {
      Array.prototype.forEach.call((root || document).querySelectorAll('[data-tally]'), render);
      return Promise.resolve();
    },
    set: function (host, expr, accessories) {
      if (!host) return Promise.resolve();
      if (expr) host.setAttribute('data-tally', expr);
      if (accessories != null) host.setAttribute('data-accessories', accessories);
      render(host);
      return Promise.resolve();
    },
    /* the table itself, so a test can check every expression has a render */
    faces: FACES
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

  function strip(node) {
    node.removeAttribute('id');
    Array.prototype.forEach.call(node.querySelectorAll('[id]'), function (n) { n.removeAttribute('id'); });
    return node;
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
