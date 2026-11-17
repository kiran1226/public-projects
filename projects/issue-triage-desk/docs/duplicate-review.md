# Duplicate review

The evaluation scores a duplicate call against one thing: whether the maintainers
closed the issue with the reason "duplicate". Every other call counts as "a new
issue called a duplicate", the worst error. This file checks how far that count can
be trusted, by reading the calls the record does not confirm.

**Who read them.** The AI model that built this project (Claude), from each issue's
title and the opening of its description, beside the same for the earlier issue the
desk named. No maintainer and no person has checked these verdicts. They are one
reader's judgement of whether two reports describe the same defect, and they are
not used in any score.

**Verdicts.** "Same" means both reports describe the same defect in the same
situation. "Arguable" means related, with a difference that could matter: usually an
old problem that was fixed and has come back, which maintainers treat as a new
issue.

Issue text is not reproduced here. The numbers can be opened on the tracker.

## Development months (issues opened May to August 2026)

### Every call at high confidence that the record does not confirm

The desk called 21 issues duplicates at high confidence. The record confirms 3. The
other 18:

| New issue | Named earlier issue | What the maintainers did | Verdict | Why |
|---:|---:|---|---|---|
| 119224 | 115824 | Closed as a duplicate of a third issue | Same | Both report the script name not updating after the script filter was used; the issue named was itself closed as a duplicate |
| 119302 | 110536 | Closed, completed | Arguable | The reporter calls it a regression of the issue named: same symptom, new cause after a fix |
| 119772 | 90803 | Closed, completed | Same | Old autoload name stays in code completion after a rename |
| 120680 | 119164 | Closed, not planned | Same | Blend space point name does not update when its animation changes |
| 120758 | 120757 | Closed, not planned | Same | The same advertisement posted twice |
| 120818 | 62727 | Closed, completed | Arguable | Both concern a scene flag that is not saved; the older report is about the unsaved marker, the newer about the running game |
| 120885 | 120877 | Open | Same | The same call and the same error message, filed twice |
| 121208 | 110142 | Closed, completed | Same | Both point at the same lines: an entry removed by value instead of by key |
| 121601 | 121591 | Open | Same | The new report says it is a rewrite of the issue named |
| 122004 | 120470 | Closed, completed | Same | The same three rendering settings together produce the same errors |
| 122265 | 122262 | Open | Same | The new report says it refiles the issue named |
| 122382 | 108325 | Open | Same | The same project setting changes the editor's own colours |
| 122426 | 122406 | Open | Same | The same text, filed again as a separate issue |
| 122437 | 74090 | Open | Same | The same crash when the log file cannot be created |
| 122498 | 122461 | Closed, completed | Same | The same resource leak in the same structure |
| 122648 | 122600 | Closed, completed | Same | The same crash on exit when the same kind of resource is leaked |
| 122661 | 89637 | Closed, completed | Same | The same missing entries in the same context menu |
| 122753 | 89533 | Closed, completed | Same | The same request to rename a menu entry on one platform |

**16 of the 18 are the same defect by this reading, 2 are arguable.** With the 3 the
record confirms, 19 of the desk's 21 high-confidence calls on the development months
point at an earlier report of the same defect.

Why the record misses them: an issue filed twice is often closed as "completed"
when the fix lands, or as "not planned", or both copies stay open. The close reason
"duplicate" is used for a part of the duplicates only.

### A sample of the calls at medium confidence

At medium confidence the desk named an earlier issue for 142 issues. The record
confirms 13, names a different original for 12, and has 6 duplicates whose original
is elsewhere or unknown. The other 111 were not closed as duplicates. Twenty of
those, drawn at random with a fixed seed:

| New issue | Named earlier issue | Verdict |
|---:|---:|---|
| 119139 | 108055 | Same |
| 119155 | 83772 | Same |
| 119260 | 101302 | Same |
| 119474 | 80894 | Arguable: both about saved values of exported properties, opposite complaints |
| 119593 | 115572 | Arguable: same menu entry, different conditions |
| 119812 | 58952 | Same |
| 119898 | 108270 | Same |
| 120029 | 98764 | Same |
| 120283 | 103497 | Same |
| 120295 | 120275 | Same |
| 120310 | 83132 | Same |
| 120405 | 112127 | Same |
| 120431 | 117440 | Same |
| 120452 | 120319 | Same |
| 120877 | 91259 | Same |
| 121040 | 90100 | Arguable: the same error message two years apart, probably a regression |
| 121268 | 91575 | Arguable: the same crash two years apart, probably a regression |
| 122192 | 120441 | Arguable: the same export step hangs, too little detail to tell |
| 122379 | 118161 | Arguable: the same packaging fault in a later release |
| 122933 | 91296 | Same |

**14 of the 20 are the same defect by this reading, 6 are arguable.** None is an
unrelated issue.

## Held-out months (issues opened September and October 2026)

Read after the held-out months were scored, for the first of the three runs. Nothing
was changed afterwards.

### Every call at high confidence that the record does not confirm

The desk called 27 issues duplicates at high confidence. The record confirms
6. The others:

| New issue | Named earlier issue | What the maintainers did | Verdict | Why |
|---:|---:|---|---|---|
| 123208 | 123172 | Open | Same | The new report says it refiles the issue named, at a maintainer's request |
| 123361 | 123360 | Closed, not planned | Same | The same advertisement posted twice |
| 123465 | 93510 | Open | Same | A type check that does not silence the same two warnings |
| 123481 | 123479 | Open | Same | The same deadlock on the same platform and release |
| 123486 | 123445 | Closed, completed | Same | The new report says it is a hand-written version of the issue named |
| 123508 | 108177 | Open | Same | The remote preview ignores moves made with the editor's tools |
| 123722 | 113891 | Open | Same | The same crash with the same syntax highlighter |
| 123805 | 104869 | Open | Same | The waveform preview does not update after a re-import |
| 123829 | 111840 | Open | Same | A project setting for the theme scale changes the editor itself |
| 123852 | 123851 | Open | Same | The same text, filed twice |
| 123915 | 123913 | Open | Same | The same editor crash when the same kind of object is hidden |
| 123963 | 123254 | Closed, completed | Same | The same argument passed in the wrong position |
| 124050 | 98537 | Closed, completed | Arguable | The reporter calls it the same bug, in a different window |
| 124280 | 124178 | Closed, completed | Same | The same keys stay pressed in the embedded game view |
| 124570 | 123963 | Closed, completed | Same | The same argument passed in the wrong position |
| 124571 | 123963 | Closed as a duplicate of 124570 | Same | The record names 124570, which is itself a report of the issue named (row above) |
| 124823 | 111677 | Open | Same | The same material setting distorts the same editor shapes |
| 124837 | 124010 | Closed, not planned | Same | The same race in the same instruction |
| 124849 | 119778 | Closed, completed | Arguable | Both concern the end of a loop in the same mixer; one reports a crash, the other skipped frames |
| 124929 | 124912 | Closed, not planned | Same | The same build failure with the same generated call |
| 124939 | 124929 | Open | Same | The same build failure, filed a third time |

**19 of the 21 are the same defect by this reading, 2 are arguable.** None is
an unrelated issue. Three say in their own text that they repeat the issue named, and
two more are the same text posted twice.

## What this means for the numbers

- "New issue called a duplicate" is an upper bound, and a loose one. At high
  confidence the record puts it at 17 on the development months and 20 on the
  held-out months; by this reading the calls that point at a different defect number
  at most 2 in each.
- "Duplicates found" is measured against the part of the duplicates that the
  maintainers closed with that reason. It says how often the desk agrees with them,
  not how many duplicates exist.
- A fair measure of the duplicate step needs maintainers to judge a sample of the
  desk's calls. That is what the shadow run is for.
