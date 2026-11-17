# Discovery notes

> This is a simulated engagement. "Helios Industrial Supply" is a fictional
> distributor of electrical materials, and the figures below are assumptions made
> for the exercise, not measurements from a real company.

## Who and what

Helios sells cables, transformers, solar mounting hardware and installation
accessories to contractors, utilities and project developers. Quotes are produced
by an inside-sales desk of six people. Discovery was two days sitting with the
desk, plus conversations with the head of sales, the pricing lead and one
warehouse planner.

## The request

The head of sales, in the first conversation:

> "My reps spend the day retyping requests into quotes. I want them checking
> quotes, not building them. And nothing wrong may go out because a machine
> guessed."

## How a quote is made today

1. A request arrives in the shared inbox, as a phone note, or as a forwarded email
   from a field rep. About a third have a PDF or spreadsheet attached.
2. A sales rep reads it and works out what the customer wants. German and English
   are mixed, and product names are whatever the customer calls them.
3. The rep looks up each product in the ERP, one at a time, by typing fragments of
   the description.
4. The rep types quantities into a quote, applies the volume discount from a
   spreadsheet, and checks the lead time.
5. Quotes above EUR 50,000 or with a margin under 12% go to the pricing lead.
6. The quote is sent as a PDF.

## What we observed

| Observation | Detail |
|---|---|
| Volume | About 60 requests per working day, with peaks on Mondays |
| Handling time | About 12 minutes for a typical request, over 30 for a long bill of quantities |
| Response time | Same day for most; two to three days in peak weeks |
| Where the time goes | Product lookup is the largest part, then retyping quantities |
| Typical errors | Wrong cross-section (5x2.5 against 5x25), 10 kV against 20 kV, drums quoted as metres, thousands separators misread |
| What an error costs | A wrong cable on site means a return shipment and a delayed project; the desk lead puts it in the low thousands of euros per incident |
| Sign-off | Done from memory; thin-margin lines are sometimes discounted without anyone noticing |

Things the team said that shaped the design:

- "Half of what I do is translating what the customer calls it into what we call it."
- "I don't mind checking a quote. I mind typing it."
- "If the system guesses the cable type I will stop trusting it the first time it is wrong."
- The pricing lead does not want a model anywhere near a price: "Show me a rule I can read."

## User stories

The request and the observations above, written as what each person needs. The
last two columns say which part of the system answers the story and which number
in the [evaluation report](eval-report.md) checks it.

| # | Who | Story | Answered by | Checked by |
|---|---|---|---|---|
| 1 | Sales rep | I want a priced draft waiting for each request, so that I check a quote instead of typing it | Intake, extraction, matching and pricing | Requests with every line exactly right |
| 2 | Sales rep | I want the system to say when it does not know a product, so that I can trust the matches it makes | The matcher may decline; only a "high" match can be auto-approved | Matcher in isolation: correct products, and wrong products with high confidence |
| 3 | Pricing lead | I want prices and sign-off limits as rules I can read, so that no model decides money | Pricing and guardrails in code, thresholds in one `Policy` object | Must-review quotes that reached a person |
| 4 | Reviewer | I want every held quote to show why it was held, so that I can fix it in minutes | Named guardrail rules and the review queue | Clean quotes held anyway |
| 5 | Head of sales | I want to see in numbers how often it is right, so that I can decide when it may send on its own | The evaluation command, the rule-based baseline and the staged rollout | Unsafe auto-approvals, against the baseline |

The README shows the same five stories as a picture, with the measured numbers
(`docs/request-to-system.png`, rendered by `scripts/render_request_to_system.py`).

## The opportunity

The work splits cleanly:

- Reading the request and naming the products is language work, with high volume
  and high variance. This is where an LLM helps.
- Pricing, discounts, minimum quantities, lead times and sign-off limits are rules
  the business already has. They belong in code.
- Judgement on unclear or risky requests stays with people, and they should be
  shown why a request reached them.

## Success criteria agreed with the head of sales

1. A rep spends minutes checking a draft instead of building a quote.
2. No quote goes out automatically with a wrong product or quantity. Missing a
   risky quote is worse than holding a safe one.
3. Every held quote states the reason.
4. The desk can see, in numbers, how often the system is right before it is
   allowed to send anything on its own.

## Out of scope for the first version

- Customer-specific price lists, freight and credit limits
- Scanned or photographed documents
- Sending the quote; the first version produces a draft
- Writing back into the ERP

## Open questions

- Which requests may be auto-approved at go-live, if any? The proposal is none
  until the shadow period shows the numbers (see the project plan).
- Who owns the catalog synonyms when customers use a new name for a product?
- How long are reviewer corrections kept, and who may see them?
