# Side by side, export page versus live app

Seven pairs, one image per screen, export on the left, live on the right.
Both sides shot in chromium at 1194x834, device scale 1.
Live app: `python app/server.py --mock --no-open --port 8860`, localStorage seeded with
child "Mia", 340 XP, stars on u1-l1 and u1-l2, chest u1-c1 opened.
Export pages opened over `file://` from `web/course/design/`.

| screen | export page | live |
| --- | --- | --- |
| welcome | welcome.html | #welcome |
| home | hub.html | #home |
| practice | practice.html | #practice |
| check | check.html | #check |
| lesson | exercise.html | node u1-l3 started |
| finish | finish.html | end of node u1-l3 |
| profile | profile.html | #profile |

## What differs

- side-welcome.png: identical layout, type and art; the live page adds a round sound toggle button at the bottom right that the export does not have.
- side-home.png: same two cards and same art; the live title reads "Ready to practice, Mia?" with a "Not Mia?" button next to it, the export reads "Ready to practice?" with no button; live adds the sound toggle at the bottom right.
- side-practice.png: same header, same unit card frame, same node art. Live shows unit subtitle "Thumbs are 6, pinkies are 10" against the export's "Numbers on every fingertip", "Lessons done 2 of 3" against the export's "2 of 6", four nodes (two done, a chest, one current) against the export's six (two done, one current, three locked), path drawn top left to bottom centre against the export's zig zag, XP chip "Tip Toucher 90 / 200 XP" against "Finger Counter 60 / 150 XP", Tally line "Next up: Mix it up." against "Your 6 times are next.", and the live Tally wears glasses. Live adds the sound toggle.
- side-check.png: the two pages show different things. The export file is a stacked states sheet: two check cards drawn on top of each other, step 1 "SHOW ME BOTH HANDS" behind and a second card with hand outlines, the word "Yes!", a "YES, I AM READY" button and two Tally bubbles. The live page shows one state only, the green banner "That is a 6 and a 6.", a full bleed dark camera panel with the mock camera frame, a green check disc, black round blobs where the export draws numbered finger dots, Tally's bubble "Yes, that's it." and the sound toggle. No step chips 1 2 3 are visible live.
- side-lesson.png: same header, same rounded camera panel, same bubble shape. The live camera panel shows the flat "mock camera" frame with no hand outlines and no numbered finger dots at the moment of capture, while the export draws two labelled hands with 8 and 7 highlighted; live is on question "3 OF 5" with "7 x 7" against the export's "2 OF 5" and "8 x 7"; live line is "Let us try 7 x 7 again. You were close." against "Touch your 8 with your 7."; the bottom right chip reads "MIC OFF" live against "LISTENING" in the export; the export's "DEV ONLY" row of WAITING, ALMOST, YES, CANNOT SEE buttons is absent live; live adds the sound toggle.
- side-finish.png: same card layout, same XP bolt, same bar. The run captured live ended below the star threshold, so live reads "Almost there" with "Get 3 of 5 right to earn a star and open the next lesson.", 25 XP, "Tip Toucher 115 / 200 XP" and two buttons, TRY AGAIN and BACK TO THE PATH; the export reads "Lesson complete!" with "Your 8 times are getting quick.", 30 XP, "Finger Counter 90 / 150 XP" and a single CONTINUE button; the live Tally wears glasses, the export Tally does not; live adds the sound toggle.
- side-profile.png: same two column layout, same ten level list, same gear row. Live is at "LEVEL 3 OF 10 Tip Toucher", 90 / 200 XP, "Next: Ten Maker", with levels 1 and 2 ticked and "YOU ARE HERE" on level 3; the export is at "LEVEL 2 OF 10 Finger Counter", 90 / 150 XP, "Next: Tip Toucher", with "YOU ARE HERE" on level 2. The live Tally art wears the glasses and shows the yellow and purple confetti marks the export art does not. Live adds the sound toggle.
