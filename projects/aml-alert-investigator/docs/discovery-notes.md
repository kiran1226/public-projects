# Discovery notes

> Simulated engagement with a fictional bank ("Harbourline Bank"). The notes show
> what discovery would produce before any code is written. Figures are assumptions
> for the exercise.

## Who was interviewed

| Person | Role | What they own |
|---|---|---|
| Head of financial crime | Sponsor | Alert backlog, regulatory deadlines, headcount |
| Two level-1 analysts | Daily users | Working alerts, writing dispositions |
| Level-2 investigator | Receives escalations | Full investigations, reports to the authority |
| Data protection officer | Approver | What data may be processed where |
| IT security lead | Approver | Which external services the bank may call |

## The request, in the sponsor's words

"My analysts spend the day clearing alerts that turn out to be nothing, while real
cases wait in the same queue. Prepare the routine ones for them. But no customer
data leaves the bank, and no machine closes a case on its own."

## How an alert is worked today

1. The monitoring system raises an alert on a rule: cash deposits under the
   reporting threshold, money leaving soon after arriving, activity that does not
   fit the profile, a watchlist name, a high-risk country, a large amount.
2. The analyst opens the customer file for occupation, expected turnover and notes.
3. They open the transaction history and read the weeks around the alert.
4. They screen names if a counterparty looks unfamiliar.
5. They check earlier alerts on the customer.
6. They decide: close as a false positive with a reason, escalate to level 2 with a
   narrative, or ask the relationship manager a question.
7. They write it up in the case management system.

Steps 2 to 5 are copying facts between four systems. Step 6 takes a minute when the
file explains the activity and much longer when it does not.

## What we heard

- "Nine out of ten I could close in my sleep, but I still have to prove why." The
  write-up, not the decision, is the cost of an easy alert.
- "If it closes one real case, we are finished." The sponsor would accept slower
  over wrong. A missed suspicious alert is the error that matters.
- "I do not trust a summary I cannot check." The level-2 investigator re-does any
  escalation whose claims are not tied to transactions.
- "The notes field explains half of them." Documented sales, seasonal businesses
  and bonuses are in the file as free text, which rules cannot read.
- "Names, IBANs and what people pay for are not going to an outside model." The
  data protection officer's condition. Payments to clinics, religious communities,
  parties and unions were named specifically.
- "I need to see what it looked at." Security and audit want a full trail per case.

## User stories

| # | As a | I want | So that |
|---|---|---|---|
| 1 | Level-1 analyst | the evidence gathered and a draft recommendation per alert | I review a case instead of opening four systems |
| 2 | Head of financial crime | no suspicious alert closed by the system | speed never costs us a missed case |
| 3 | Data protection officer | no real customer identifier to reach an external model | using AI does not widen our exposure |
| 4 | Level-2 investigator | every claim tied to evidence I can open | I can trust an escalation without redoing it |
| 5 | Operations lead | the expensive model used only where a case needs it | cost per alert stays predictable |

Each story has an answer in the system and a number in the evaluation. The README
shows the mapping as a picture (`docs/request-to-system.png`).

## Success criteria agreed with the sponsor

| Criterion | Measure |
|---|---|
| No suspicious alert is closed | Suspicious closed = 0 on the labelled set, at every tier |
| Nothing identifying leaves | Leak scan over everything sent to a model finds nothing |
| Escalations can be trusted | Every cited item was retrieved in the case; key evidence is cited |
| Analysts save time | Share of alerts that arrive with a usable recommendation |
| Cost is known | Cost per alert, by tier, with a hard cap on spend |

## What we decided not to do

- Auto-closing from day one. The sponsor wants evidence first; assisted mode is the
  default.
- Replacing the monitoring rules. Alerts are inputs.
- Filing reports or blocking accounts. The agent has no write access.

## Open questions for the customer

- Which closed alerts can be used as labelled examples, and who confirms the labels?
- Which model channel has the bank already approved, and in which region?
- How long must case traces be kept, and who may see the re-identified view?
