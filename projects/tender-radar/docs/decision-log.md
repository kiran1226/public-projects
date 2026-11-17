# Decision log

Choices made while building the radar, with the alternative that was considered and
the trade-off accepted. Numbers quoted here come from the development months (tenders
first published in April and May 2026) unless a line says otherwise. The
[evaluation report](eval-report.md) has the full tables.

## 1. Score against who won, not against labels written for the project

**Decision.** The evaluation asks one question: of the tenders a real supplier went on
to win, how many were at the top of the list the system wrote for that supplier on
the day the tender was published. The answer comes from award notices in the same feed.

**Alternative.** A hand-labelled set of 40 to 60 tender and profile pairs, as in the
other projects in this repository.

**Why.** Awards are free, public and written by someone else. They give 1,003 outcomes
for 200 suppliers instead of 40 labels, and nobody chose them with the system in mind.

**Trade-off.** A win is the only outcome on record. A tender a supplier bid for and
lost was relevant and counts here as clutter. So the figures are a lower bound on how
useful a list is, and precision cannot be measured at all.

## 2. A strict split in time, profiles frozen at the cut

**Decision.** A profile is built from awards published before 1 April 2026. Test
tenders were first published from 1 April to 30 June. June was not looked at until
every setting below was fixed.

**Why.** The live system only ever knows the past. A backtest that lets a profile see
one award from the test window would flatter every ranker.

**Trade-off.** Supplier identity is resolved once, over all awards, including later
ones. That affects which earlier wins are grouped under one supplier, not what a
ranker sees about a test tender. It is still a small use of later data, and it is said
here.

## 3. The backtest is a replay through the live pipeline

**Decision.** `replay` feeds stored history, one publication day at a time in date
order, through `process_day`, the function the morning run calls.

**Alternative.** A separate evaluation script that scores rankers on a matrix of
profiles and tenders. It would have been faster to write.

**Why.** Two code paths drift apart. The filters, the list size, the tie-breaking and
the log format in the report are the ones that run every morning.

## 4. The unit is the procedure, on the day it was first published

**Decision.** A procedure can have several competition notices: corrections and new
versions. In division 38, 3,925 notices from April to June belonged to 2,975
procedures. The first notice sets the day; later ones are not listed again.

**Why.** That is the day a bid desk could first have seen the tender. Listing each
correction would also let a ranker "find" the same tender several times.

## 5. Supplier identity by clustering, with numbers paired only in single-winner awards

**Decision.** The feed has no supplier key. Buyers type the winner's name and
registration number. Names are normalised (case, accents, legal form) and joined to
numbers (prefixes such as `NIP` or `DE` removed) in one union-find structure. A name is
paired with a number only when an award names exactly one winner and gives exactly one
number. A number that appears under unrelated names links nothing.

**Alternative.** Pair the name list and the number list by position, which is what the
equal length of the lists invites.

**Why.** In three months of awards, 913 numbers appeared with more than one name, and
for 812 of them the names were unrelated companies: the two lists are not in the same
order. After the change, numbers shared by unrelated names fell from 4,096 to 287 over
the full history. A spot check of merged clusters found spelling variants of one
company; a spot check of refused merges found different companies.

**Trade-off.** The guard is conservative. A company that buyers spell in very
different ways is split into several suppliers and falls below the panel's threshold.
Some wins are attributed to the wrong supplier or to none.

## 6. The panel: companies with history, taken country by country

**Decision.** A supplier is on the panel if its name carries a legal form, it won at
least five procedures before the cut and at least two test tenders. Of 33,868
suppliers found, 1,550 qualify. Two hundred are taken, country by country in turn, in
the order of their hashed ids.

**Why.** The legal-form test keeps sole traders out, whose names are personal data.
Five wins are needed to say what a supplier sells. Taking countries in turn stops
Poland, with a quarter of all notices, from filling the panel.

**Trade-off.** The panel is made of active suppliers, so it says nothing about a
company bidding for the first time. Small countries weigh more than their share of
tenders, and a small country has few tenders a day, which makes every ranker look
better in the first 15 places. The first one, three and five places are the figures
to read.

## 7. The baseline is the category alert at five digits

**Decision.** The baseline lists every tender that shares the first five digits of a
category code with a category the supplier has won in, in publication order.

**Why.** It is what the bid desk has today. Three and eight digits were also scored
and were no better in the first three places (53% and 56% against 56%), so the
baseline was given its best setting.

## 8. The fused ranker is categories plus embeddings; two signals were left out

**Decision.** The fused ranker combines weighted category match and multilingual
embeddings by reciprocal rank. Word matching and buyer history are scored on their own
and are not part of it.

**Why.** On 733 tenders later won, in the first three places:

| Ranker | In top 1 | In top 3 |
|---|---|---|
| Category alert | 28% | 56% |
| Weighted categories | 37% | 68% |
| Word matching (TF-IDF on character n-grams) | 35% | 60% |
| Multilingual embeddings | 39% | 65% |
| Categories and embeddings, fused | 42% | 72% |
| The same with word matching added | 43% | 69% |
| The same with buyer history added | 38% | 66% |

Word matching only works inside one language and repeats what the category codes
already say. Added to the fusion it moved the first place up by one point, lost three
points in the first three places, and left 25 later-won tenders off the list instead
of 15. Buyer history looked promising: the buyer was one the supplier had won
from before for 62% of the tenders it went on to win, against 19% of the other tenders
on its lists. But a supplier's regular buyers publish many tenders, most of them for
something else, and promoting all of them pushed the right one down. A small bonus for
a known buyer moved the top-1 figure by about one point, which is inside the noise, so
it was left out.

**Trade-off.** Buyer history is real information that this ranker does not use. A
learned ranker over all four signals is the obvious next step and is listed in the
README.

## 9. A tender is compared with the past win it most resembles

**Decision.** The text score of a tender is its similarity to the single nearest past
win, not to the average of all wins or of the nearest three.

**Why.** A distributor wins reagents and instruments, which look nothing alike. The
average blurs both lines. The nearest win had 72% in the top three; the mean of the
nearest three had 71%.

## 10. A small multilingual embedding model on a CPU

**Decision.** `paraphrase-multilingual-MiniLM-L12-v2` through fastembed: 384
dimensions, about 220 MB, Apache-2.0, no key. Vectors are cached on disk.

**Why.** Notices are written in the buyer's language only. Before relying on it, the
model was tried on one tender text written in five languages: the five scored 0.81 to
0.84 against each other and 0.59 against a different product. It embeds about 400
texts a second on a laptop, so a day's tenders take under a second.

**Trade-off.** It reads only the start of a notice (700 characters). A long tender
whose subject appears late is judged on its title and first sentences.

## 11. The model reads few tenders, returns structure, and quotes

**Decision.** Claude reads the first ten tenders of the client's list. It returns a fit
from 0 to 100, one of bid, skip or ask a person, a reason, the passage the reason
rests on, and up to five conditions, each with its passage. It returns no deadline and
no value.

**Alternative.** Let the model rank the whole day, or write a free-text summary.

**Why.** A day has about 190 tenders in scope. Reading all of them for every profile
would cost about twenty times as much and the free rankers already put the right one
in the first five places four times out of five. Structure makes every claim
checkable. The deadline and the value are in the notice's own fields, so a model has
nothing to add there and one more way to be wrong.

## 12. A gate between the model and the list

**Decision.** Code checks each quoted passage against the notice text the model was
shown, ignoring spacing, case and outer punctuation. A condition whose passage is not
there is dropped and counted. A decision whose passage is not there becomes "ask a
person".

**Why.** The worst thing the model can do is take the right tender off the top of the
list. A "skip" must rest on words that are in the notice. The same check is the
defence against an instruction planted in a notice: whatever the model was talked
into, it cannot justify it with a passage that exists.

**Trade-off.** A correct judgement with a slightly misquoted passage is downgraded
too. That costs the bid manager one look.

## 13. Cheapest model first, at low effort

**Decision.** Claude Haiku 5.5 with effort set to low. A larger model would need to
earn its place on the sample in the evaluation report.

**Why.** The task is reading one notice against one profile. Measured cost is about
US$0.0006 per assessment, so ten assessments a morning cost under a cent.

## 14. The model is scored on a sample of supplier-days

**Decision.** In the backtest the model runs for the first 100 panel suppliers by
hashed id, on up to three days each on which a tender they later won was published.
The free rankers are scored on the same supplier-days next to it.

**Why.** Running it on all 12,000 supplier-days would cost about US$40 and answer the
same question. Days with a later win are the ones where an order can be right or
wrong.

**Trade-off.** The sample cannot say how many tenders the model would mark "skip" on
an ordinary day for an ordinary profile; the fixture day gives one example of that.

## 15. Hard rules never remove a tender for a missing field

**Decision.** Four named rules: deadline too close, outside the territory, value
outside the band, excluded procedure or contract type. A tender with no deadline, no
value or no country passes.

**Why.** Half the notices state no value. Treating missing as failing would hide half
the feed.

**Trade-off.** For a panel supplier the territory is the set of countries it has won
in. A supplier that wins in a new country is never shown that tender. The evaluation
counts those on their own line.

## 16. Append-only logs, hashed ids, a version on every row

**Decision.** Lists and outcomes are JSON lines that are only appended to. A list
carries the profile's hashed id, the day, the ranker, the system version and the time
it was written. Supplier names and numbers never leave the local database; committed
files carry a hash.

**Why.** The log is the evidence. If rows could be rewritten, a score computed from
them would prove nothing. The version, a hash of settings, prompt and model, puts a
changed system on its own line of the scoreboard.

## 17. A publication day is listed once, and only when it is over

**Decision.** The morning run lists the publication days up to yesterday. The first
run lists only the latest complete day.

**Alternative.** List today's issue as soon as it appears.

**Why.** A day is processed once. If the run started while the day's issue was still
arriving, the tenders that came later would never be listed. Lists for older days,
written long after publication, would not be honest predictions either.

**Trade-off.** The bid desk reads yesterday's tenders this morning. Deadlines are
weeks away, so a day costs nothing.

## 18. The scheduled job carries its state in small files

**Decision.** The job has no copy of the 2.3 GB history database. It needs the panel
profiles, a table of hashed supplier aliases, and a day index that records which
tenders were published on which day, without their text. Results go to a `live-data`
branch, never to `main`.

**Why.** A runner starts empty. With these files the job can list today's tenders and
later recognise a panel supplier in an award notice, from 14 days of feed.

## 19. What is committed

**Decision.** Ids, lists, outcomes and scores. One day of real notices as a fixture,
with a `NOTICE` file. No winner names, no registration numbers.

**Why.** TED's legal notice allows free reuse of procurement notices. The feed sends a
`links` field of document URLs whether asked or not; it is dropped. One buyer had typed
an email address into a description; addresses in free text are replaced before the
fixture is written.

## 20. A daily spend cap in code, and a polite feed client

**Decision.** The cap (US$0.50 a day by default) is read from the log before every
model call. At the cap the list is written without assessments and the skips are
counted. The feed client sends one request at a time, 1.2 seconds apart, with backoff.

**Why.** The job runs unattended. A burst of about ten requests in a few seconds was
refused by the feed during the build; this spacing never was.

## 21. Scope: three category divisions

**Decision.** Divisions 24 (chemical products), 33 (medical equipment and
pharmaceuticals) and 38 (laboratory, optical and precision equipment).

**Why.** The whole feed is about 3,100 notices a day. These three are what the
scenario client would follow, and they hold 126,452 awards since January 2025, enough
for a panel.

**Trade-off.** Every number in the report is for these divisions. Construction or IT
services may behave differently.

## 22. The held-out result on the model was reported, not tuned away

**Decision.** On the development sample the model moved the later-won tender into
first place in 52% of cases against 41% without it, and marked 12 of 258 later-won
tenders "skip". On the held-out sample the first-place figure was 50% against 46%, and
it marked 18 of 176 "skip". The prompt, the profile text and the model were left as
they were, and the rollout gate on the model is recorded as not met.

**Alternative.** Change what the model is shown (for example a supplier's categories
and more of its history), or try a larger model, and score June again.

**Why.** A second look at June after a change made because of June would not be a
held-out result. The honest next measurement needs a month the system has not seen,
which the live log will supply.

**Trade-off.** The repository ships a model step whose "skip" is wrong about one time
in ten on later-won tenders. The design already limits the damage: a "skip" moves a
tender to the bottom of the list and never removes it, and the list's order without
the model is the one the headline numbers are about.
