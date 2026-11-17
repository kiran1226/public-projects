# Impact estimate

> A model with stated assumptions for a fictional bank, not a measurement. Its
> purpose is to show which numbers the business case depends on, so they can be
> measured in shadow mode.

## Assumptions

| Input | Value | Source |
|---|---|---|
| Alerts per working day | 220 | Assumed |
| Working days per year | 250 | Assumed |
| Analyst time per alert today | 30 minutes | Assumed (range 20 to 40) |
| Share of alerts that are routine | 85% | Assumed; a production queue has far fewer hard cases than the evaluation set, which is enriched on purpose |
| Analyst time to review a prepared routine alert | 4 minutes | Assumed; to be measured in shadow mode |
| Analyst time to review a prepared hard alert | 15 minutes | Assumed |
| Share of alerts where the agent gives no usable recommendation | 10% | Assumed; these take as long as today |
| Loaded cost of an analyst hour | EUR 55 | Assumed |

Model cost per alert, measured on the evaluation set: $0.003 with Haiku in every
role, $0.032 routed and $0.030 with Opus alone. At 220 alerts a day that is under
USD 1 a day with Haiku and about USD 7 with either Opus setup, all small against
the analyst time below. The evaluation
set is enriched with hard cases, so these are more likely high than low for a
production queue.

## Time

Per day, with the assumptions above:

| | Alerts | Minutes each | Hours |
|---|---|---|---|
| Today | 220 | 30 | 110 |
| Routine, prepared | 168 | 4 | 11 |
| Hard, prepared | 30 | 15 | 8 |
| No usable recommendation | 22 | 30 | 11 |
| With the agent | 220 | | 30 |

That is about 80 analyst hours a day, or roughly EUR 1.1 million a year at the
assumed rate. The figure is only as good as the two review times, which is why
shadow mode measures them first.

## What the estimate leaves out

- **The value of faster escalation.** Suspicious cases reach level 2 sooner, with a
  cited narrative. This is the benefit the sponsor cares most about and the hardest
  to price.
- **The cost of an error.** One missed suspicious alert outweighs a year of saved
  time, which is why the go-live gate is zero suspicious alerts closed and why
  assisted mode is the default.
- **Review quality.** If analysts approve without reading, the saving is real and
  the control is gone. Sampled quality checks and logged overrides exist for this.
- **Engineering and running costs.** Integration with the bank's systems, the
  model channel, and upkeep of the scrubbing vocabulary and the labelled set.

## What would change the answer

| If | Then |
|---|---|
| Review of a routine alert takes 10 minutes, not 4 | The saving falls by about a fifth |
| The agent gives no usable recommendation on 30% of alerts | The saving falls by about a fifth |
| Hard alerts are 30% of the queue, not 15% | Model cost per alert rises and the time saving falls |
| Auto-close is enabled for the lowest-risk class | Review time for that class drops to the sampled share |
