# Workflow map

> Fictional customer; times are assumptions for the exercise.

## Today

| Step | Who | System | Time |
|---|---|---|---|
| Notice that something is wrong | Customer service, or whoever reads the morning numbers | Dashboards, complaint inbox | hours to days |
| Find where it is | On-call engineer | Five or six dashboards | 15 to 45 min |
| Find what changed | On-call engineer | Deployment channel, coupon tool, pricing import log, provider status pages | 15 to 60 min |
| Find the owner | On-call engineer | Phone, chat | 5 to 30 min; often the wrong team first |
| Fix | Owning team | Admin tools | minutes |

Total from start to fix: several hours on a working day, up to two days at a weekend.
Most of it passes before anyone knows there is a problem.

## With the agent, approved-mitigation mode

| Step | Who | System | Time |
|---|---|---|---|
| Score every slice of five metrics | Code (ML detector) | Revenue incident agent | every hour, about a second |
| Group alarms into one alert that names a slice | Code | Revenue incident agent | seconds |
| Investigate: where, since when, what changed | Scripted investigator or agent, read-only tools | Revenue incident agent | seconds for the script; 8 to 10 s of model time on average for the agent, 18 s at most, in the recorded run |
| Recompute every cited number; check cause, target and calendar | Code | Guardrails | seconds |
| Set severity and owner | Code | Policy | seconds |
| Read the brief and the evidence; approve, correct or reject | On-call engineer | Review queue | 2 to 5 min |
| Execute the approved mitigation, once | Code, after approval | Executor and admin API | seconds |
| Investigate from scratch when the finding is `unknown` or held | On-call engineer | As today, starting from the named slice and the trace | shorter; to be measured |

## What changes and what does not

- The on-call still decides every action. What changes is that the alert names
  a place, and the evidence and a draft cause are waiting.
- The admin tools are still where things are switched off. The system calls them
  only after an approval, with a key that makes the call safe to repeat.
- An incident the system could not explain looks like today's, plus a slice, a
  start time and a list of what was looked at.
- A planned promotion is not an alert. An incident that starts inside one is.
- Nothing the model reads or writes is visible to a customer, and no
  customer-level data reaches it.
