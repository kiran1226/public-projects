# Discovery notes

> This is a simulated engagement. "Tarnwell Lab Supply" is a fictional distributor of
> laboratory equipment, and what its people say below is written for the exercise.
> The tenders, the awards and the suppliers used for scoring are real: they come from
> TED, the EU's public procurement journal.

## Who and what

Tarnwell sells and installs laboratory instruments made by others, and supplies the
reagents and consumables that run them. Its customers are hospital, university and
public-health laboratories in Germany, Austria, Poland and the Czech Republic. Most
of that business is won through public tenders. A bid desk of three people finds the
tenders, decides which to answer and writes the bids. Discovery was two days with the
bid desk, and conversations with the sales director and one account manager.

## The request

The sales director, in the first conversation:

> "We find tenders through category alerts. We get two hundred a day, read ten, and
> hear about the ones we should have bid on when a competitor wins them. I want a
> short list every morning, with the deadline and what would rule us out."

## How tenders are found today

1. Each bid manager has saved searches on the tender portal, by category code and
   country. The portal sends a daily email per search.
2. The emails list every notice that carries one of the codes, in the order the
   notices were published. There is no ranking.
3. A bid manager reads titles, opens the ones that look right, and reads the notice
   in the buyer's language: German, Polish or Czech.
4. For a notice that fits, the bid manager reads the conditions for tenderers:
   minimum turnover, certificates, references. This is where most tenders are dropped.
5. The tender goes on a shared spreadsheet with its deadline. The sales director
   decides at a weekly meeting which to bid on.

## What we observed

| Observation | Detail |
|---|---|
| Volume | A broad category code brings in far more notices than anyone reads. A narrow one misses tenders filed under a neighbouring code |
| Order | The alert is in publication order. The tender that matters is as likely to be last as first |
| Language | Notices are written in the buyer's language only. The Czech and Polish notices are read last, or not at all in a busy week |
| Buyers | The desk recognises its own customers by name. A tender from a hospital it already supplies is read first, when someone notices it |
| Conditions | A third of the reading time goes on tenders that a single stated condition rules out, such as a certificate the company does not hold |
| Deadlines | Kept in a spreadsheet, typed by hand from the notice |
| Feedback | Nobody records why a tender was skipped. The same kind of tender is opened and dropped again the next month |

Things the team said that shaped the design:

- "I do not need it to decide. I need it to put the five I should read at the top."
- "If it tells me a tender needs a certificate, I want to see the sentence."
- "The deadline has to be right. If it is wrong once, I go back to the portal."
- The sales director: "Show me that it would have found the ones our competitors won.
  Otherwise it is just a nicer email."

## User stories

The request and the observations, written as what each person needs. The last two
columns say which part of the system answers the story and where the
[evaluation report](eval-report.md) checks it.

| # | Story | Answered by | Checked by |
|---|---|---|---|
| 1 | As the bid manager, I get a ranked list each morning of at most 15 new tenders that fit what we sell, each with the reason it is on the list | Hard rules, then rankers over categories, text meaning and buyer history; the model's reason for the first ten | Share of later-won tenders in the first 1, 3 and 5 places |
| 2 | As the bid manager, for each tender I see the deadline, the value, the lots and the conditions we would have to meet, each tied to the place in the notice it came from | Deadline and value from the notice's own fields; conditions from the model, each with a quoted passage that the gate checks | Conditions kept and dropped by the gate; the hand-read sample |
| 3 | As the sales director, I see that the list beats our category alerts, measured on tenders real suppliers went on to win | The backtest: 200 real suppliers, profiles frozen before the test window, awards as outcomes | The alert row against the fused row, on the held-out month |
| 4 | As the bid manager, I mark each tender "bid" or "skip" with a reason, and those decisions are kept | The morning list page and the decision log | Tests of the decision log |
| 5 | As the owner of the service, I know when the feed broke, when a run was missed and what yesterday cost | Feed health checks, the daily spend cap, the scoreboard | Tests of the health checks and the cap; the live run |

## Success criteria

Agreed with the (fictional) sales director:

1. On tenders that real suppliers went on to win, the list puts more of them in its
   first three places than the category alert does, on a month not used for tuning.
2. No tender is taken off the list by the model on the strength of a passage that is
   not in the notice.
3. The deadline and the value shown are the notice's own.
4. The service costs less per day than ten minutes of a bid manager's time.
5. A broken feed fails the run. It never produces an empty list that looks normal.

## Out of scope

- Downloading the tender documents from buyers' portals. The radar reads the notice.
- National portals below the EU threshold.
- Writing the bid.
- Email notifications and login.

## Open questions

- Which tenders did Tarnwell bid on and lose? Only wins are public. With the desk's
  own bid history the list could be scored on what the desk considered relevant, not
  only on what it won.
- Should a known buyer always be shown, even when the tender is outside the usual
  categories? The desk said yes. The first version ranks it higher but does not force it.
- How far ahead of the deadline does the desk need a tender? The profile says ten
  days. The desk had no firm number.
