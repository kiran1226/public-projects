# Runbook: edit moderation cascade

For the person who keeps the live loop going. Commands are run in
`projects/edit-moderation-cascade`. The wiki watched is `wiki_host` in
`src/editwatch/config.py` (`en.wikipedia.org`): edits to articles, made by people.

State on 17 November 2026: the image has never been built and the workflow has never
run on GitHub. Two windows were run by hand that day, and the figures on this page
are from them. Where this page says "not measured", nobody has run it yet.

## What runs and when

One pass of the loop is four commands: check the stream, order ten minutes of it,
read whether the edits ordered 48 hours ago were reverted, write the scoreboard. The
system only orders a review queue. It reads Wikimedia's public stream and API and
writes only its own files. The loop is scheduled in one of two places. Use one, not
both: each keeps its own store and its own copy of the logs, and two clients ask
Wikimedia twice.

- **GitHub Actions**, `.github/workflows/live-edit-moderation-cascade.yml`: every hour
  at minute 43. Each run reads the stream for ten minutes, so ten minutes of every
  hour are ordered and the other fifty are not. It writes to the `live-data` branch,
  never to `main`. GitHub can start a scheduled run several minutes late.
- **Docker Compose**, `compose.yaml`: on any machine, continuously. A window starts as
  soon as the pass before it has finished. The stream is not read while the other
  three commands run, so not all of it is ordered. How much is left out was not
  measured: the Compose loop has never run. It writes to the working tree and
  serves the review page.

Nothing in either schedule can write to a wiki. See the last section.

## First-time setup

On your own machine or the machine that runs Compose:

```bash
uv sync --all-extras                 # --all-extras adds Streamlit for the review page
uv run editwatch backfill            # three weeks of sampled history with outcomes
uv run editwatch train               # tier 1 on the edits before the cut
uv run editwatch health              # the stream and the wiki answer
uv run editwatch run --minutes 10    # the first window
```

- `backfill` takes about 25 minutes: 268 requests to the wiki's API, one every five
  seconds. It remembers each slice it has fetched, so it can be stopped and started
  again. The store is `var/editwatch.db`, about 60 MB. It is never committed.
- `train` trains the tier 1 of the backtest on the edits made before `cut` and writes
  `data/tier1.json`: the edits and the reverted edits in each window, which the
  scoreboard shows. It takes a few seconds.
- The live loop has a tier 1 of its own. The first `run` trains it on all the stored
  history, prints `training the live tier 1 on N edits` and keeps it as
  `var/tier1-live-*.pkl`. Later runs load that file. The training took about six
  seconds on the build machine.

Both of these are optional:

- `ANTHROPIC_API_KEY`, in the environment or in a `.env` file here or in a parent
  folder. Without it `run` prints `No model key found: running tier 1 alone.` and
  orders the queue with tier 1 only. The cost is then zero.
- `EDITWATCH_USER_AGENT`. Set it before the first request if you can. See "The
  User-Agent".

Under Compose the same two commands run in the container, once:
`docker compose run --rm watch editwatch backfill`, then
`docker compose run --rm watch editwatch train`.

On GitHub Actions:

1. Push the repository. The schedule only runs from the default branch, `main`.
2. Optional: add the key as a repository secret named `ANTHROPIC_API_KEY`, and the
   repository variables `EDITWATCH_USER_AGENT` and `EDITWATCH_DAILY_CAP_USD` (Settings,
   Secrets and variables, Actions).
3. In the Actions tab open `live-edit-moderation-cascade`, press Enable workflow if
   GitHub shows that button, then Run workflow. The first run finds no store in the
   cache, so it runs `backfill` itself, about 25 minutes, and `run` then trains the
   live tier 1. It also creates the `live-data` branch if it is not there. No branch
   rule may block GitHub Actions from pushing to it.

The workflow does not run `train`. The live loop does not need it.

## The loop, command by command

| Command | What it does | What it writes |
|---|---|---|
| `editwatch health` | Listens to the stream for 20 seconds, then asks the wiki for the tags of the newest edit it heard. Five checks. Exits 1 if one fails. | `var/scoreboard/health.json` |
| `editwatch run --minutes 10` | Reads the stream from now for ten minutes and keeps the article edits by people. Tier 1 scores each edit at once. The riskiest 20% wait for the model: one request to the wiki at a time, five seconds after the last answer, reads what up to 25 of them changed, then the model reads each change and the gate checks its answer. An edit that finds the queue full or the day's allowance used keeps its tier-1 score. | During the window, in the store: each edit heard and what each fetched edit changed. The model's answers go to `var/cassettes/live.jsonl`. As the window goes: one row per edit appended to `data/log/live.jsonl`. At the end of the window: one line to `data/log/live-runs.jsonl`, and how long the requests to the wiki took to `data/latencies.json`, for the load test. |
| `editwatch truth` | For each logged edit that is 48 hours old and has no outcome yet: reads its tags, 50 revisions to a request. The edit counts as reverted if it carries `mw-reverted`. | Appends to `data/log/outcomes-live.jsonl` |
| `editwatch scoreboard` | Scores the logs. Uses no network. | `var/scoreboard/scoreboard.json`, `var/scoreboard/index.html` |

If `health` fails, `run` and `truth` are skipped for that pass and the scoreboard is
still written, so the failed check is on it. If `run` fails, `truth` is skipped. A
skipped `truth` loses nothing: the next one reads every outcome that is due.

A normal `run` ends with a line of this form, with a number for each N:

```text
N edits in 10 minutes, N read by the model, skipped N (queue) and N (budget), US$N. Seconds to the queue for the model tier: median N, 95th percentile N.
```

The same numbers are the run's line in `data/log/live-runs.jsonl`: `edits`,
`read_by_model`, `skipped_queue_full`, `skipped_budget`, `model_errors`, `cost_usd`,
`tier1_p95_s`, `model_p50_s`, `model_p95_s`, `model`, `version`, `events`,
`reconnects`, `minutes`, `seconds`, `at`.

A row of `live.jsonl` holds a revision id, two times, the system version, the risk
and the tier-1 score. If the model read the edit it also holds the model's risk, the
category and the line numbers. `skip` says why an edit in the model's slice kept its
tier-1 score:

| `skip` | Meaning |
|---|---|
| `queue_full` | The queue between the tiers was full when the edit arrived |
| `budget` | The day's allowance was used up at that moment |
| `no_model` | The run had no model key |
| `model_error` | The model call failed, or the text of the edit could not be read |
| `gate` | The gate dropped the model's answer; `g` names the rule |
| `window_closed` | The edit was still waiting a minute after the window ended |

Where to look after a run on GitHub (the scoreboard page opens from disk):

```bash
git fetch origin live-data
git show origin/live-data:projects/edit-moderation-cascade/var/scoreboard/health.json
git show origin/live-data:projects/edit-moderation-cascade/data/log/live-runs.jsonl | tail -n 3
git show origin/live-data:projects/edit-moderation-cascade/var/scoreboard/index.html > /tmp/scoreboard.html
```

On the machine that holds the store: `uv run editwatch queue` prints the riskiest
edits of the last run, and `uv run --extra ui streamlit run app/review_queue.py`
opens the review page. The page's decisions go to `data/corrections/reviews.jsonl`.
A decision is a note on that machine. It changes nothing on a wiki.

`backfill` and `train` belong to setup. `record`, `prepare`, `baseline`, `backtest`,
`eval`, `loadtest`, `replay` and `demo` belong to the evaluation and the demo. None
of them is part of the loop.

## Start and stop

```bash
gh workflow run live-edit-moderation-cascade.yml       # one run now
gh workflow disable live-edit-moderation-cascade.yml   # stop the schedule
gh workflow enable live-edit-moderation-cascade.yml    # start it again

mkdir -p var                    # Compose, once
docker compose up -d --build    # start: the first window begins at once
docker compose logs -f watch    # watch
docker compose down             # stop: the logs and the store stay on disk
```

Compose serves the review page at `http://127.0.0.1:8501`. It reads the key and the
other three names from the shell or from a `.env` file next to `compose.yaml`. The
settings in `config.py` are inside the image: after a change there, start again with
`docker compose up -d --build`.

After a failed step the Compose loop writes the scoreboard, waits five minutes and
starts the next window. It does not stop by itself.

`run` appends its rows as the window goes, a moment after each edit is decided. A
window that is stopped part of the way, or that fails, keeps the rows written until
then, and what the model cost in them counts against the day's cap. It writes no
line to `live-runs.jsonl`, so the run count on the scoreboard leaves it out.
Stopping Compose loses the edits that were waiting for the model, not the window.

GitHub turns off schedules in a public repository after 60 days without activity in
it. Enable the workflow again if the runs stop.

## A missed run

Nothing is caught up, and there is nothing to do. `run` reads the stream from the
moment it connects. Edits made while nothing was listening are never ordered, and
they are not in the log, so they are not scored either. Under the schedule this is
the normal state for fifty minutes of every hour. Inside a window it is different:
when the connection drops, the reader connects again from the last event it saw, so
nothing is lost or read twice.

Outcomes are caught up. `truth` reads every logged edit that is at least 48 hours old
and has no outcome. After a gap those outcomes are read later than 48 hours after
the edit, so a revert made in the gap counts.

One exception, on GitHub Actions only. GitHub deletes a cache that no run has used
for seven days. After a gap that long the next run fetches the history again, about
25 minutes, and trains tier 1 again. See "Retraining tier 1" for what that changes
and for when the fixed dates in `config.py` are too old to fetch.

## The User-Agent

Wikimedia's policy asks every client for a name and a way to reach its operator. The
default in `config.py` names the client only:

```text
editwatch/0.1 (edit moderation cascade; open-source portfolio project)
```

A client that does not say how to reach its operator may be limited or blocked
without notice. Set `EDITWATCH_USER_AGENT` to a value that includes the repository's
URL once the repository is public. Do not put an email address there: the value is
sent with every request, and a repository variable is not a secret.

```bash
# in the shell; for Compose the same name goes into the .env file
export EDITWATCH_USER_AGENT="editwatch/0.1 (https://github.com/OWNER/REPOSITORY)"

# for the workflow, as a repository variable
gh variable set EDITWATCH_USER_AGENT --body "editwatch/0.1 (https://github.com/OWNER/REPOSITORY)"
```

The same value is sent to the stream and to the wiki's API. How the client behaves
towards the API, all of it in `src/editwatch/wiki.py`:

- Requests are made one at a time, never in parallel.
- At least five seconds pass between two requests (`wiki_pause_s`). On 17 November
  2026 the API refused the eleventh request of a burst and accepted one request
  every five seconds. Do not lower the pause.
- Every request carries `maxlag=5`. When the servers are behind, the client waits
  five seconds and asks again.
- On HTTP 429 or a server error it waits as long as the answer says, plus a second,
  and tries again, five attempts in all. Where the answer names no wait, it waits 30
  seconds longer each time. When the wait would be more than two minutes it stops
  with `Wikimedia asked for a wait of N seconds ...; stopping instead of pressing
  on`, and the command fails. The next pass tries again: an hour later under the
  schedule, five minutes later under Compose.
- Revisions are read 50 to a request, so one request covers 25 edits.

The pause is kept inside one command. Each command of the loop starts with a request
of its own. Whether Wikimedia treats requests from GitHub's runners differently from
requests from one fixed address is not known: the workflow has never run.

## The daily spend cap

The cap is `daily_cap_usd` in `config.py`, US$1.00 a day. `EDITWATCH_DAILY_CAP_USD`
wins over the file: as a repository variable for the workflow (Settings, Secrets and
variables, Actions, Variables), or in the shell or `.env` file for Compose. The day
is the UTC day. The cap is not part of the system version.

The cap is spent evenly through the day (`src/editwatch/budget.py`). At any moment
the allowance is the cap times the share of the day that has passed, plus one
twenty-fourth of the cap as headroom, and never more than the cap. At noon UTC that
is half the cap plus the headroom. A plain cap would be gone in the morning of a busy
day and leave the evening's edits unread.

At its start `run` adds up, from `live.jsonl`, what the rows written today have cost.
Before it admits an edit to the model's queue it compares the spend with the
allowance of that moment. At the allowance the edit keeps its tier-1 score, its row
carries `skip: budget`, and it is counted: the run prints the count and writes it to
`live-runs.jsonl` as `skipped_budget`. Those edits are not sent to the model later.
The scoreboard shows the spend of today and of the last 30 days next to the cap.

Three things to know:

- A call's cost is added when the call returns. A burst can pass the allowance by
  the calls that were already admitted, at most `queue_bound` of them.
- The cost of a call is its tokens times the prices in `PRICES` in
  `src/editwatch/llm.py`. A model that is not listed there is counted as free, and
  the cap never stops it.
- One call on a real edit costs about US$0.00016 with Haiku. The two ten-minute
  windows of 17 November 2026 cost 2.7 and 2.4 US cents (163 and 147 calls), so the
  schedule's 24 windows cost about 65 cents a day, under the cap. Compose reads
  several times as much for the same cap, so expect `skipped_budget` above zero
  there, or raise the cap. A full day was never run in either place.

## Backpressure

Between the two tiers is a bounded queue (`Admission` in `src/editwatch/cascade.py`).
Two settings in `config.py` shape it:

- `queue_bound`, 50: the most edits that may be admitted and not yet answered,
  whether they wait for the request to the wiki or are with the model.
- `workers`, 4: how many model calls run at once.

Tier 1 is never skipped: every edit gets a score and a row. What is given up under
load is the model's reading, in order of arrival. An edit in the model's slice that
arrives while 50 are already admitted keeps its tier-1 score, whatever that score
is, and its row carries `skip: queue_full`. The queue is checked before the budget,
so a full queue is counted as `queue_full` even when the allowance is also used up.
An unbounded queue would hide overload as latency. This one turns it into a count.

Where the count shows:

- the last line `run` prints: `skipped N (queue) and N (budget)`;
- `skipped_queue_full` and `skipped_budget` in the run's line in
  `data/log/live-runs.jsonl`;
- the scoreboard: "skipped under load or budget" under "Edits ordered", and
  `live.skipped` in `scoreboard.json`.

At the stream's usual rate the queue did not fill in the live runs of 17 November
2026, and in the load test it does not fill below ten times that rate. If
`skipped_queue_full` is above zero in run after run:

1. Look at `model_p50_s`. If the model calls themselves are slow, raise `workers`.
   More calls at once only helps while the model's rate limit allows them.
2. Lower the share of edits the model reads. See "Changing the model or the share
   of edits it reads".
3. Raising `queue_bound` removes the skips and adds waiting. Do that only while the
   latency target still holds.

`uv run editwatch loadtest` replays the committed ten-minute slice of the stream at
1 to 50 times its rate, ten times at each speed, and prints the skips and the
latency at each. It is a simulation on a virtual clock: it shows what this policy
does under load, not what the network does. It writes `data/loadtest.json`. With
`--every` the model reads every edit, and the result goes to
`data/loadtest-every.json`. The request times it draws from are in
`data/latencies.json`: `wiki` for full batches of 25 edits, written by `prepare`,
and `wiki_small` for requests of up to five edits, written by each `run`.

## The latency target

`latency_target_s` in `config.py` is 15 seconds: the time from an edit's event to its
place in the queue, at the 95th percentile, for the edits the model read. Each run
writes its own figure as `model_p95_s` in `live-runs.jsonl`, and the scoreboard shows
the figure of the last run next to the target ("Seconds to the queue"). Nothing
fails when the target is missed. It is a number to watch. On 17 November 2026 the
live loop reached 7.1 seconds, with a median of 4.5. The first run that day took
17.9: it made the request to the wiki on the loop that scores edits, which was
changed before the second run.

The time has three parts: the wait for the next request to the wiki (five seconds
after the last answer), that request, and the model call. It is counted from the
time the event carries, not from the time it arrived here, so it includes the
stream's own delay of a few tenths of a second.

When the 95th percentile is above the target:

1. Read the last lines of `data/log/live-runs.jsonl`.
2. `tier1_p95_s` is high as well (it was 0.8 seconds in the second run): the delay
   is not in the model and not in the wiki, because the request to the wiki runs on
   a thread of its own and tier 1 does not wait for it. Either the stream lags or
   this machine's clock is wrong (run `uv run editwatch health` and read
   `stream_lag`; compare `date -u` with a clock you trust), or the machine is short
   of CPU. A rate-limit answer from the wiki delays only the edits waiting for the
   model, for the wait it asks for.
3. `skipped_queue_full` is above zero: the model tier cannot keep up. See
   "Backpressure".
4. Only `model_p95_s` is high and `model_p50_s` is not: a few slow model calls. Check
   `model_errors`, and Anthropic's status page.
5. Both are high and nothing is skipped: lower the share of edits the model reads.
   Do not lower `wiki_pause_s`.

## Rotate the model key

1. Create a new key in the Anthropic Console.
2. Replace the repository secret `ANTHROPIC_API_KEY`
   (`gh secret set ANTHROPIC_API_KEY`). Under Compose, change it in the shell or
   `.env` file and run `docker compose up -d`.
3. Wait for the next window, or start a run by hand. Its line in `live-runs.jsonl`
   must show `read_by_model` and `cost_usd` above zero and `model_errors` at zero.
4. Delete the old key in the Console.

A wrong or expired key does not fail the run, so look at those numbers. With a bad
key every model call fails: `model_errors` counts every edit that reached the
model, `read_by_model` and `cost_usd` are zero, `model` still names the model, and
the run prints `N model calls gave no usable answer.` With no key at all, `model` is
`null` and the run prints `No model key found: running tier 1 alone.`

In a run without a key the rows of the model's slice carry `skip: no_model`, and
all its rows go under the same system version as rows written with a key. Hours
without a key therefore pull that version's line on the scoreboard towards tier 1
alone.

## When a health check fails

`health` exits 1. The workflow publishes `health.json` and the scoreboard and then
ends failed, and GitHub sends its notice. Compose logs the failure, waits five
minutes and tries again. Run `uv run editwatch health` to see the lines. Each check
below is named as it appears there. The limits are constants at the top of
`src/editwatch/health.py`.

### `stream_answers`

The stream could be read and its events parse. It fails with the reason:

- `The stream answered HTTP N`: the stream refused the connection. 403 or 429: see
  "The User-Agent" and wait. 5xx: Wikimedia's side. Wait for the next pass.
- `The stream dropped N times in a row`: the network, here or there. Wait.
- `A stream event is not JSON` or `A stream event no longer parses`: see "When
  Wikimedia changes an event or a response".

When this check fails, `stream_volume`, `edits_kept` and `stream_lag` are not run.

This check can read ok while the stream is down. The reader waits longer between
attempts each time, and the 20 seconds can end while it is still trying. Then
`stream_volume` fails with 0 events. Read the two together.

### `stream_volume`

Fewer than one event a second arrived, counted across all wikis. The ten-minute
recording of 17 November 2026 had 23,983 events, about 40 a second, so a count this
low is a fault and not a quiet hour. With 0 events the stream could not be reached at
all. Wait for the next pass. If it fails for hours while
`https://stream.wikimedia.org/v2/stream/recentchange` opens in a browser, the client
is being refused: see "The User-Agent".

### `edits_kept`

Fewer than 0.2 article edits by people a second were kept, that is fewer than 4 in
the 20 seconds. The same recording had 695 in ten minutes, more than one a second.

- `stream_volume` is ok and this check shows 0: events arrive and none passes the
  filter. `parse_event` drops, without an error, every event whose `type`, `wiki`,
  `namespace` or `bot` field does not match. If Wikimedia renamed one of those
  fields, this check is the only sign. Look at a raw event:

  ```bash
  curl -sN --max-time 10 -A "editwatch/0.1 (edit moderation cascade; open-source portfolio project)" \
    https://stream.wikimedia.org/v2/stream/recentchange | grep '^data:' | head -n 3
  ```

  A raw event carries the account name of whoever made the change. Read it in the
  terminal and do not paste it into an issue or a commit. Then see "When Wikimedia
  changes an event or a response".
- A few edits, but too few: English Wikipedia may be read-only for maintenance. Wait
  for the next pass.

### `stream_lag`

The newest event heard carries a time more than 120 seconds in the past. Either the
stream is behind or this machine's clock is wrong. Compare `date -u` with a clock
you trust, then wait for the next pass. A lagging stream also raises the latency
figures, because latency is counted from the event's own time.

`No event carried its own time` means one of two things. No edit was kept at all:
then `edits_kept` has failed too, start there. Or the events no longer have
`meta.dt`: see "When Wikimedia changes an event or a response".

### `wiki_answers`

After listening, `health` asks the wiki's API for the tags of the newest edit it
heard. This is the request `truth` depends on, and `run` uses the same client. The
check is missing from the list when no edit was heard. It fails with the reason:

- `Wikimedia asked for a wait of N seconds`: the client is being rate limited. Do
  not run the command again and again. See "The User-Agent".
- `Wikimedia answered HTTP N`: 403 usually means the client is refused. See "The
  User-Agent".
- `Wikimedia did not answer after five attempts`: their side or the network. Wait.
- `Wikimedia API error` or `A revisions response no longer parses`: see "When
  Wikimedia changes an event or a response".
- `The wiki did not return the newest edit`: the revision was deleted or hidden in
  the seconds between the event and the request. Once is chance. If it repeats, the
  revisions response has changed shape.

## When Wikimedia changes an event or a response

A `FeedError` means an answer could not be used: no answer, a status other than 200,
a body that is not JSON, or a record without a field the system needs. The command
stops with `Feed error: ...` and exit status 1, and nothing is guessed. `run` keeps
the rows it had written before the error and writes no line to `live-runs.jsonl`.

The parsers:

- `stream.parse_event` in `src/editwatch/stream.py`: one event of the stream. It
  reads `type`, `wiki`, `namespace`, `bot`, `revision.new`, `revision.old`, `title`,
  `timestamp`, `minor`, `length.old`, `length.new`, `comment`, `meta.dt`, and `user`
  once, to tell a temporary account from a registered one.
- `wiki.parse_recent_change` in `src/editwatch/wiki.py`: one entry of the
  recent-changes list, which `backfill` reads.
- `WikiClient.recent_changes`, `revision_texts` and `revision_tags` in the same
  file: the envelope around the entries, and the revisions that `run` and `truth`
  read.

No parser keeps an account name, and no record has a field for one. Keep it that way
when a parser changes.

1. Reproduce it away from the live record. `EDITWATCH_HOME` moves `var/` and `data/`
   to another folder, and an empty one is enough for `health`:

   ```bash
   EDITWATCH_HOME="$(mktemp -d)" uv run editwatch health
   ```

2. Change the parser. Put a record in the new shape into the tests:
   - a stream event: `stream_event` in `tests/conftest.py` builds one as the stream
     sends it, and `tests/test_stream_recorder.py` holds the cases to extend (the
     tests of kept and dropped events, and `test_a_stream_event_in_a_changed_shape_fails_loudly`);
   - a recent-changes entry or a revisions response: `api_item` and `FakeWiki` in
     `tests/conftest.py`, and the cases in `tests/test_wiki.py`
     (`test_a_recent_change_*`, `test_a_changed_recent_changes_answer_fails_loudly`,
     `test_revision_*`).
3. Run `uv run --all-extras pytest -q`, then step 1 again until every check is ok.
4. Commit to `main` and push. Under Compose, `docker compose up -d --build`. Nothing
   is caught up: the next window reads from now.

## Changing the model or the share of edits it reads

Every row carries a system version: a hash of the configuration name, the tier-1
features, the number of edits tier 1 was trained on and its score thresholds, the
model's name, the prompt, the share of edits the model reads, the longest change it
is shown (`change_chars`) and the combiner's weights. A change to any of them starts
a new version, and the scoreboard scores each version on its own line. Old rows are
never rewritten. The cap, `queue_bound`, `workers` and the latency target are not
part of the version.

**The share.** `model_share` in `config.py`, 0.20, or `EDITWATCH_MODEL_SHARE`, which
wins over the file. Tier 1 keeps a score threshold for these shares only: 0.02,
0.05, 0.10, 0.20, 0.30, 0.40 and 0.50. Use one of them, or 1 for every edit. Any
other value stops the command with an error that lists them. The threshold is the
score that this share of the training edits exceeds. On the live stream of 17 November 2026 the model read 163 of 757
edits and 147 of 742, so 22% and 20%. A larger share costs more, fills the queue sooner
and adds requests to the wiki. `EDITWATCH_MODEL_SHARE` is passed through by
`compose.yaml`. The workflow does not pass it: change `config.py` on `main` there.

**The model.** `live_model` in `config.py`. It must be one of the three names in
`MODELS` in `src/editwatch/cli.py`, each of which has a price in `PRICES` in
`src/editwatch/llm.py`. With any other name `run` stops with a traceback before it
reads the stream. Leave `EDITWATCH_EFFORT` unset: it changes how the model is asked
and is not part of the version.

**The weights.** For an edit the model read, the risk in the queue comes from the
tier-1 score and the model's answer through two weights. They are kept per model in
`data/combiners.json`, fitted on the development days of the backtest only and then
fixed, so the held-out days and the live run use numbers set in advance. A model
without an entry there runs with the default weights in `src/editwatch/combine.py`,
and nothing says so. Fit a new model's weights before it goes live:

```bash
uv run editwatch backtest --models sonnet --part development --live --record --cap 0.30
```

That command calls the paid model, up to the `--cap` given, and needs the store and
the changes that `uv run editwatch prepare` fetches. Commit `data/combiners.json`.

## Retraining tier 1

The dates are in `config.py`: `history_start`, `cut`, `held_out_start` and
`test_end`. `backfill` fetches a slice of 500 edits every two hours between
`history_start` and `test_end`, one request a slice. `test_end` must be at least 48
hours in the past when `backfill` runs, so that every edit it fetches has its
outcome.

On your own machine or under Compose:

```bash
# edit history_start, cut, held_out_start and test_end in src/editwatch/config.py
uv run editwatch backfill    # fetches the slices the store does not hold yet
uv run editwatch train       # the backtest's tier 1 and data/tier1.json
uv run editwatch run --minutes 10   # trains the live tier 1 on all stored history
```

The store keeps the history it already has, and the live tier 1 is trained on all of
it, old and new. To train on the new dates alone, move `var/editwatch.db` aside
before the backfill. The new tier 1 has new thresholds, so the system version
changes and the scoreboard starts a new line. The weights in `data/combiners.json`
were fitted with the scores of the tier 1 before it.

On GitHub Actions:

1. Change the dates on `main` and push.
2. Delete the cached store, so that the next run fetches the history under the new
   dates:

   ```bash
   for id in $(gh cache list --key edit-moderation-cascade-store- --limit 100 --json id --jq '.[].id'); do
     gh cache delete "$id"
   done
   ```

3. Start a run, or wait for the next one. It takes about 25 minutes longer than
   usual.

Two things to know before a rebuild surprises you:

- **The dates go stale.** The recent-changes list holds a limited period. How far
  back it reaches was not checked: the backfill of 17 November 2026 asked for 25
  October and got it. Once `history_start` is older than that, `backfill` returns less history
  than before, and in the end none. `run` then stops with a message that begins
  `No stored history`, or trains on a thinner store and starts a new version
  without saying why. The workflow rebuilds after a gap of seven days and whenever
  you delete the cache: move the dates forward first. If a rebuilt store is wrong,
  fix the dates and delete the cache again.
- **A rebuild can start a new version.** A rebuilt store need not be identical to
  the one before: an edit may have been deleted since, and a revert made since
  changes an outcome. Tier 1 is trained again on what was fetched. If its
  thresholds move, the scoreboard starts a new line.

## What is committed and what is not

| What | Path | Kept in |
|---|---|---|
| Live prediction log, run log, live outcomes | `data/log/live.jsonl`, `live-runs.jsonl`, `outcomes-live.jsonl` | `live-data` under the workflow, the working tree under Compose |
| Feed checks, scoreboard | `var/scoreboard/health.json`, `scoreboard.json`, `index.html` | `live-data`; not in git under Compose |
| Backtest log and outcomes, once the backtest has run | `data/log/backtest.jsonl.gz`, `outcomes-backtest.json.gz`, `backtest-meta.json` | `main` |
| Tier-1 summary, the model samples (revision ids), the combiner's weights, the load test's results and the request times it draws from | `data/tier1.json`, `data/samples.json`, `data/combiners.json`, `data/loadtest.json`, `data/loadtest-every.json`, `data/latencies.json` | `main` |
| The invented demo wiki, and a ten-minute slice of real stream events without edit summaries | `data/fixture/` | `main` |
| The model's recorded answers: four fields each, a hash of the prompt, token counts | `data/cassettes/` | `main` |
| Settings | `src/editwatch/config.py` | `main` |
| The store: edits with their scrubbed summaries, what each edit changed, Wikimedia's scores | `var/editwatch.db` | never committed; Actions cache, or the disk |
| The trained tier 1 | `var/tier1-*.pkl` | never committed; Actions cache, or the disk |
| The model's answers on live edits | `var/cassettes/live.jsonl` | never committed; the disk under Compose, dropped after each run of the workflow |
| Recordings of the stream | `var/recordings/` | never committed |
| Reviewers' decisions | `data/corrections/reviews.jsonl` | git-ignored; commit them in a real deployment |

Never committed, on any branch: page text, edit summaries, the store, and the
recordings under `var/`. Page text and summaries are other people's writing, and the
store quotes both. `var/` is git-ignored as a whole. The only files from it that
reach a branch are the three scoreboard files the workflow names and publishes to
`live-data`. The text of a failed check in `health.json` can quote a field of an
event that did not parse, such as a page title. No parser hands an account name or
an unscrubbed summary to the record that is checked.

Committed: revision ids, scores, categories, line numbers and outcomes in the logs;
the slice of the stream in `data/fixture/stream-slice.jsonl`, which keeps page
titles and has no edit summaries; and the model's recorded answers, which contain no
text because the model returns none. No account name is stored anywhere, committed
or not. On `live-data` the files are at the same paths as on `main`, under
`projects/edit-moderation-cascade/`. `EDITWATCH_HOME` moves `var/` and `data/` under
another folder.

Two limits to watch:

- `data/log/live.jsonl` grows by one row for every edit and is never rotated. The
  ten-minute recording had 695 edits, so expect about 700 rows a run under the
  schedule, 24 runs a day, and several times that under Compose. GitHub refuses a
  file larger than 100 MB. A row is about 180 bytes, so a run adds about 130 KB
  and a day about 3 MB: the file reaches the limit after about a month. Rotation is
  not built: before then the log has to be split by month, which is a change to
  `live_paths` in `src/editwatch/live.py` and to what the scoreboard reads.
- Under Compose the store grows with every window: the edits heard, and what each
  edit the model read changed. How fast was not measured. The workflow does not keep
  that growth. It saves the store only after fetching the history or training
  tier 1.

## The system never edits and never reverts

The system orders a queue for people to read. It has no code path that writes to a
wiki. Every request to a wiki is a GET with `action=query`, made in
`src/editwatch/wiki.py`, and the stream is read with a GET in
`src/editwatch/stream.py`. The one POST in the code asks Wikimedia's revert-risk
service to score a revision. It is used by `baseline` for the evaluation, is not
part of the loop, and changes nothing on a wiki. The project holds no wiki account,
no password and no token, and neither the workflow nor Compose is given one. The
review page records a reviewer's decision in a local file and does nothing else with
it. Whether an edit is undone is decided and done by a person, on the wiki, outside
this system.
