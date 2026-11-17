# Discovery notes

> This is a simulated engagement. "Fernhill Home" is a fictional online retailer of
> home and living products selling in six European countries, and the figures
> below are assumptions made for the exercise, not measurements from a real
> company.

## Who and what

Fernhill sells furniture, lighting, textiles and garden products through a web
shop and two apps. A commerce operations team of five watches the business
during the day; one engineer is on call at night and at weekends. Discovery was
two days with the operations team and the on-call rota, plus conversations with
the head of e-commerce, the payments lead, the marketing lead and the analyst who
owns the dashboards.

## The request

The head of e-commerce, in the first conversation:

> "We find out on Monday that something broke on Saturday. I want to know within
> hours, I want to know what it was, and I don't want a robot switching things off
> in my shop."

## How an incident is handled today

1. A dashboard alert fires, or more often a customer-service agent notices
   complaints, or someone sees a weak daily number the next morning.
2. The on-call opens five or six dashboards and tries to find where the number
   is off: which country, which app, which payment method.
3. They scroll through the deployment channel, the coupon tool, the pricing
   import log and the payment providers' status pages, looking for something
   that changed at about the right time.
4. They call the team they think owns it. Often it is the wrong team first.
5. That team rolls back, switches off or fails over, by hand.

## What we observed

| Observation | Detail |
|---|---|
| How incidents are found | Roughly half through customer service or the next day's numbers, not through an alert |
| Time to notice | Hours at best; a weekend incident can run until Monday |
| Dashboard alerts | Fixed percentage thresholds. They fire for the whole of every promotion, so the on-call has muted most of them |
| Where the time goes | Finding where the problem is, then finding what changed. The fix itself usually takes minutes |
| Changes per day | About 15 across releases, coupons, prices, campaigns, tags and configuration. The nearest change is often not the cause |
| Typical incidents | A payment provider failing in one country; a release breaking checkout on one platform; a coupon that stacks; a price with a slipped decimal; bot traffic; a tracking tag that stops counting |
| What an incident costs | A card outage in the largest market loses several thousand euros of revenue an hour. A mispriced product loses margin on every order until someone notices |

Things the team said that shaped the design:

- "The alert says revenue is down. I know revenue is down. Tell me where."
- "During a sale everything is red. So I stopped looking at red."
- "Half the time the thing that changed is in a tool I don't have open."
- The payments lead: "If it tells me psp_alpha is failing I want to see the
  number, not a sentence."
- The on-call: "I will not approve something at three in the morning because a
  chatbot sounded sure."

## User stories

The request and the observations above, written as what each person needs. The
last two columns say which part of the system answers the story and which number
in the [evaluation report](eval-report.md) checks it.

| # | Who | Story | Answered by | Checked by |
|---|---|---|---|---|
| 1 | On-call engineer | I want to hear about an incident within hours and be told where it is, so that I call the right team first | The forecasting detector over 706 slices, and grouping that names one slice per incident | Incident recall, time to detect, localisation accuracy |
| 2 | On-call engineer | I want the pager quiet during planned sales and holidays, so that I still look when it goes off | Calendar-aware forecasting, false-alarm control, the `EXPECTED_EVENT` check | False incidents per week, false pages per week |
| 3 | Payments, marketing and pricing leads | I want the likely cause with the numbers behind it, so that I can check it in a minute | The investigator with read-only tools; every cited number recomputed by code | Root-cause accuracy, evidence validity, correct declines |
| 4 | Head of e-commerce | I want nothing switched off without a person saying yes, and never the wrong thing, so that the cure is not worse than the incident | Closed list of reversible mitigations, guardrails on the target, approval queue, executor that acts once | Unsafe mitigation proposals |
| 5 | Head of e-commerce | I want to see in numbers how good it is before anyone relies on it, so that I can decide how far to trust it | One evaluation command, a scripted baseline, a held-out world, a staged rollout | The evaluation report as a whole; the script against the agent |

The README shows the same five stories as a picture, with the measured numbers
(`docs/request-to-system.png`, rendered by `scripts/render_request_to_system.py`).

## The opportunity

The work splits cleanly:

- Noticing that a number is off is statistics, 706 series every hour. It has to
  be cheap, calibrated and repeatable. This is a forecasting model, not a
  language model.
- Finding what changed means reading across tools and free-text notes and
  following whichever lead the data offers. This is where a language model with
  tools can help, and where a fixed script runs out.
- Whether a cited number is true, whether an action is allowed, and who is
  paged are rules. They belong in code.
- Switching something off in the shop is a decision. It stays with a person.

## Success criteria agreed with the head of e-commerce

1. An incident reaches the right team within a few hours of starting, not the
   next morning through customer service.
2. The pager stays quiet on planned promotions and public holidays.
3. Every alert arrives with verified evidence and a named cause, or says plainly
   that no cause was found.
4. No mitigation is proposed against the wrong target.
5. The team can see in numbers how the system performs before it is trusted.

## Out of scope for the first version

- Acting without approval, for any class of incident
- Customer-level or order-level data; the system sees hourly aggregates
- Incidents shorter than an hour
- Paging and ticketing integrations; the first version fills a queue
- Undoing an executed action from the review page

## Open questions

- Who may approve a mitigation at night: the on-call alone, or the on-call plus
  the owning team's lead for anything that pages?
- Which admin APIs exist in reality, and do they accept an idempotency key? The
  mock service assumes they do.
- How long are investigation traces kept, and who may read them?
- The page threshold of EUR 2,000 an hour is a placeholder. Finance should set it.
