# Workflow map

> Fictional customer; times are assumptions for the exercise.

## Today

| Step | Who | System | Time |
|---|---|---|---|
| Alert raised | Monitoring rules | Transaction monitoring | automatic |
| Read the customer file | Level-1 analyst | KYC system | 3 to 5 min |
| Read the transactions around the alert | Level-1 analyst | Core banking | 5 to 15 min |
| Screen unfamiliar names | Level-1 analyst | Screening tool | 2 to 5 min |
| Check earlier alerts | Level-1 analyst | Case management | 2 to 3 min |
| Decide | Level-1 analyst | | 1 to 5 min |
| Write the disposition | Level-1 analyst | Case management | 5 to 10 min |
| Investigate escalations | Level-2 investigator | All of the above | hours |

Total per alert: 20 to 40 minutes, most of it before the decision.

## With the agent, assisted mode

| Step | Who | System | Time |
|---|---|---|---|
| Alert raised | Monitoring rules | Transaction monitoring | automatic |
| Risk features and hard flags | Code | Agent | seconds |
| Gather evidence, propose a disposition | Agent, on pseudonymised data | Agent | under a minute for a routine alert, a few minutes for a hard one |
| Check the proposal against policy | Code | Policy gate | seconds |
| Review the case with real values, approve or override | Level-1 analyst | Case viewer | 2 to 5 min for a routine alert |
| Review closes on flagged alerts | Senior analyst | Case viewer | as today |
| Investigate from scratch when the agent failed or was rejected | Level-1 analyst | As today | as today |
| Investigate escalations, starting from a cited narrative | Level-2 investigator | Case viewer and source systems | shorter; to be measured |

## What changes and what does not

- The analyst still decides every alert. What changes is that the evidence and a
  draft are waiting.
- The four systems are still the source of truth. The agent reads them through
  tools and cites what it read.
- A case the agent could not handle looks exactly like today, plus a note saying why.
- Nothing the agent does is visible to the customer.
