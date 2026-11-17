# Runbook: issue triage desk

For the person who keeps the hourly run going. Commands are run in
`projects/issue-triage-desk`. The tracker served is the `repo` in `triagedesk.yaml`
(`godotengine/godot`).

## What runs and when

Once an hour the desk checks the feed, answers the issues opened since its last run,
reads what the maintainers did with issues that are 14 days old, and writes a
scoreboard. It runs in shadow mode: it reads the tracker and writes only its own
files. The run is scheduled in one of two places. Use one, not both: each keeps its
own store and its own copy of the logs.

- **GitHub Actions**, `.github/workflows/live-issue-triage-desk.yml`: every hour at
  minute 17. It writes to the `live-data` branch, never to `main`. GitHub can start a
  scheduled run several minutes late.
- **Docker Compose**, `compose.yaml`: on any machine. One pass at start, then one an
  hour. It writes to the working tree and serves the review page.

Neither passes `--apply`. Nothing in the schedule can write to the tracker.

## First-time setup

On your own machine or the machine that runs Compose:

```bash
uv sync --all-extras          # --all-extras adds Streamlit for the review page
uv run triagedesk init        # reads the repository's label list into the store
uv run triagedesk backfill    # every issue opened since history_start
uv run triagedesk index       # one vector per stored issue
uv run triagedesk run         # the first live run: answers the issues opened in the last 24 hours
```

- `init` reads `triagedesk.yaml` as it is and makes a few requests.
- `backfill` takes about 45 minutes without a token, because GitHub's search allows
  10 requests a minute. It remembers each finished month, so it can be stopped and
  started again.
- `index` takes about 20 minutes on a laptop CPU. On first use it downloads the
  embedding model (about 70 MB). It also picks up where it stopped.
- The store is `var/triagedesk.db`, about 270 MB. It is never committed.

Both of these are optional:

- `GITHUB_TOKEN` (or `GH_TOKEN`), in the environment or in a `.env` file here or in a
  parent folder. It only raises GitHub's read limits: search goes from 10 to 30
  requests a minute. Any token works; it needs no permission for a public repository.
- `ANTHROPIC_API_KEY`, the same way. Without it `run` says "No model key found" and
  writes the answers of the rules and the classifier only. The cost is then zero.

Under Compose the same three commands run in the container, once:
`docker compose run --rm desk triagedesk init`, then `backfill`, then `index`.

On GitHub Actions:

1. Push the repository. The schedule only runs from the default branch, `main`.
2. Optional: add the key as a repository secret named `ANTHROPIC_API_KEY` (Settings,
   Secrets and variables, Actions). The workflow uses its own `GITHUB_TOKEN` for
   reading; nothing has to be created for that.
3. In the Actions tab open `live-issue-triage-desk`, press Enable workflow if GitHub
   shows that button, then Run workflow. The first run finds no store in the cache,
   so it runs `init`, `backfill` and `index` itself. That takes roughly an hour. It
   also creates the `live-data` branch. No branch rule may block GitHub Actions from
   pushing to it.

After a rebuild the first run looks back 24 hours and answers every issue opened in
that time that the live log has no answer for. Older issues are history: they are in
the store and are not answered.

## The hourly loop, command by command

| Command | What it does | What it writes |
|---|---|---|
| `triagedesk health` | Searches the issues of the last seven days and runs four checks on them. Exits 1 if one fails. | `var/scoreboard/health.json` |
| `triagedesk run` | Builds the desk from the issues opened before `live_cut`. Fetches the issues opened since the last run, stores each as first seen, embeds it and answers it under each configuration: `rules`, `classifier`, and `model` if there is a key. | Appends to `data/log/live.jsonl` (one row per issue and configuration) and one line to `data/log/live-runs.jsonl`. Rewrites `data/taxonomy-live.json`. In the store: the new issues, their vectors, the model's notes. Model responses go to `var/cassettes/live.jsonl`. |
| `triagedesk truth` | For each answered issue that is 14 days old and has no outcome yet: reads its labels and state again and, if it was closed as a duplicate, its timeline. | Appends to `data/log/outcomes-live.jsonl` |
| `triagedesk scoreboard` | Scores the logs. Uses no network. | `var/scoreboard/scoreboard.json`, `var/scoreboard/index.html` |

If `health` fails, `run` and `truth` are skipped for that hour and the scoreboard is
still written, so the failed check is on it. A normal `run` ends with a line like
this (example numbers):

```text
7 new issues (2 already labelled or closed), 21 predictions, US$0.0009
```

Where to look after a run on GitHub (the scoreboard page opens from disk):

```bash
git fetch origin live-data
git show origin/live-data:projects/issue-triage-desk/var/scoreboard/health.json
git show origin/live-data:projects/issue-triage-desk/data/log/live-runs.jsonl | tail -n 3
git show origin/live-data:projects/issue-triage-desk/var/scoreboard/index.html > /tmp/scoreboard.html
```

On the machine that holds the store: `uv run triagedesk queue` prints the latest
proposals, and `uv run --extra ui streamlit run app/triage_queue.py` opens the review
page. The page's decisions go to `data/corrections/reviews.jsonl`.

`backfill`, `timelines`, `replay`, `tune`, `eval` and `demo` are not part of the loop.
They belong to setup and to the evaluation.

## Start and stop

```bash
gh workflow run live-issue-triage-desk.yml       # one run now
gh workflow disable live-issue-triage-desk.yml   # stop the schedule
gh workflow enable live-issue-triage-desk.yml    # start it again

mkdir -p var                    # Compose, once
docker compose up -d --build    # start: one pass now, then one an hour
docker compose logs -f desk     # watch
docker compose down             # stop: the logs and the store stay on disk
```

Compose serves the review page at `http://127.0.0.1:8501`. It reads the key and the
token from the shell or from a `.env` file next to `compose.yaml`.

GitHub turns off schedules in a public repository after 60 days without activity in
it. Enable the workflow again if the runs stop.

## A missed run

Nothing to do. The next run starts from the creation time of the newest issue the
desk has seen (it reads two hours before that again, in case search lagged), so it
answers everything opened in the gap. Issues that the maintainers labelled or closed
in the meantime are answered and logged with a flag, and the live scores leave them
out: the answer was already on the page. The run prints how many there were.

One exception, on GitHub Actions only. GitHub deletes a cache that no run has used
for seven days. After a gap that long the next run rebuilds the store (about an hour)
and, having lost its place, looks back 24 hours only. Issues opened earlier in the gap
are not answered.

## The daily spend cap

At its start `run` adds up, from `live.jsonl`, what the rows written today (UTC) have
cost. It adds the cost of each model call and checks the total before the next call.
The cap is `daily_cap_usd` in `triagedesk.yaml`, US$0.50 a day. At the cap the run
goes on without the model: each remaining issue is answered by the classifier, sent
to a person with the reason `model_unavailable`, and counted. The run prints the
count and writes it to `live-runs.jsonl` as `calls_refused_at_cap`. Those issues are
not sent to the model later.

To change the cap, edit `daily_cap_usd` or set `TRIAGEDESK_DAILY_CAP_USD`, which wins
over the file: as a repository variable for the workflow (Settings, Secrets and
variables, Actions, Variables), or in the shell or `.env` file for Compose. The cap is
not part of the system version.

## Rotate the model key

1. Create a new key in the Anthropic Console.
2. Replace the repository secret `ANTHROPIC_API_KEY`. Under Compose, change it in the
   shell or `.env` file and run `docker compose up -d`.
3. Wait for a run that answers a new issue, or start one by hand. Its line in
   `live-runs.jsonl` must show a `cost_usd` above zero.
4. Delete the old key in the Console.

A wrong or expired key does not fail the run, so look at that number. With a bad key
each issue is answered by the classifier with the reason `model_unavailable`, the
cost stays at zero, and `configs` still lists `model`.

## When a health check fails

`health` exits 1, the workflow publishes `health.json` and the scoreboard and then
ends failed, and GitHub sends its notice. Run `uv run triagedesk health` to see the
four lines. Each check below is named as it appears there.

### `feed_answers`

The search for the last seven days of issues failed. The other three checks were not
run. The detail says why:

- "GitHub did not answer ... after six attempts", or "incomplete search results
  three times in a row": GitHub is down or slow. Wait for the next run.
- "quota is used up; it resets in N minutes": see "Rate limits". Wait.
- "GitHub answered HTTP 401": the token is wrong or expired. Remove or replace it.
  HTTP 404 or 422: check `repo` in `triagedesk.yaml`.
- "no longer parses": see "When GitHub changes a response shape".

### `feed_fresh`

The newest issue is more than 48 hours old, or no issue was opened in seven days.
Open the tracker in a browser. If it has newer issues, the search no longer returns
them: check that the repository was not renamed or moved, then treat it as a changed
response. If the tracker really is quiet, the check fails until an issue arrives;
disable the schedule for that time if the notices are a nuisance.

### `volume`

The last seven days have less than 40% of the issues of a usual week (the median of
the eight weeks before, counted in the store). A holiday week does this and needs
nothing. If it repeats, compare the count in the detail with the tracker. A store
that was rebuilt for only part of the history also shifts the median: run
`uv run triagedesk backfill` again, it only fetches what is missing.

### `labels_known`

An issue of the last seven days carries a label of a configured group (`topic:` or
`platform:`) that the taxonomy has never seen. The maintainers added a label. The
desk cannot propose it until it is retrained, and its scores on that label would be
wrong, so the run stops. Follow "Retraining". The detail names the labels.

## When GitHub changes a response shape

A `FeedError` means an answer could not be used: no answer, a status other than 200,
a body that does not parse, or a record without a field the desk needs. The run stops
and nothing is guessed. Everything that reads GitHub's answers is in
`src/triagedesk/feed.py`:

- `GitHubClient.search_page`: the search response (`total_count`, `items`,
  `incomplete_results`).
- `parse_issue`: one issue (number, title, body, dates, state, state reason, labels).
- `parse_timeline`: an issue's timeline, where the original of a duplicate is named.
- `GitHubClient.labels`: the repository's label list.

No field that names a person is read. Keep it that way when a parser changes.

1. Reproduce it away from the live record. `TRIAGEDESK_HOME` moves `var/` and `data/`
   to another folder; an empty one is enough to exercise the search parser. Only the
   `feed_answers` line matters here, the other checks have nothing to compare with.

   ```bash
   TRIAGEDESK_HOME="$(mktemp -d)" uv run triagedesk health
   ```

2. Change the parser. Put a record in the new shape into the tests: `api_item` in
   `tests/conftest.py` builds an issue as the API returns it, and `tests/test_feed.py`
   holds the cases to extend (`test_parse_issue_*`, `test_parse_timeline_*`, the
   search tests). A timeline change also needs a case in `tests/test_truth_gate.py`.
3. Run `uv run --all-extras pytest -q`, then step 1 again until `feed_answers` is ok.
4. Commit to `main` and push. The next run answers the issues opened in the meantime.

## Rate limits

| Quota | Without a token | With a token | Used by |
|---|---|---|---|
| Search | 10 requests a minute | 30 a minute | `backfill`, `health`, `run`, `truth` |
| Core | 60 requests an hour | 5,000 an hour (1,000 for the token GitHub Actions provides) | timelines, one request per issue closed as a duplicate; the label list |

The client paces itself: one search every 6.2 seconds without a token, every 2.1
seconds with one. When GitHub still answers 403 or 429, it reads how long to wait
from the response. `backfill` and `timelines` wait, up to about an hour. The
scheduled commands wait at most 90 seconds (`truth` 30) and then stop with "quota is
used up; it resets in N minutes", and the next hourly run tries again. Server errors
are tried six times with a growing pause. Nothing retries in a tight loop.

A timeline that could not be fetched does not fail `truth`. The outcome is written as
`not_fetched`, kept out of the duplicate scores, and read again on a later run.

## Retraining

The live desk is trained on the issues opened before `live_cut` in `triagedesk.yaml`.
Move it forward to retrain: the taxonomy and the classifier are rebuilt from the
store, the system version changes, and the scoreboard starts a new line for it. The
old rows stay in the log under the old version.

On your own machine or under Compose:

```bash
uv run triagedesk backfill --from 2026-05-01 --refresh   # read the labels and state of recent months again
# edit live_cut in triagedesk.yaml, for example to today's date
uv run triagedesk run                          # trains, rewrites data/taxonomy-live.json
uv run triagedesk health                       # labels_known must pass now
```

The store keeps the labels an issue had when it was last fetched, and `backfill`
skips a month it has finished. `--refresh` fetches finished months again: titles and
bodies stay as first seen, labels and state are brought up to date. Refresh at least
the months since the previous `live_cut`.

The order matters after a failed `labels_known`: `health` reads the label list that
only `run` writes, so the loop cannot get past the check by itself. Run `run` once by
hand as above (`docker compose run --rm desk triagedesk run` under Compose).

On GitHub Actions:

1. Change `live_cut` on `main` and push.
2. Delete the cached store, so that the next run fetches the history again with
   today's labels:

   ```bash
   for id in $(gh cache list --key issue-triage-desk-store- --limit 100 --json id --jq '.[].id'); do
     gh cache delete "$id"
   done
   ```

3. Start one run that is allowed past the failed check. It takes about an hour:

   ```bash
   gh workflow run live-issue-triage-desk.yml -f run_after_failed_check=true
   ```

   This run still ends failed if the check failed at its start. The next hourly run
   must be green.

A new label needs at least `min_issues` training issues (30) to be proposed. With
fewer it is listed as dropped, which is enough for the check to pass.

## Going back to an earlier system version

Every log row carries a system version: a hash of the configuration name, the
repository, the label groups, the taxonomy, the classifier's thresholds, the
duplicate thresholds, the search settings and, for the model, its name, its
parameters and the prompt. A change to any of them starts a new version with its own
line on the scoreboard. Old rows are never rewritten, so the log keeps every
version's rows and going back means running the earlier files:

```bash
git log --oneline -- triagedesk.yaml src/triagedesk   # find the commit that made the change
git revert <commit>                                   # on main, then push
```

The settings are in `triagedesk.yaml`, the prompts in `src/triagedesk/triage.py`. If
the change was made with `TRIAGEDESK_MODEL` or `TRIAGEDESK_EFFORT`, unset the
variable. The next run computes the earlier hash again and adds to that version's
line, provided the store is the same: the taxonomy is built from the stored issues,
so a store that was rebuilt in between can give a new hash from the same files.

## Apply mode

Off by default, and not used by the workflow or by Compose. In apply mode the desk
writes to the tracker what it is sure of:

- the labels it proposes at high confidence are added to the issue;
- for a duplicate at high confidence it posts one comment that names the earlier
  issue, quotes the matching passage and says that a maintainer decides.

It never closes an issue, never removes a label and never edits anything. It acts
only on issues that are new in that run, and on the answers of the last configuration
that ran: the model's, or the classifier's when there is no model key.

**This path has tests against a fake tracker and has never been run against GitHub.**
Try it on a repository of your own first. It needs three things:

1. The repository in `apply_allow` in `triagedesk.yaml`, for example
   `apply_allow: ["owner/name"]`. It must be the `repo` the desk serves.
2. A token with write access to that repository's issues, as `GITHUB_TOKEN`.
3. `uv run triagedesk run --apply`.

Without the first two the command exits with status 2 and "Refusing to apply", and
nothing is fetched or written. Each run's line in `live-runs.jsonl` records `mode`
(`shadow` or `apply`) and the number of issues `applied`.

## Where the data is

| What | Path | Kept in |
|---|---|---|
| Live prediction log, run log, live outcomes | `data/log/live.jsonl`, `live-runs.jsonl`, `outcomes-live.jsonl` | `live-data` under the workflow, the working tree under Compose |
| The labels the live desk proposes | `data/taxonomy-live.json` | same |
| Feed checks, scoreboard | `var/scoreboard/health.json`, `scoreboard.json`, `index.html` | `live-data`; not in git under Compose |
| Backtest log, outcomes, taxonomy | `data/log/backtest.jsonl.gz`, `outcomes-backtest.jsonl`, `backtest-meta.json`, `data/taxonomy.json` | `main` |
| The invented demo tracker and the model's answers on it | `data/fixture/`, `data/cassettes/fixture.jsonl` | `main` |
| Settings | `triagedesk.yaml` | `main` |
| The store: issue text, vectors, timelines, the model's notes | `var/triagedesk.db` | never committed; Actions cache, or the disk |
| Model recordings on real issues | `var/cassettes/` | never committed |
| The trained classifier | `var/classifier-*.pkl` | never committed; Actions cache, or the disk |
| The embedding model, about 70 MB | `var/fastembed` in the workflow and the image, else the system temp folder | never committed; Actions cache |
| Reviewers' decisions | `data/corrections/reviews.jsonl` | git-ignored; commit them in a real deployment |

Three things are never committed, on any branch: issue text, the store, and model
recordings made on real issues. Issue text belongs to the people who wrote it, and
the recordings and the store quote it. The committed logs hold issue numbers, label
names and the desk's answers only. On `live-data` the files are at the same paths as
on `main`, under `projects/issue-triage-desk/`. `TRIAGEDESK_HOME` moves `var/` and
`data/` under another folder.
