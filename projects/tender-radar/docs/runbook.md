# Runbook: tender radar

For the person who keeps the daily run going. Commands are run in
`projects/tender-radar`. The run is scheduled in one of two places. Use one, not both:
each keeps its own copy of the logs.

- **GitHub Actions**, `.github/workflows/live-tender-radar.yml`: Tuesday to Saturday at
  05:40 UTC, so each run lists the working day before. It writes to the `live-data`
  branch, never to `main`.
- **Docker Compose**, `compose.yaml`: on any machine. It writes to the working tree.

## One-time setup

1. Push the repository to GitHub. The schedule only runs from the default branch, `main`.
2. Optional: add the key as a repository secret named `ANTHROPIC_API_KEY` (Settings,
   Secrets and variables, Actions).
3. In the Actions tab open `live-tender-radar`, press Enable workflow if GitHub shows
   that button, then Run workflow. The first run creates the `live-data` branch. No
   branch rule may block GitHub Actions from pushing to it.

Without the secret everything runs except the model. The client's list is written
without assessments and the cost is zero. GitHub turns off schedules in a public
repository after 60 days without activity; enable the workflow again if the runs stop.

## Start and stop

```bash
gh workflow run live-tender-radar.yml       # one run now
gh workflow disable live-tender-radar.yml   # stop the schedule
gh workflow enable live-tender-radar.yml    # start it again

mkdir -p var                    # Compose, once
docker compose up -d --build    # start: one pass now, then daily at 06:00 UTC
docker compose logs -f radar    # watch
docker compose down             # stop: logs and database stay on disk
```

Compose serves the scoreboard at `http://127.0.0.1:8080`. It reads the key from the
shell or from a `.env` file next to `compose.yaml`.

## A normal day

`tenderradar run` reads the last 14 days of the feed and writes lists for every
publication day after the last one in `live-days.jsonl`, up to yesterday. Today's issue
is left for tomorrow's run: a day is listed once, so it has to be complete. It ends like
this (example numbers), with one line per feed check:

```text
days processed: ['2026-11-16']; tenders: 212; lists written: 402
model: 10 assessments, US$0.0041 spent, 0 skipped at the daily cap, 0 errors
  [ok] feed_answers: the feed answered every request
```

It appends to `live.jsonl`, `live-days.jsonl` and `data/cassettes/live.jsonl` and writes
`health.json`. Then `truth` appends to `outcomes-live.jsonl`, `eval` writes the report
and `scoreboard` writes the page. On a day without a new issue of TED, `days processed`
is empty and nothing is appended.

Where to look after a run on GitHub (the scoreboard page opens from disk):

```bash
git fetch origin live-data
git show origin/live-data:projects/tender-radar/var/scoreboard/health.json
git show origin/live-data:projects/tender-radar/var/scoreboard/index.html > /tmp/scoreboard.html
git show origin/live-data:projects/tender-radar/data/log/live.jsonl | tail -n 3 | jq -r '[.day, .ranker, .system_version] | @tsv'
```

On the machine that holds the logs: `uv run tenderradar health`, `uv run tenderradar today`,
and the bid manager's page, `uv run --extra ui streamlit run app/morning_list.py`.

## A failed run

The run exits 1 when a feed check fails. The workflow still publishes `health.json` and
the scoreboard, then ends failed, and GitHub sends its notice.

| Check | It failed because | What to do |
|---|---|---|
| `feed_answers` | TED did not answer after 5 attempts, or answered in a shape the client does not know. No list was written; the other checks were not run. | Timeout or HTTP 429, 500, 502, 503, 504: run again later, the next run catches up. HTTP 400, "not JSON" or "format changed": see "The feed changes its format". |
| `records_parse` | More than 1% of the records fetched could not be read. Lists were written from the rest. | See "The feed changes its format". |
| `feed_is_fresh` | The newest tender is more than 4 days old. TED does not publish at weekends or on EU holidays. | Look at ted.europa.eu. On a break, wait. If TED has new notices, the query no longer matches them: see the same section. |
| `volume_is_usual` | The latest day has less than 30% of the median of the days before it (up to 20; needs 6 days). | A short day before a holiday does this. If it repeats, check the query and `SCOPE_DIVISIONS`. |

A model problem does not fail the run. A wrong or expired key shows as `errors` in the
`model:` line, and the list is written without assessments. If the Publish step says a
file "does not start with what live-data holds", nothing was pushed and the branch is
intact; run the workflow again.

## Backfill

- **A missed day, or a gap of up to 14 days.** Nothing to repair. The next run writes
  a list for each day it has not recorded. Each list carries the time it was written.
- **A gap longer than 14 days.** Find the last recorded day and run once with a longer
  look back. This reads tenders and awards for the whole gap.
- **Older awards.** `truth` reads awards from the local database, `var/tenderradar.db`.
  It is not in the repository; a new machine holds only what `run` fetched. `backfill`
  adds older notices. It is free and needs no key.

```bash
git show origin/live-data:projects/tender-radar/data/log/live-days.jsonl | tail -n 1 | jq -r .day
gh workflow run live-tender-radar.yml -f lookback_days=40   # on GitHub
uv run tenderradar run --lookback-days 40                   # Compose or your own machine

uv run tenderradar backfill --kind award --from 2026-11-17         # the first live day
uv run tenderradar backfill --kind competition --from 2026-11-17   # tenders, same way
uv run tenderradar truth
```

## The spend cap

At its start the run reads from `live.jsonl` what the lists written today (UTC) have
cost. It adds the cost of each model call and checks the total before the next call.
The default cap is US$0.50 a day. At the cap the run goes on without the model: the
list is written, and the tenders not read are counted in `skipped at the daily cap` and
in the row's `skipped_assessments`. They are not read later. Replayed recordings cost
nothing.

To change the cap set `TENDERRADAR_DAILY_CAP_USD`, for example to `0.25`: as a
repository variable for the workflow (Settings, Secrets and variables, Actions,
Variables), or in the shell or `.env` file for Compose. The cap is not part of the
system version.

## Rotate the key

1. Create a new key in the Anthropic Console.
2. Replace the repository secret `ANTHROPIC_API_KEY`. Under Compose, change it in the
   shell or `.env` file and run `docker compose up -d`.
3. Run once by hand. The `model:` line must show assessments and `0 errors`.
4. Delete the old key in the Console.

## The feed changes its format

The fields asked for (`COMPETITION_FIELDS`, `AWARD_FIELDS`) and the queries are in
`src/tenderradar/feed.py`. The parser (`parse_notice`, `parse_award`) is in
`src/tenderradar/notices.py`.

A `FeedError` means the whole response was not understood: no answer, a status other
than 200, a body that is not JSON, or no `notices` list. TED answers HTTP 400 with the
valid field names when asked for a field it does not know. A rise in unreadable records
means single records lack a publication number, date or procedure identifier.

1. Reproduce it away from the live record. `TENDERRADAR_HOME` moves `var/` and
   `data/log` to another folder. Embeddings are computed again, so it takes minutes.

   ```bash
   TENDERRADAR_HOME="$(mktemp -d)" uv run tenderradar run --no-model
   ```

2. Change the field in `feed.py` and the line that reads it in `notices.py`. Add a
   record in the new shape to the tests (`tests/conftest.py` holds the record shape).
3. Run `uv run pytest -q`, then step 1 again until the four checks pass.
4. If the fix changes what the rankers read, raise `PIPELINE_VERSION` in `log.py`.
5. Commit to `main`, push, and run the workflow with `lookback_days` covering the days
   lost. The local database keeps records as received, so a machine that has it needs
   no second download.

## Go back to an earlier system version

Every log row carries a system version: a hash of the model, the effort, the prompt, the
ranker settings, the list size and the scope. A change starts a new version with its
own line on the scoreboard. Old rows are never rewritten, so going back means running
the earlier code:

```bash
git log --oneline -- src/tenderradar    # find the commit that made the change
git revert <commit>                     # on main, then push
```

The next run computes the earlier hash again and adds to that version's line. If the
change was made with `TENDERRADAR_MODEL` or `TENDERRADAR_EFFORT`, unset the variable.

## Where each file lives

| What | Path | Kept in |
|---|---|---|
| Live lists, day index, live outcomes | `data/log/live.jsonl`, `live-days.jsonl`, `outcomes-live.jsonl` | `main` (first rows), then `live-data` |
| Recorded model responses, live | `data/cassettes/live.jsonl` | same |
| Backtest log, outcomes, settings | `data/log/backtest.jsonl.gz`, `outcomes-backtest.jsonl`, `backtest-meta.json` | `main` |
| Panel profiles, supplier aliases, client profile | `data/profiles/panel.jsonl.gz`, `aliases.json`, `data/client_profile.yaml` | `main` |
| Feed checks, scoreboard, daily report | `var/scoreboard/health.json`, `scoreboard.json`, `index.html`, `eval-report.md` | `live-data` |
| Local database of raw notices | `var/tenderradar.db` | not in git; `backfill` rebuilds it |
| Embeddings already computed | `var/embeddings.sqlite` | not in git; Actions cache |
| Embedding model, about 220 MB | `var/fastembed` in the workflow and the image, else the system temp folder | not in git; Actions cache |
| Bid and skip decisions | `data/corrections/decisions.jsonl` | git-ignored; commit them in a real deployment |
| Settings: scope, list size, cap | `src/tenderradar/config.py` | `main` |

On `live-data` the files are at the same paths as on `main`. `TENDERRADAR_HOME` moves
`var/`, `data/log` and `data/corrections` under another folder.
