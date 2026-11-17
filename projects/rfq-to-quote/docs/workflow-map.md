# Workflow map

> Simulated engagement with a fictional distributor. Minutes are assumptions used
> for the impact model, not measurements.

## Before

| # | Step | Who | Minutes | Pain |
|---|---|---|---:|---|
| 1 | Open request, read email and attachments | Sales rep | 2 | Mixed languages, forwarded chains |
| 2 | Work out products and quantities | Sales rep | 2 | Units, separators, corrections |
| 3 | Look up each product in the ERP | Sales rep | 4 | Customer wording differs from catalog wording |
| 4 | Type the quote, apply discount | Sales rep | 2 | Retyping; discount table kept in a spreadsheet |
| 5 | Check lead time and minimum quantity | Sales rep | 1 | Often skipped under load |
| 6 | Sign-off if large or thin-margin | Pricing lead | 1 | Depends on the rep noticing |
| | **Total per request** | | **12** | |

## After

| # | Step | Who | Minutes | What changed |
|---|---|---|---:|---|
| 1 | Intake: parse any format to text | Agent | 0 | |
| 2 | Extract customer, dates, line items | Agent (LLM) | 0 | Typed output, gaps reported, no guessing |
| 3 | Match each line to a SKU | Agent (search + LLM) | 0 | May decline; then a person decides |
| 4 | Price with discount rules | Agent (code) | 0 | Same rules, applied every time |
| 5 | Guardrails | Agent (code) | 0 | Lead time, MOQ, margin and value always checked |
| 6a | Auto-approved quote: spot check | Sales rep | 0.5 | Sampled, not every quote |
| 6b | Held quote: review with reasons shown | Sales rep | 3 | Source and draft side by side, edit and approve |
| 7 | Correction saved as an eval case | Agent | 0 | Each mistake becomes a regression test |

## Who decides what

| Decision | Before | After |
|---|---|---|
| What the customer is asking for | Sales rep | Agent proposes, rep confirms on held quotes |
| Which SKU | Sales rep | Agent when the wording pins it down, rep otherwise |
| Price and discount | Rep, from a spreadsheet | Code, from the same table |
| Whether sign-off is needed | Rep's attention | Rule, always applied |
| Whether a quote goes out | Sales rep | Rule for clean quotes, rep for the rest |

## What reaches a person, and why

| Reason code | Meaning |
|---|---|
| `PIPELINE_ERROR` | The file could not be read or a model call failed; the request is not dropped |
| `NO_LINE_ITEMS` | Nothing to quote was found; may not be an RFQ |
| `UNMATCHED_ITEM` | A line has no catalog product, or the request is too vague |
| `LOW_MATCH_CONFIDENCE` | A product was chosen but an attribute had to be assumed |
| `MISSING_QUANTITY` | The customer did not say how much |
| `UNKNOWN_UNIT` | A quantity with no unit |
| `UNIT_MISMATCH` | For example drums requested for a product sold per metre |
| `BELOW_MOQ` | Quantity is under the minimum order |
| `MARGIN_BELOW_FLOOR` | Margin after volume discount is under 12% |
| `HIGH_ORDER_VALUE` | Total is EUR 50,000 or more |
| `LEAD_TIME_RISK` | Requested date is earlier than the product can ship |
| `MISSING_CUSTOMER_NAME` | No company name |
| `MISSING_CUSTOMER_EMAIL` | No address to send the quote to |
| `INTERNAL_RECIPIENT` | The quote would go to one of our own addresses |
| `EMAIL_NOT_IN_SOURCE` | The recipient address does not appear in the document |
| `RECIPIENT_NOT_SENDER` | The recipient is not the external sender of the mail |
| `OPEN_QUESTION` | The request contains a point a rep must resolve |
| `UNREAD_ATTACHMENT` | An attachment could not be read |
| `ASSISTED_MODE` | Auto-approval is switched off; every quote is reviewed |
