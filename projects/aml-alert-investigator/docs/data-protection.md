# Data protection

How this project keeps customer data away from the models, what that does and does
not achieve, and what a real deployment would add. These are engineering controls
demonstrated on synthetic data. This is not legal advice and not a compliance claim.

## The boundary

Real identifiers live in the bank's data and in the token vault. Models work on the
other side of a gateway and see tokens.

| Inside the boundary | Outside |
|---|---|
| Source data, risk features, name screening, the token vault, the analyst's case viewer | Model calls: triage, lead, workers |

## Controls

| Control | What it does | How it is checked |
|---|---|---|
| Pseudonymisation | Names, IBANs, customer and account numbers, emails, phones become case-scoped tokens | Unit tests; the leak scan |
| Per-case tokens | The same token means a different party in each case | Unit test |
| Minimisation | A field allow-list per role; date of birth becomes an age band | Unit test that the triage view has no name, date of birth, address, email, phone or city |
| Free-text scrubbing | Known parties become tokens, in any case and from a surname alone; unknown capitalised words are redacted; emails, IBANs, phone and card numbers are replaced whatever separators they are written with; the bank's own ids become tokens | Measured on every free-text field in the dataset: 188 of 188 fields that hold an identifier are clean afterwards |
| Sensitive categories | Health, religion, political and trade-union counterparties are withheld | Unit test |
| Need-to-know | The lead may request a withheld category with a justification; logged and flagged | Evaluation counts requests and how many were on alerts where it was relevant |
| Screening in code | The model gets comparison facts, never the watchlist name or the customer name | Unit test |
| Outbound check | Every request is scanned for known identifiers and identifier patterns; a hit blocks the call and routes the case to a person | Unit test with a planted identifier |
| Leak scan | Everything recorded as sent to a model is scanned again, against the full identifier inventory plus names planted in free text | `aml-agent leak-check`, in CI |
| No future data | Tools never return anything dated after the alert | Unit test |
| Read-only | No tool writes, files, blocks or contacts | By construction |

## What the controls do not do

- **Pseudonymised data is still personal data.** The bank can reverse the tokens,
  and a pattern of transactions can identify a person without a name. The controls
  reduce what an outside party receives; they do not make it anonymous.
- **Scrubbing is vocabulary-based.** It over-redacts unusual words, and it passes
  an unknown name written entirely in lower case. The outbound check and the leak
  scan know the bank's identifier inventory and the common identifier patterns, so
  they catch what the scrubber missed among those, and nothing else. The scrubber
  figure is measured with that same knowledge plus names planted in free text; it
  is not evidence about names nobody has listed.
- **Amounts, dates and occupations are sent.** The investigation needs them.
- **The category of a sensitive counterparty can be requested.** The control is the
  trace it leaves, not a prohibition.

## What a real deployment would add

- Model access through the channel the bank has already approved, in its region,
  under terms that exclude retention and training. The provider interface is where
  that plugs in.
- Key management and access control for the token vault, separate from the store
  of case traces.
- A retention period for traces and recordings, and deletion on schedule.
- Access control on the case viewer, since it shows real values.
- A name recogniser running inside the bank as a third line for free text.
- A data protection impact assessment with the data protection officer, covering
  the points in "What the controls do not do".

## The two vendors in this repository

Recorded runs in this repository send only synthetic data, and only to Claude. The
NVIDIA-hosted configuration has not been run: nothing in this repository was sent
to that endpoint. It targets a trial endpoint meant for development and testing,
and it is here to show that the system is not tied to one vendor, not as a
recommendation for where a bank should send data.

## Tipping off

A customer must not learn that they are being investigated. The agent has no
customer-facing channel and no write access to any system a customer can see.
