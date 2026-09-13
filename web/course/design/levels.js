/* The only progression in Tenfold: Tally levels up with XP.
   Ten levels, 150 XP each, an accessory at levels 2, 4, 6, 8 and 10. */
(function () {
  var LEVELS = [
    'Thumb Buddy', 'Finger Counter', 'Tip Toucher', 'Ten Maker', 'Sixes Master',
    'Sevens Tamer', 'Eights Rider', 'Nines Wizard', 'Tens Champion', 'Tenfold Hero'
  ];
  var ACC = { 2: 'glasses', 4: 'headband', 6: 'hat', 8: 'cape', 10: 'crown' };
  var SPAN = 150;
  var KEY = 'tenfold.xp';

  function xp() {
    var v = parseInt(localStorage.getItem(KEY), 10);
    return isNaN(v) ? 210 : v;
  }
  function setXp(v) { localStorage.setItem(KEY, String(Math.max(0, v))); }

  function info(value) {
    var total = value == null ? xp() : value;
    var level = Math.min(LEVELS.length, Math.floor(total / SPAN) + 1);
    var inLevel = level === LEVELS.length ? SPAN : total % SPAN;
    return {
      total: total,
      level: level,
      name: LEVELS[level - 1],
      inLevel: inLevel,
      span: SPAN,
      percent: Math.round(inLevel / SPAN * 100),
      next: level < LEVELS.length ? LEVELS[level] : null,
      accessories: accessories(level)
    };
  }
  function accessories(level) {
    return Object.keys(ACC).filter(function (k) { return Number(k) <= level; })
      .map(function (k) { return ACC[k]; }).join(' ');
  }
  function earnedAt(level) { return ACC[level] || null; }

  window.TF = {
    LEVELS: LEVELS, ACC: ACC, SPAN: SPAN,
    xp: xp, setXp: setXp, info: info, accessories: accessories, earnedAt: earnedAt
  };
})();
