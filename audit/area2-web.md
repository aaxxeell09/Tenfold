# Area 2 audit: web/course/

Read-only audit of `web/course/app.js`, `index.html`, `styles.css`, `levels.js`,
`levels_test.js`, `tally.js`, `tally.svg`, `icons.svg`.
Everything below was checked against the actual lines; claims that depend on the
server protocol were checked against `app/server.py` and `lesson/scheduler.py`,
which are outside this area and were not modified.

Checks run: `node --test web/course/levels_test.js` (17 pass), XP and level math
probed with `node -e`, the scheduler simulated with `python3` to confirm the fact
sequence of one node. No server started, no browser driven.

Counts: 0 BLOCKER, 10 BUG, 13 CLEANUP.

---

## BLOCKER

None found. The happy path (welcome, home, check, path, lesson, finish) holds
together, and the demo script in `demo/scenario.json` avoids the one sequencing
bug below. The two closest calls are BUG 3 and BUG 4, both of which degrade
silently rather than stop the page.

---

## BUG

### BUG 1. A lesson whose two pairs are the same fact freezes the counter and the first-try bonus
`web/course/app.js:572`, effects at `app.js:674` and `app.js:681`,
node defined at `web/course/levels.js:25`.

```js
if (fresh && m.fact && m.fact !== lesson.fact) { lesson.fact = m.fact; lesson.done += 1; lesson.slipped = false; }
```

Question counting and the first-try reset both hang off a change of `m.fact`.
The scheduler keys facts unordered with the smaller factor first, so node
`u1-l2` "Six and seven", `pairs: [[6, 7], [7, 6]]`, has exactly one fact in
scope. Simulated:

```
python3 -c "... s.start_session(pairs=[[6,7],[7,6]], length=5) ..."
0 6x7 6 7 next_new
1 6x7 7 6 review
2 6x7 6 7 review
3 6x7 7 6 review
4 6x7 6 7 review
```

All five exercises carry `fact == "6x7"`. Consequences for the whole lesson:
`lesson.done` stays 1, so the header reads "1 of 5" from start to finish
(`app.js:681`) and the progress bar stays at 0 percent (`app.js:674`, which
renders `done - 1`); and `lesson.slipped` is never reset, so one wrong pose,
one hint or one wrong answer on question 1 removes the 15 XP first-try bonus
for the remaining four questions.
It also means the same freeze appears on any future node whose pairs are two
orientations of one fact.
Fix direction: count questions from an index the server owns, or from
`answer_correct` transitions, and reset `slipped` on every new exercise rather
than on a change of fact.

### BUG 2. Leaving a running lesson by the hash leaves the lesson alive
`web/course/app.js:104` to `app.js:116` (`show`), `app.js:898` (hashchange).

`show()` only hides `#lesson` and `#finish`. It never calls `leaveLesson()`.
The lesson view covers the screen but the hash is still `#practice` while a
lesson runs, so the browser Back button routes to the previous view and leaves
`lesson` non null, the websocket node running on the server and no `quit` sent.
State messages keep arriving, `renderPractice` keeps writing into the hidden
`#lesson`, and `app.js:585` keeps calling `speak(...)`, so Tally goes on talking
over the home screen or the profile. The same applies to the finish screen
(`app.js:109`).

### BUG 3. The check camera never reconnects, and it streams from page load
`web/course/index.html:111`, compare `app.js:629`.

```html
<img class="practice-video" src="/video" alt="">
```

This element is static markup. Two consequences.
1. The MJPEG stream opens as soon as the page loads, on the welcome screen, and
   is held open for the life of the page even while the learner is on home,
   learn or profile. `hidden` does not stop an image from loading. During a
   lesson there are two concurrent `/video` streams from one tab.
2. Nothing ever re-requests it. If the server was not up when the page loaded,
   or is restarted while Chrome stays open, the check keeps showing a black box
   or a frozen last frame for the rest of the session, with the finger overlay
   moving on top of it, and there is no error path. The camera lesson recovers
   from the same restart because `shellPractice` builds a fresh `<img>` on every
   `startLesson` (`app.js:629`).
Fix direction: set and clear `src` in `startCheck` and `stopCheck`, and retry on
the element's `error` event.

### BUG 4. No websocket retry and no user feedback when the connection cannot be made
`web/course/app.js:542` to `app.js:551`.

`connect()` either calls back immediately, or queues `onOpen` on a socket that
may never open; `onclose` only nulls the socket. There is no timeout, no retry
and no UI. If the server is down, or dies between the click and the open:
`startLesson` (`app.js:602`) leaves "Getting ready" (`app.js:626`) on screen for
ever, and `startCheck` (`app.js:267`) leaves "Show me both hands." for ever.
Both are only escapable through the quit button, which sends a `quit` that is
silently dropped by `sendLesson` (`app.js:551`).
The recovery after a plain server restart does work for the next node, because
`onclose` nulls `socket` and the following `connect` builds a new one.

### BUG 5. A two level jump loses the accessory of the level it passed through
`web/course/app.js:742`, card built at `app.js:763` to `app.js:774`,
shown by `app.js:794` and `app.js:798`.

```js
const earned = after.level > before.level ? L.earnedAt(after.level) : null;
```

A perfect boss is 220 XP (checked: `xpForNode("boss", 8, 8, true) === 220`) and
the level 1 to 2 and 2 to 3 spans are 100 and 150, so from 30 XP a boss takes
the learner from level 1 to level 3 in one node (checked). `earnedAt(3)` is
null, so the level up card says "Tally is proud of you" and the glasses earned
at level 2 simply appear on Tally with no mention. Only one card is shown for
two levels.
Fix direction: collect `earnedAt` for every level in `(before.level, after.level]`.

### BUG 6. A wrong pose costs the first-try XP, which contradicts the star score
`web/course/app.js:573`, against `app/server.py` `_check`.

```js
if (m.hint_level > 0 || (fresh && m.state === "wrong_pose")) lesson.slipped = true;
```

The server states the opposite rule in `_check`: "Fixing a finger on the way is
how the input works, not a mistake: a child who corrects their hand and then
answers right has earned the point", and counts that answer in the `correct`
that the page uses for stars (`app.js:734`). So the same exercise can earn a
star point and pay 10 XP instead of 15. The two definitions of "first try"
should be one definition. The `hint_level > 0` half is defensible (hints are
raised by elapsed time in `_reaction`), the `wrong_pose` half is not.

### BUG 7. Quitting a lesson discards every correct answer already paid for
`web/course/app.js:820` to `app.js:825` (`leaveLesson`), reached from
`app.js:870`.

`addXp` only runs inside `finish()`. A learner who answers three of five
correctly and then presses the quit cross gets nothing: no stars (expected) and
no XP for the three correct answers. SPEC section on XP says 10 per correct
exercise, so the per-answer XP is arguably already earned at that point.

### BUG 8. Once the microphone is denied, there is no way back inside the session
`web/course/app.js:479` to `app.js:506`, the `onerror` handler at
`app.js:486` to `app.js:488`, mic button at `app.js:869`.

`voice.denied` is set on `not-allowed` or `service-not-allowed` and is never
cleared. After that `armVoice`, `startVoice` and the mic button all return
early for ever, even if the learner grants the permission in Chrome a moment
later. Only a reload recovers.
Related: `app.js:897` arms recognition on the first click anywhere, which means
the permission prompt appears on the welcome screen, before anything has
explained why the microphone is wanted. A denial there disables voice for the
whole session.

### BUG 9. The check's third step has no visible way to answer without a microphone
`web/course/index.html:120` to `index.html:124`, `web/course/app.js:889` to
`app.js:892`, label at `app.js:507`.

At step 3 the check says "Say the answer." and shows the mic pill whatever the
browser supports. On a browser with no `SpeechRecognition`, `micLabel()` returns
"Mic off" (it only returns "Type it" when the mic was explicitly denied), and
the check view contains no `.typed` element, so `setTyped` writes nowhere
(`app.js:726`). Typing does in fact work, the digits are shown in
`#check-miclabel` by `app.js:890`, but Backspace at `app.js:891` does not update
that label, so a deleted digit stays on screen. The learner is never told that
typing is possible.

### BUG 10. The stylesheet blocks the first paint on a Google Fonts request
`web/course/styles.css:5`, with `web/course/index.html:9` and `index.html:10`.

```css
@import url('https://fonts.googleapis.com/css2?family=Nunito:...');
```

An imported stylesheet is render blocking. The rest of the app is fully local
(the server serves every file in `web/course/` by name), so on venue wifi or
offline the demo machine waits on a request that cannot succeed before painting
anything. Ship the font, or drop the import and rely on the fallback stack.

---

## CLEANUP

### C1. `watchCamera` is dead code and so is the CSS it targets
`web/course/app.js:902` to `app.js:912`, called at `app.js:917`;
`web/course/styles.css:770` and `styles.css:771`.
`$(".panel-camera-art")` matches nothing: the right rail of the learn view is
`.panel-hands` (`index.html:76`). The function returns on its third line. Had it
run it would have opened a third `/video` stream for a 96 by 72 thumbnail.

### C2. About a third of styles.css targets markup that no longer exists
`web/course/styles.css` is three stylesheets concatenated; the second block is
introduced at line 270 as "base rules from the shell, dropped by the merge and
restored" and the third at line 559 and 560 as "Load after styles.css: welcome.html,
hub.html, learn.html, check.html, practice.html, lesson.html and finish.html".
Of the 318 distinct class names in the file, roughly 95 match nothing in
`index.html`, `app.js`, `tally.js`, `levels.js`, `tally.svg` or `icons.svg`.
Examples: `.fit` (68), `.screen` (69), `.topbar`, `.brand`, `.chips`, `.chip`,
`.dot`, `.stage`, `.cam-label`, `.cards`, `.castle`, `.quest-row`, `.wardrobe`,
`.verdict`, `.options`, `.pdots`, `.tabbar`, `.hero`, `.lesson-grid`,
`.finish-inner`, `.starbank`, `.ghosts` (670 to 678), and the whole button
family `.btn-blue`, `.btn-red`, `.btn-white`, `.btn-ghost`, `.btn-disabled`,
`.btn-lg`, `.map-btn`, which the markup never sets (it uses `.btn-primary`,
`.btn2`, `.btn2.red`, `.btn2.ghost`).
Two selectors are defined twice with different geometry: `.cam` at
`styles.css:183` (width 716px, 16 by 9) and again at `styles.css:666`
(width 100 percent), `.dev-reset` at `styles.css:316` and `styles.css:754`.
Both current users of `.cam` override width, so this works by accident.

### C3. Dead state selectors: the page never writes those state values
`web/course/styles.css:53`, `200` to `202`, `683`, `689`, `694`, `714` to `717`.
`body[data-state="yes"]` never matches: `app.js` sets `body.dataset.view`
(`app.js:107`, `app.js:611`, `app.js:777`), never `body.dataset.state`.
`[data-state="cannotsee"]` and `[data-state="yes"]` never match either:
`app.js:685` to `app.js:687` write real engine states (`exercise_shown`,
`correct_pose`, `answer_correct` and so on) into `data-state`, and the two
panels that matter are driven instead by `[data-reaction="cannot_see"]`
(`styles.css:830`) and `.has-reasons` (`styles.css:832`). The speech bubble
colour rules at `styles.css:714` to `717` therefore never fire.

### C4. Unused icon symbols
`web/course/icons.svg`: `icon-check`, `icon-grid`, `icon-user` are never
referenced by `use(...)` or by any `<use href="#icon-...">` in `index.html`.

### C5. Two sources of truth for the learner's name
`web/course/app.js:16`, `app.js:37` to `app.js:47`.
`tenfold.name` duplicates `learner.display_name`. The dev reset
(`app.js:872`) removes `tenfold.learner` but not `tenfold.name`, so after a
reset the learner keeps a name with zero XP and never sees the welcome screen
again.

### C6. `unitAt` survives leaving the practice view
`web/course/app.js:57`, `app.js:203` to `app.js:207`, set at `app.js:866`.
Switch to unit 3, go home, come back: the map still opens on unit 3 rather than
on the current node's unit. It is only cleared by the dev reset, by `backToMap`
and by the demo seeding.

### C7. `recordLesson` can return without `stars`, and `finish` compares it to 0
`web/course/levels.js:175` returns `{ progress: p, error: "locked" }`;
`web/course/app.js:743` tests `res.stars === 0`. With `stars` undefined the
screen would claim "Lesson complete!" with zero stars and `res.unlocked`
undefined. Not reachable from the current UI (`app.js:861` blocks locked nodes),
but the guard should test the error, not the star count.

### C8. Only 6 of the 10 levels are reachable in one pass
Checked: every scored node played perfectly is 1020 XP, which is level 6.
`web/course/levels.js:59` to `levels.js:71` advertise thresholds up to 3200 and
accessories at levels 8 and 10 (cape, crown); the profile ladder
(`app.js:844` to `app.js:851`) lists them all. They can only be reached by
replaying lessons, which grants full XP again every time (`app.js:738`).

### C9. Two tabs on the same app both write the learner record
`web/course/app.js:554` to `app.js:561` runs in every open tab, including tabs
with no lesson, because the `node_end` broadcast reaches every websocket
subscriber. XP is protected by the `Math.max` at `app.js:558`, so nothing is
lost, but the two writes race and the scheduler memory of the losing tab wins.

### C10. The camera check can be skipped, then comes back later
`web/course/index.html:62` links the learn sidebar straight to `#practice`, and
`route()` (`app.js:117`) allows it. `tenfold.checked` is only set by
`checkEnd` (`app.js:345`), so a learner who enters the path that way is sent
through the check the next time they use the home door.

### C11. `fitOverlay` runs before the first frame has a size
`web/course/app.js:655` to `app.js:670`.
`videoRatio` falls back to 4 by 3 while `naturalWidth` is 0, and nothing listens
for the image's `load` event, so the first overlay is laid out for the wrong
aspect ratio and the finger circles are offset until the next state message
redraws them. No division by zero is possible: `ratio` is never 0, and a hidden
or zero sized box gives width 0, not a divide.
Minor ordering wart on the same function: line 659 queries inside `stage` before
line 660 checks that `stage` is not null, so a null `stage` silently queries the
whole document; the early return makes it harmless today.

### C12. `decorate` on every frame of a boss lesson
`web/course/app.js:677` calls `decorate(hearts)` on every state message, which
recomputes `levelInfo`, walks four `data-level-*` queries and chains two async
svg fetches, only to expand one flame icon.

### C13. Recognition stays live on every screen
`web/course/app.js:500` to `app.js:506` and `app.js:246`: once armed,
`voice.wanted` stays true and `onend` restarts recognition for the life of the
page, so Chrome's microphone indicator is lit on the welcome, home and profile
screens. Only `voice.gate` stops a heard number from being submitted.

---

## Checked and found correct

Worth recording, since these were the suspicious spots.

- `levelInfo` at the top level: `levelInfo(3200)` gives percent 100, `toNext` 0,
  `ceiling` null, and the `LEVELS[level - 2]` access at `levels.js:94` is in
  range. `levelInfo(-5)`, `levelInfo(NaN)` and `levelInfo(5000)` are all sane.
- `xpForNode` clamps `firstTry` to `correct` and floors negatives
  (`levels.js:75` to `levels.js:77`), so a local first-try over count cannot
  inflate XP.
- No double XP from the server: `node_end` is published once per session by
  `_end_session`, which is guarded by `self.finished`, and `_quit` publishes
  nothing, so the out-of-hearts path at `app.js:578` cannot be followed by a
  second `finish`.
- XP cannot be lost at `node_end`: `app.js:558` keeps the higher of the local
  and the returned value, and the server round trips `xp` and `display_name`.
- Every `$("#id")` in `app.js` exists in `index.html` or in markup the file
  itself writes; every `data-tally` expression (`happy`, `ready`, `hands`,
  `thinking`, `almost`, `squint`) exists in `tally.svg`.
- No handler is bound to an element that `innerHTML` later replaces: clicks and
  keys are delegated on `document` (`app.js:856`, `app.js:881`), and the only
  direct binding is on the static `#welcome-form`.
- The check timers cannot fire after the view is left: `later` only pushes while
  `checkRun` exists (`app.js:284`) and `stopCheck` clears them all
  (`app.js:276`).
- `[hidden]` is enforced with `display: none !important` at `styles.css:272`, so
  the `.view` rule at `styles.css:750` does not leak hidden sections.
- Reload in the middle of a lesson is clean on the page side: nothing is written
  until `finish`, the page comes back on the map, and the next `start_node`
  hands the server the stored learner again. The lesson's XP and mastery are
  lost, nothing is corrupted.
- `speechSynthesis` is guarded everywhere it is touched (`app.js:410`,
  `app.js:172` region, `app.js:823`), missing voices are handled by the
  `voiceschanged` plus 1500 ms timeout path (`app.js:392` to `app.js:405`), and
  a `speak` before any user gesture fails into `onerror` and drains the queue
  rather than throwing.
