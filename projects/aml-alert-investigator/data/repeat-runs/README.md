# Repeat runs

Each model configuration was run three more times, live, on all 40 alerts, after
the recorded run. These two files are the scored result of those nine runs, next to
the recorded run for comparison. They back the repeat-run figures in the project
README.

| File | One row per | Columns |
|---|---|---|
| `runs.csv` | run (12 rows) | right outcomes, suspicious alerts closed, typology, tier-one closes, cases with no recommendation, leak-scan hits, steps and cost |
| `outcomes.csv` | alert in a run (480 rows) | gold label, the outcome recommended, the tier that handled it, the review queue, and whether the outcome was right |

`run` is `recorded` for the recording in `data/cassettes/`, and `1` to `3` for the
repeats. An `outcome` of `none` means the case went to manual investigation with no
recommendation.

How they were made: each repeat was a live run into its own empty recording
folder, so nothing was replayed from an earlier run. Each recording was then
scored with `evals.score_case`, the function behind `aml-agent eval`, and scanned
with the leak scan. The `recorded` rows can be reproduced from this repository
with `uv run aml-agent eval`.

What is not here: the nine repeat recordings themselves (about 11 MB). The rows
for runs 1 to 3 therefore cannot be re-derived from this repository; they are a
summary of data that was not committed. The nine runs cost $7.42, and an aborted
first attempt a further $0.36.
