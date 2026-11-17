# Decision log

Choices made while building the cascade, with the alternative that was considered and
the trade-off accepted. Decisions 1 to 12 were made before the evaluation; their
numbers come from an experiment run before the build (8,000 edits of 11 and 12
November 2026) or from the first probes of the build. Decisions 13 to 22 came out of
the evaluation, and their numbers are this system's.

## 1. Score against which edits were reverted, not against labels written for the project

**Decision.** An edit counts as bad if it carries MediaWiki's tag `mw-reverted` at
least 48 hours after it was made.

**Alternative.** A hand-labelled set of a few hundred edits.

**Why.** The outcome is free, it keeps arriving, and nobody chose it with this
system in mind. Before the build the tag was checked against page histories on 116
examples and agreed on all of them.

**Trade-off.** "Reverted" is not "vandalism". Good-faith mistakes are reverted, some
reverts are disputes between editors, an editor undoing their own edit counts, and
damage nobody noticed counts as fine. Every table says so.

## 2. A split in time, on sampled history

**Decision.** Three weeks of history, 25 October to 15 November 2026, sampled as 500
edits every two hours. Tier 1 trains on the first two weeks. The test week is split
into five development days and two held-out days, which are to be scored once.

**Alternative.** Every edit of three weeks, as the brief first assumed.

**Why.** The brief estimated 30,000 edits a day from a 49-second sample. An
experiment before the build measured about 88,000 a day by people. Three weeks in
full is two and a half million edits and 5,000 requests; the sample is 262 requests
and still gives tens of thousands of edits with outcomes.

**Trade-off.** A slice is a few minutes of traffic. Runs of edits to one page that
span a slice boundary are cut.

## 3. Tier 1 orders the queue and never clears an edit

**Decision.** Tier 1 gives every edit a place in the queue and decides which edits
a model reads. It has no "clearly fine" band.

**Alternative.** Three bands as in the brief: clearly fine, unclear, clearly bad,
with the model on the unclear ones.

**Why.** The experiment before the build showed that the event's own fields rank
edits and cannot clear them: the safest half of the queue by tier 1 still held 26
of 161 reverted edits. A band named "clearly fine" would be a promise the data does
not keep.

## 4. The model reads the top of the queue, not the middle

**Decision.** The model reads the riskiest 20% of edits by tier-1 score.

**Why.** In the experiment the model found little in the lowest third of the queue
and sharpened the top. Reviewers work from the top, so that is where a second
opinion changes what they see.

**Trade-off.** A reverted edit that tier 1 ranks low is never read by the model. The
evaluation counts those on their own line. This turned out to be the largest
trade-off in the project: see decision 17.

## 5. Tier 1 uses only what the stream event carries

**Decision.** Twenty features, all computed from the event: size change, the edit
summary's shape, whether the editor is logged in, the minor flag, time of day, the
gap to the page's previous revision, the title's shape.

**Alternative.** Add the tags MediaWiki attaches to an edit, which the
recent-changes list returns and which carry signal.

**Why.** The live stream event has no tags. A feature the live run cannot see would
make the backtest flatter the system.

**Trade-off.** Tier 1 is weaker than it could be with a second request per edit.
That is the design: tier 1 costs no request.

## 6. Three fields and no sentence from the model

**Decision.** The model returns a risk from 0 to 100, a category from a fixed list
of seven, and a range of line numbers. The page words the reason from the category.

**Alternative.** A level and a sentence of explanation, as in the experiment.

**Why.** Three reasons. Offensive text stays out of the recordings, which can
therefore be committed. An instruction hidden in an edit has nothing to steer but
three fields, each checked by the gate. And the answer is about 30 tokens, which is
most of why a call costs a fraction of what it did in the experiment.

**Trade-off.** A reviewer gets "Looks like vandalism" and marked lines, not an
argument.

## 7. A line range, not a character range

**Decision.** The change is shown to the model as numbered lines, and it marks the
risky part by first and last line number.

**Alternative.** Character offsets, as the brief wrote.

**Why.** Models count characters badly. A line number is something the model can
read off the prompt and the gate can check exactly.

## 8. The change is computed here, from two revision texts

**Decision.** `diff.py` reads the text of a revision and of its parent, 50 revisions
to a request, and compares them locally.

**Alternative.** The wiki's own diff, one request per edit.

**Why.** The wiki API refused the eleventh request of a burst and accepted one
request every five seconds. At that pace one request per edit covers about 17,000
edits a day, which is what a 20% slice of the stream needs with nothing to spare.
Two revisions per edit, 50 to a request, is 25 edits a request.

## 9. No account names, anywhere

**Decision.** No record has a field for who made an edit. The account name in a
stream event is read once, to tell a temporary account from a registered one. Edit
summaries are scrubbed of user links, temporary-account names, IP addresses and the
name after "by" in a revert summary before they are stored. The summary of an edit
that is itself a revert is not shown to the model at all.

**Why.** The policy owner's requirement: the system ranks edits, not people. In the
experiment a scrubber that removed only links let 7 to 11 names through in revert
summaries.

**Trade-off.** Who made an edit is the strongest signal there is (Wikimedia's own
model uses the editor's history). This system gives it up.

## 10. Requests are serial, paced and identified without contact details

**Decision.** One request to the wiki API every five seconds, one at a time, with
`maxlag`. The User-Agent names the client and the project. It carries no contact
address.

**Why.** Wikimedia's policy asks for a client name and a way to reach the operator.
The repository had no public URL during the build, and an email address is not
something this project adds on its own. `EDITWATCH_USER_AGENT` sets it.

**Trade-off.** An unidentified client may be limited or blocked without notice. The
client stops when told to wait more than two minutes.

## 11. A daily cap spent evenly, and a bounded queue

**Decision.** The spend allowance grows through the day: half the cap is available
at noon. Between the tiers at most 50 edits wait and 4 model calls run at once. An
edit that finds the queue full or the allowance used keeps its tier-1 score, and the
skip is counted.

**Alternative.** A plain daily cap, and an unbounded queue.

**Why.** A plain cap is gone in the morning of a busy day. An unbounded queue hides
overload as latency: edits would reach the reviewers minutes late with nothing
saying so. A bounded queue turns overload into a number.

**When this stops being enough.** One process, one machine. Above a few edits a
second through the model tier, or with more than one reader, the queue belongs in a
broker and the fetch in its own worker.

## 12. The load test is a simulation, and says so

**Decision.** The load test replays a real ten-minute recording of the stream on a
virtual clock. Request and model times are drawn from times measured in this
project: 120 requests to the wiki and 694 model calls. The admission policy is the
same object the live run uses.

**Alternative.** Replay the recording against the real services at 10 or 50 times
the rate.

**Why.** Fifty times the stream's rate against a wiki that refuses a burst of eleven
requests, and against a paid model, is neither polite nor affordable. The waiting is
what the test is about, and waiting can be computed.

**Trade-off.** It shows what the policy does under load, not what the network does.

## 13. The differing part of a changed line is marked

**Decision.** Where an edit changes a line, both versions are shown with the part
that differs enclosed in ⟦ ⟧, widened to whole words, and a long line is cut down
to that part with some context.

**Why.** Most edits change a few words inside a paragraph. Without the marks a
reader, or a model, has to find them. The marks also make the direction of a change
plain: what is inside the marks of the removed line is gone.

**What it did not fix.** Claude Haiku 5.5 still rates an edit that removes an
insult about as high as the edit that added it (85 and 88 on the two fixture
edits), with the marks and with an instruction that removing bad text is
constructive. Sonnet and Opus rate the removal 2 and the addition 95. Changing the
words "-" and "+" to "removed" and "added" made no difference to Haiku either.

## 14. Tier 1 scores edits in batches

**Decision.** The live loop collects the edits that arrived since its last pass and
scores them in one call.

**Why.** Measured on the recording: one call to the classifier takes 45
milliseconds for one edit, 51 for five and 80 for fifty. Scored one at a time, tier
1 would itself be the bottleneck at about twenty edits a second.

## 15. The model sample, and a smaller slice for the most expensive model

**Decision.** 1,500 edits drawn at random from the development days and 1,500 from
the held-out days. Haiku reads every one of them, which gives the "model on every
edit" line and, by setting answers aside, every smaller slice. Sonnet reads the
riskiest 20% by tier 1. Opus reads the riskiest 10%.

**Why.** A call on a real edit is about 1,400 tokens in and 36 out: US$0.00016 for
Haiku, US$0.0032 for Sonnet, US$0.0066 for Opus. The budget for the whole
evaluation was US$5. Opus on the same 20% slice would have taken it past that.

**Trade-off.** Opus and Sonnet can be compared only on the top 10% and below, and
on a few hundred edits. One run each; Haiku's cascade has three.

## 16. One number from two tiers, fitted on the development days only

**Decision.** For an edit the model read, the queue's risk is a logistic function of
the tier-1 score and the model's 0 to 100 answer. Its three numbers are fitted per
model on the development sample and committed in `data/combiners.json` before the
held-out days are run.

**Alternative.** Put every edit the model rated high above every edit it did not
read.

**Why.** The two scores carry different information: tier 1 knows the event, the
model knows the text. The fitted weights say how much each is worth, and the result
is a probability that can be compared with the tier-1 score of an edit no model
read.

## 17. What the held-out days said, and what was not changed afterwards

**Result.** On 1,500 held-out edits with 74 reverted, the cascade with Haiku put 16
to 17 reverted edits in the top 5% of the queue over three runs. Tier 1 alone put
14 there, Sonnet's cascade 20, Haiku on every edit 23, Wikimedia's own model 17.
The slice the model reads held 28 of the 74 reverted edits; the other 46 never
reached a model.

**Decision.** Nothing was changed after these numbers were read. The share stays at
20%, the two weights stay as fitted on the development sample, the prompt stays.

**Alternative.** Raise the share to 40%, where the same answers give 20 in the top
5%, or refit the weights on both samples.

**Why.** The held-out days are the only edits no setting has seen. A setting chosen
on them would turn them into development data and leave nothing to check it against.

**What it means.** The cascade is limited by its cheap tier. That was not expected:
decision 4 assumed that most bad edits would be in the slice. The next build step is
tier 1, not the model.

## 18. The live model is Haiku on the riskiest fifth

**Decision.** The live loop runs Claude Haiku 5.5 on the riskiest 20% of edits by
tier 1, under a cap of US$1.00 a day. The cap was US$0.50 until the first live run:
a ten-minute window cost 2.7 US cents, so 24 windows cost 65 cents, and the old cap
would have refused a quarter of the slice every day. A cap is there to stop a
runaway, not to starve an ordinary day.

**Alternatives.** Sonnet on the same slice. Haiku on every edit. Tier 1 alone.

**Why.** Cheapest first, and a larger model only if the numbers pay for it. Sonnet
put one to six more reverted edits at each cut of the queue than Haiku did, on one
run, at twenty times the cost per call: about US$13 a day for ten minutes of every
hour, against 65 US cents. Haiku on every edit was the best order measured, and it
is what the client ruled out; it is reported, with its cost, as the thing to
reconsider. Tier 1 alone would be the plain choice on the held-out numbers. The
cascade runs live anyway because the live log is the larger sample: about 17,000
edits a day with outcomes, where the evaluation had 1,500. If the cascade is not
ahead of tier 1 there, the model tier goes.

**Trade-off.** The live system is one the evaluation could not show to be better
than its own first tier.

## 19. A latency target of 15 seconds, and a pause that stays at five

**Decision.** An edit the model reads should be in the queue within 15 seconds of
its event, at the 95th percentile. The pause between requests to the wiki stays at
five seconds after each answer.

**Result.** 7.1 and 7.8 seconds in the second and third live runs, with medians of
4.5 and 4.0, and 6.8 in the load test at the stream's rate. The first live run took
17.9 seconds because of a defect (decision 21). In the load test the target holds
at twice the stream's rate and is missed at five times (16.4 seconds).

**Why the time is what it is.** It is set by the wiki, not the model. The client
sends one request at a time and waits five seconds after each answer. The model
call took 0.64 seconds.

**Alternative.** A shorter pause would cut the median roughly in proportion. Before
the build a burst of eleven requests was refused and one request every five seconds
was accepted; nothing in between was tried, and this client gives the wiki's
operators no address to complain to. The polite setting stays until someone who can
be reached runs it.

**Trade-off.** "Within seconds", as the client put it, holds for tier 1's order,
which is there in under a second. The model's second opinion arrives about four
seconds later.

## 20. The load test: every edit, ten replays, the client's pacing, live request times

**Decision.** Four changes to the load test after the first results.
`editwatch loadtest --every` replays the recording with every edit sent to the
model. Each speed is replayed ten times with a new draw of request and model times,
and the replays are pooled. The simulated client waits five seconds from its last
answer, as the real one does. And a request for up to five edits draws its time
from requests that live runs made, where a larger one draws from full batches.

**Why.** Once "Haiku on every edit" was the best order, the question was whether it
keeps up. It does at the stream's rate, at 15.4 seconds at the 95th percentile; at
twice the rate it skips 6 of 695 edits and at three times 85, where the cascade
skips nothing until ten times. That headroom is what the cascade buys on this
stream. One replay was not enough to say so: its 95th percentile moved between 13
and 22 seconds with the draw. The pacing and the request times were both wrong in
the first version, and the live runs showed it: the first was slower than the
simulation said, and after the fix the second was twice as fast as it said. A
request for one or two edits takes 0.47 seconds at the median, one for 25 edits
1.5 seconds.

## 21. The request to the wiki has a thread of its own

**Decision.** The live loop hands each request for what edits changed to a second
thread. One request runs at a time.

**Why.** The first live run made the request inside the loop that scores edits.
While a request ran, or waited its five seconds, nothing was scored: tier 1 placed
an edit after 1.0 seconds at the median and 5.6 at the 95th percentile, where the
scoring itself takes 50 milliseconds, and the model tier took 17.9 seconds at the
95th percentile. In the next run tier 1 took 0.4 and 0.8 seconds and the model tier
7.1. A test now holds a request open and checks that an edit arriving meanwhile is
placed.

## 22. What was never run, and is said so

**Decision.** The README, the runbook and the scoreboard name what was not run: the
scheduled workflow on GitHub, the Docker image, the review page with a reviewer, a
second wiki, and a full day under the spend cap.

**Why.** The repository had no remote during the build, and a portfolio that claims
a schedule it never ran is worth less than one that says so.
