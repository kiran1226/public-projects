# Evaluation report

Written by `triagedesk eval` from the committed logs. Do not edit by hand.

## What was scored

- Tracker: `godotengine/godot`. History from 2022-01-01: 33,371 issues stored, 30,935 of them opened before the cut (2026-05-01) and used for training.
- Taxonomy: 35 labels (29 area, 6 platform). 4 labels were too rare to keep.
- Development months: issues opened 2026-05-01 to the day before 2026-09-01. Settings were chosen here.
- Held-out months: issues opened 2026-09-01 to 2026-10-31. Not looked at while settings were chosen.
- Truth: the labels on each issue and its close reason, read on 2026-11-17, at least 14 days after it was opened.

## Held-out months

- 754 issues. 638 carry at least one area label of the taxonomy, 233 carry several, 116 carry none.
- 36 were closed as duplicates. The original was resolved for 28; 1 point to another repository, 7 name no single earlier issue. Only resolved duplicates count towards "duplicates found".

### Labels

| Configuration | Labels proposed | Right | Of the maintainers' labels found | Area right | Applied alone | Wrong among those | ...on issues left unlabelled |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rules (keywords) | 1,030 | 484 of 1,030 (47%) | 484 of 1,024 (47%) | 206 of 638 (32%) | 599 | 242 of 599 (40%) | 89 |
| Classifier (TF-IDF, logistic regression) | 1,184 | 716 of 1,184 (60%) | 716 of 1,024 (70%) | 347 of 638 (54%) | 199 | 48 of 199 (24%) | 29 |
| Model alone (label names only, no candidates) | 1,040 | 690 of 1,040 (66%) | 690 of 1,024 (67%) | 443 of 638 (69%) | 665 | 170 of 665 (26%) | 92 |
| Model with definitions and candidates | 905 | 622 of 905 (69%) | 622 of 1,024 (61%) | 412 of 638 (65%) | 520 | 135 of 520 (26%) | 67 |
| Model with definitions and candidates, run 2 | 904 | 626 of 904 (69%) | 626 of 1,024 (61%) | 419 of 638 (66%) | 525 | 134 of 525 (26%) | 69 |
| Model with definitions and candidates, run 3 | 901 | 621 of 901 (69%) | 621 of 1,024 (61%) | 416 of 638 (65%) | 522 | 135 of 522 (26%) | 66 |
| Cascade (classifier when sure, model otherwise) | 907 | 625 of 907 (69%) | 625 of 1,024 (61%) | 413 of 638 (65%) | 530 | 135 of 530 (25%) | 67 |

"Area right" counts issues where every area label the desk proposed is on the issue and it proposed at least one, out of the issues the maintainers gave one. "Applied alone" are labels at high confidence. A label on an issue the maintainers left without any label of the taxonomy counts as wrong; the last column says how many of the wrong ones are of that kind.

Per kind of label:

| Configuration | area: right of proposed | area: found of present | platform: right of proposed | platform: found of present |
|---|---:|---:|---:|---:|
| Rules (keywords) | 387 of 839 (46%) | 387 of 897 (43%) | 97 of 191 (51%) | 97 of 127 (76%) |
| Classifier (TF-IDF, logistic regression) | 609 of 988 (62%) | 609 of 897 (68%) | 107 of 196 (55%) | 107 of 127 (84%) |
| Model alone (label names only, no candidates) | 575 of 806 (71%) | 575 of 897 (64%) | 115 of 234 (49%) | 115 of 127 (91%) |
| Model with definitions and candidates | 511 of 720 (71%) | 511 of 897 (57%) | 111 of 185 (60%) | 111 of 127 (87%) |
| Model with definitions and candidates, run 2 | 514 of 719 (71%) | 514 of 897 (57%) | 112 of 185 (61%) | 112 of 127 (88%) |
| Model with definitions and candidates, run 3 | 511 of 717 (71%) | 511 of 897 (57%) | 110 of 184 (60%) | 110 of 127 (87%) |
| Cascade (classifier when sure, model otherwise) | 514 of 723 (71%) | 514 of 897 (57%) | 111 of 184 (60%) | 111 of 127 (87%) |

### Duplicates

| Configuration | Called a duplicate | Right | Real duplicate, other issue named | **New issue called a duplicate** | ...of them at high confidence | Duplicates found |
|---|---:|---:|---:|---:|---:|---:|
| Rules (keywords) | 0 | 0 | 0 | **0** | 0 | 0 of 28 (0%) |
| Classifier (TF-IDF, logistic regression) | 36 | 2 | 2 | **32** | 6 | 2 of 28 (7%) |
| Model alone (label names only, no candidates) | 4 | 0 | 0 | **4** | 0 | 0 of 28 (0%) |
| Model with definitions and candidates | 99 | 11 | 6 | **82** | 20 | 11 of 28 (39%) |
| Model with definitions and candidates, run 2 | 92 | 11 | 6 | **75** | 19 | 11 of 28 (39%) |
| Model with definitions and candidates, run 3 | 92 | 11 | 6 | **75** | 19 | 11 of 28 (39%) |
| Cascade (classifier when sure, model otherwise) | 92 | 10 | 6 | **76** | 18 | 10 of 28 (36%) |

The original is among the earlier issues the search returns: first place: 8 of 28 (29%), first 3: 14 of 28 (50%), first 5: 15 of 28 (54%), first 8: 18 of 28 (64%), first 20: 21 of 28 (75%).

### Routing and cost

| Configuration | Issues | Handled alone | ...with nothing wrong | To a person | Answered by the model | Cost per 1,000 issues | Seconds per issue |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rules (keywords) | 754 | 229 of 754 (30%) | 132 of 229 (58%) | 525 of 754 (70%) | 0 | none | 0.06 |
| Classifier (TF-IDF, logistic regression) | 754 | 50 of 754 (7%) | 42 of 50 (84%) | 704 of 754 (93%) | 0 | none | 0.05 |
| Model alone (label names only, no candidates) | 754 | 351 of 754 (47%) | 259 of 351 (74%) | 403 of 754 (53%) | 754 | US$0.29 | 1.50 |
| Model with definitions and candidates | 754 | 249 of 754 (33%) | 179 of 249 (72%) | 505 of 754 (67%) | 754 | US$0.89 | 1.48 |
| Model with definitions and candidates, run 2 | 754 | 256 of 754 (34%) | 186 of 256 (73%) | 498 of 754 (66%) | 754 | US$0.89 | 1.48 |
| Model with definitions and candidates, run 3 | 754 | 259 of 754 (34%) | 189 of 259 (73%) | 495 of 754 (66%) | 754 | US$0.89 | 1.51 |
| Cascade (classifier when sure, model otherwise) | 754 | 262 of 754 (35%) | 191 of 262 (73%) | 492 of 754 (65%) | 704 | US$0.83 | 1.38 |

### Repeat runs

- Model with definitions and candidates, 3 runs on the same 754 issues: labels right 621 to 626 (of 901 to 905 proposed), area right 412 to 419, new issues called duplicates 75 to 82, duplicates found 11 to 11, handled alone 249 to 259.

### Gate rules that fired

- Rules (keywords): TOO_MANY_LABELS 18.
- Classifier (TF-IDF, logistic regression): TOO_MANY_LABELS 2.
- Model alone (label names only, no candidates): LABEL_NOT_IN_TAXONOMY 7, TOO_MANY_LABELS 3.
- Model with definitions and candidates: LABEL_NOT_IN_TAXONOMY 8.
- Model with definitions and candidates, run 2: LABEL_NOT_IN_TAXONOMY 9.
- Model with definitions and candidates, run 3: LABEL_NOT_IN_TAXONOMY 6.
- Cascade (classifier when sure, model otherwise): LABEL_NOT_IN_TAXONOMY 8.

## Development months

- 1,489 issues. 1,310 carry at least one area label of the taxonomy, 499 carry several, 179 carry none.
- 87 were closed as duplicates. The original was resolved for 73; 8 point to another repository, 6 name no single earlier issue. Only resolved duplicates count towards "duplicates found".

### Labels

| Configuration | Labels proposed | Right | Of the maintainers' labels found | Area right | Applied alone | Wrong among those | ...on issues left unlabelled |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rules (keywords) | 1,755 | 924 of 1,755 (53%) | 924 of 2,083 (44%) | 445 of 1,310 (34%) | 992 | 320 of 992 (32%) | 89 |
| Classifier (TF-IDF, logistic regression) | 2,203 | 1,471 of 2,203 (67%) | 1,471 of 2,083 (71%) | 769 of 1,310 (59%) | 369 | 54 of 369 (15%) | 31 |
| Model alone (label names only, no candidates) | 1,927 | 1,416 of 1,927 (73%) | 1,416 of 2,083 (68%) | 928 of 1,310 (71%) | 1158 | 207 of 1,158 (18%) | 101 |
| Model with definitions and candidates (first duplicate instruction, May only) | 436 | 313 of 436 (72%) | 313 of 451 (69%) | 199 of 284 (70%) | 239 | 35 of 239 (15%) | 17 |
| Model with definitions and candidates | 1,648 | 1,287 of 1,648 (78%) | 1,287 of 2,083 (62%) | 890 of 1,310 (68%) | 903 | 143 of 903 (16%) | 74 |
| Cascade (classifier when sure, model otherwise) | 1,650 | 1,288 of 1,650 (78%) | 1,288 of 2,083 (62%) | 892 of 1,310 (68%) | 930 | 148 of 930 (16%) | 75 |

"Area right" counts issues where every area label the desk proposed is on the issue and it proposed at least one, out of the issues the maintainers gave one. "Applied alone" are labels at high confidence. A label on an issue the maintainers left without any label of the taxonomy counts as wrong; the last column says how many of the wrong ones are of that kind.

Per kind of label:

| Configuration | area: right of proposed | area: found of present | platform: right of proposed | platform: found of present |
|---|---:|---:|---:|---:|
| Rules (keywords) | 764 of 1,445 (53%) | 764 of 1,854 (41%) | 160 of 310 (52%) | 160 of 229 (70%) |
| Classifier (TF-IDF, logistic regression) | 1,290 of 1,904 (68%) | 1,290 of 1,854 (70%) | 181 of 299 (61%) | 181 of 229 (79%) |
| Model alone (label names only, no candidates) | 1,216 of 1,551 (78%) | 1,216 of 1,854 (66%) | 200 of 376 (53%) | 200 of 229 (87%) |
| Model with definitions and candidates (first duplicate instruction, May only) | 271 of 369 (73%) | 271 of 397 (68%) | 42 of 67 (63%) | 42 of 54 (78%) |
| Model with definitions and candidates | 1,102 of 1,378 (80%) | 1,102 of 1,854 (59%) | 185 of 270 (69%) | 185 of 229 (81%) |
| Cascade (classifier when sure, model otherwise) | 1,105 of 1,382 (80%) | 1,105 of 1,854 (60%) | 183 of 268 (68%) | 183 of 229 (80%) |

### Duplicates

| Configuration | Called a duplicate | Right | Real duplicate, other issue named | **New issue called a duplicate** | ...of them at high confidence | Duplicates found |
|---|---:|---:|---:|---:|---:|---:|
| Rules (keywords) | 0 | 0 | 0 | **0** | 0 | 0 of 73 (0%) |
| Classifier (TF-IDF, logistic regression) | 78 | 4 | 6 | **68** | 8 | 4 of 73 (5%) |
| Model alone (label names only, no candidates) | 10 | 0 | 0 | **10** | 0 | 0 of 73 (0%) |
| Model with definitions and candidates (first duplicate instruction, May only) | 121 | 5 | 12 | **104** | 3 | 5 of 15 (33%) |
| Model with definitions and candidates | 163 | 16 | 19 | **128** | 17 | 16 of 73 (22%) |
| Cascade (classifier when sure, model otherwise) | 152 | 16 | 18 | **118** | 16 | 16 of 73 (22%) |

The original is among the earlier issues the search returns: first place: 20 of 73 (27%), first 3: 32 of 73 (44%), first 5: 36 of 73 (49%), first 8: 45 of 73 (62%), first 20: 51 of 73 (70%).

### Routing and cost

| Configuration | Issues | Handled alone | ...with nothing wrong | To a person | Answered by the model | Cost per 1,000 issues | Seconds per issue |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rules (keywords) | 1,489 | 439 of 1,489 (29%) | 282 of 439 (64%) | 1,050 of 1,489 (71%) | 0 | none | 0.06 |
| Classifier (TF-IDF, logistic regression) | 1,489 | 116 of 1,489 (8%) | 106 of 116 (91%) | 1,373 of 1,489 (92%) | 0 | none | 0.06 |
| Model alone (label names only, no candidates) | 1,489 | 625 of 1,489 (42%) | 527 of 625 (84%) | 864 of 1,489 (58%) | 1489 | US$0.27 | 1.37 |
| Model with definitions and candidates (first duplicate instruction, May only) | 322 | 82 of 322 (25%) | 72 of 82 (88%) | 240 of 322 (75%) | 322 | US$0.86 | 1.47 |
| Model with definitions and candidates | 1,489 | 470 of 1,489 (32%) | 401 of 470 (85%) | 1,019 of 1,489 (68%) | 1489 | US$0.88 | 1.49 |
| Cascade (classifier when sure, model otherwise) | 1,489 | 504 of 1,489 (34%) | 431 of 504 (86%) | 985 of 1,489 (66%) | 1374 | US$0.81 | 1.38 |

### Gate rules that fired

- Rules (keywords): TOO_MANY_LABELS 36.
- Classifier (TF-IDF, logistic regression): TOO_MANY_LABELS 3.
- Model alone (label names only, no candidates): DUPLICATE_NOT_OLDER 1, LABEL_NOT_IN_TAXONOMY 8, TOO_MANY_LABELS 1.
- Model with definitions and candidates (first duplicate instruction, May only): LABEL_NOT_IN_TAXONOMY 5, TOO_MANY_LABELS 1.
- Model with definitions and candidates: LABEL_NOT_IN_TAXONOMY 15, TOO_MANY_LABELS 3.
- Cascade (classifier when sure, model otherwise): LABEL_NOT_IN_TAXONOMY 15, TOO_MANY_LABELS 3.

## Live

- Live since 2026-11-17. 13 issues answered in 3 runs; 3 of them before the tracker showed a label of the taxonomy or a close.
- Outcomes read so far: 0. An outcome is read 14 days after an issue opens.

No live prediction has an outcome yet.

## Reading these numbers

- The maintainers' labels are the reference, not the truth. They leave some issues unlabelled and miss some duplicates, and both count against the desk.
- A "new issue called a duplicate" may be a real duplicate nobody closed as one. The count is an upper bound on the worst error.
- Backtest titles and bodies are as the tracker shows them today. A few were edited after they were submitted.
- Backtest and live numbers are never added together.
