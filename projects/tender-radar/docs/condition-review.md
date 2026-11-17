# Review of the conditions the model stated

The gate checks by code that every quoted passage is in the notice. It cannot check two
other things: whether the model's English sentence says what the passage says, and
whether its "met" judgement follows from the client profile. Those were read one by one.

**What was read.** Every condition the model stated for the fixture day, 2026-05-12:
the 74 tenders that passed the client's rules, of which
16 carried 26 conditions between them.
The notices are in German, Polish, Czech and English. The recorded answers are in
`data/cassettes/fixture.jsonl` and the notices in `data/fixture/`, so the reading can be
repeated.

**Result.**

- The English sentence says what the quoted passage says: 25 of 26. One adds a
  clause that is not in its passage.
- The "met" judgement is reasonable given the profile: 24 of 26. Two say "no" where
  the profile is silent and "unknown" would be safer.
- No condition was invented: all 26 quoted passages are in their notices (checked by
  the gate, and by `tests/test_fixture_demo.py`).

**How far to trust it.** One reading of one day, by one reviewer who is not a
procurement lawyer. It says the conditions are usable as pointers to the right sentence
of the notice. It does not say they are complete: conditions that sit only in the
tender documents, which the radar does not read, are not here.

| Notice | Condition as the model wrote it | Met | Sentence matches its passage | "Met" judgement |
|---|---|---|---|---|
| 322977-2026 | Supplier must have completed at least one delivery of one ultrasound machine worth at least PLN 380,000 gross in the last 3 years, with references. | no | Yes | Reasonable |
| 323326-2026 | Net annual turnover above EUR 200,000 in laboratory equipment delivery in 2023, 2024 and 2025 | yes | Yes | Reasonable |
| 323326-2026 | At least two reference projects delivering laboratory equipment in 2023 to 2025 | unknown | Yes | Reasonable |
| 323326-2026 | Valid ISO 9001 certificate or equivalent quality management evidence | yes | Yes | Reasonable |
| 323534-2026 | Average annual turnover of at least EUR 1,380,000 gross over the last three years. | yes | Yes | Reasonable |
| 323534-2026 | Professional or operator liability insurance scaled to the contract value. | unknown | Yes | Reasonable |
| 323534-2026 | At least three comparable references for mK dilution cryostats with base temperature below 10 mK and a magnet of 14 T or more, delivered to research institutions in the last five years. | no | Yes | Reasonable |
| 323601-2026 | Valid marketing authorisation for the medicinal product in Poland or EU authorisation, submitted with the offer | no | Yes | Reasonable |
| 323824-2026 | Tenderer must submit registration proof for the legal entity (commercial register extract, not older than 6 months, simple copy). | unknown | Yes | Reasonable |
| 323966-2026 | Offered products must hold all necessary certificates, including declarations of conformity. | no | Yes | Debatable: the profile does not say; "unknown" would be safer |
| 324032-2026 | Product must hold a current MRiRW authorisation for use and trade and appear on the IBL list of products recommended for forestry use. | no | Yes | Reasonable |
| 324110-2026 | The offered medical device must hold certificates and be registered as the law requires. | unknown | Yes | Reasonable |
| 324200-2026 | Valid marketing authorisation for the medicinal product in Poland or EU authorisation, valid on the offer date. | no | Yes | Reasonable |
| 324293-2026 | Civil liability insurance of at least PLN 1,700,000 for the business related to the subject of the contract. | unknown | Yes | Reasonable |
| 324293-2026 | Completed contract worth at least PLN 1,700,000 gross in the last 3 years covering water system modernisation, including water meter replacement and a smart network management system. | no | Yes | Reasonable |
| 324554-2026 | Products must be registered as medical devices under the Polish Medical Devices Act and EU MDR 2017/745 | no | Yes | Debatable: true for the products the client sells, but the profile does not say so in these words |
| 324554-2026 | Lot 1 item 2 must be a medicinal product meeting the Pharmaceutical Law | no | Yes | Reasonable |
| 324595-2026 | At least one reference of comparable deliveries from the last three years, with value, date and public or private recipient, plus contact details | unknown | Yes | Reasonable |
| 324715-2026 | Entry in the commercial register as a manufacturer of FFP masks | no | Yes | Reasonable |
| 324715-2026 | Production capacity for FFP2 masks within 20 km of the Hamburg city border | no | Yes | Reasonable |
| 324715-2026 | At least 200 pallet spaces storage within 20 km of the Hamburg city border | unknown | Yes | Reasonable |
| 324715-2026 | Reference list of at least three comparable supplies of FFP2 masks, one to a hospital | no | Yes | Reasonable |
| 324715-2026 | Liability insurance with 1 million EUR cover for property and personal injury | unknown | Yes | Reasonable |
| 324791-2026 | Warranty and statutory warranty period as set in Annex 1 to the SWZ; shorter periods lead to rejection. | unknown | Partly: "shorter periods lead to rejection" is not in the quoted passage | Reasonable |
| 326339-2026 | Medicinal products must hold a current marketing authorisation under the Polish Pharmaceutical Law. | no | Yes | Reasonable |
| 326603-2026 | Supplier must show at least one completed delivery of a specialist medical device with installation and commissioning, worth at least 300,000 PLN gross, in the last 3 years. | unknown | Yes | Reasonable |
