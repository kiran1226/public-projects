# Discovery notes

> This is a simulated engagement. "Orrinvale" and its product, the Larkspur Engine,
> are fictional, and what its people say below is written for the exercise. The
> tracker the desk is measured on is real: the public issue tracker of the Godot
> engine on GitHub, a product of the same kind. This project is not affiliated with
> Godot, and it writes nothing to that tracker.

## Who and what

Orrinvale maintains an open-source game engine with a public issue tracker. Five
people share the first reading of new issues, next to their other work: two support
engineers and three engine developers on a weekly rota. Discovery was two days with
the support lead and the rota, and an afternoon reading the tracker.

## The request

The support lead, in the first conversation:

> "Every new ticket waits for one of five people to read it, label it and check
> whether we already have it. A good share are duplicates. I want that first pass
> done in a minute, and I want to know how often it is wrong before it touches the
> tracker."

## How a new issue is handled today

1. An issue arrives through the bug-report template: versions tested, system
   information, description, steps to reproduce.
2. Whoever is on the rota reads it when they next look at the tracker.
3. They apply an area label (which part of the engine) and, where the problem is
   specific to one, a platform label. Area labels decide which developers see it.
4. They search the tracker for the same report, by keywords from the title. If they
   find one, they comment with its number and close the new issue as a duplicate.
5. Unclear reports get a question, and the issue waits for the reporter.

## What we observed

Measured on the stand-in tracker, on issues opened May to October 2026 unless a row
says otherwise.

| Observation | Detail |
|---|---|
| Volume | 2,243 issues in six months, about twelve a day |
| Who labels | People. Every label event in two samples was by a person's account: 74 of 74 on 20 issues read before the build, and 359 of 359 on the timelines of the 123 issues closed as duplicates |
| How fast | The area label came a median of 1.4 hours after the issue opened (27 label events on 20 issues, measured before the build) |
| Several areas | 732 of 2,243 issues carry more than one area label |
| No area | 295 of 2,243 carry none: most were closed as duplicates, as not planned, or quickly |
| Duplicates | 123 of 2,243 were closed as duplicates |
| Where the original is recorded | In a comment, in the maintainer's own words. There is no field for it |
| Label descriptions | The area and platform labels have no description on the tracker. What a label means is in the maintainers' heads |
| Titles | Some titles are renamed after an issue is opened: 16 of the 123 issues whose timeline was read |

Things the team said that shaped the design:

- "A wrong area label is worse than none. It sends the issue to people who will
  never look at it again."
- "Do not close anything. If it thinks something is a duplicate, show me the other
  issue and the sentence that matches."
- "Telling someone their new bug already exists, when it does not, is the worst
  thing we can do to a reporter."
- The support lead: "Run it beside us for a month first. I want numbers on issues it
  had never seen, against what we did."

## User stories

| # | As | I need | So that |
|---|---|---|---|
| 1 | a triager | a new issue to arrive with proposed area and platform labels and a confidence for each | I confirm instead of starting from nothing |
| 2 | a triager | a likely duplicate to arrive with up to three earlier issues and the passage in each that matches | I can judge it without searching |
| 3 | the support lead | numbers on issues that arrived after the system was frozen, scored against what the maintainers did | I know how often it is wrong before it acts |
| 4 | the support lead | the system to write nothing to a tracker unless that tracker is named in its configuration | a test run cannot touch the real tracker |
| 5 | the owner of the service | to know when the feed broke, when a run was missed and what yesterday cost | it can run unattended |

## Success criteria

Agreed with the support lead before the build, as counts on the held-out months:

- Labels the desk would apply on its own are wrong in at most 1 of 20 cases.
- It proposes the right area for more issues than the keyword rules and than the
  classical classifier.
- A new issue is called a duplicate at high confidence in at most 1 of 100 issues.
- The whole first pass costs under one US dollar per 1,000 issues.

The evaluation report says which of these were met.

## Out of scope

Pull requests, replying to the reporter, priority, assigning people, fixing the bug,
and any decision to close.

## Open questions

- The reference is what the maintainers did. They miss duplicates and label
  unevenly, so some of the desk's "errors" are theirs. Nobody has read a sample of
  disagreements to say how many.
- Whether the taxonomy holds as the product changes: a new area label means a
  rebuild, which the health check flags but does not do.
- Whether reporters would act on a "this looks like a duplicate" comment, or whether
  it should only ever be shown to the triager.
