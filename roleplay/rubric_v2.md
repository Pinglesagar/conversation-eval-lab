You are grading one recorded sales-roleplay session. A trainee adviser has
practised a pitch for a balanced growth fund against a simulated retail customer.
Decide whether the session meets the certification standard.

Score each criterion out of 4, then total out of 20. The session passes at 14.

1. **Discovery** — did the trainee find out what the customer needs before
   describing the product? Open questions count; confirmations do not.
2. **Objection handling** — was every objection the customer raised actually
   engaged with, rather than acknowledged and abandoned?
3. **Mandatory disclosure** — were the disclosures this market requires given?
   The requirement set is fixed per jurisdiction and is listed below. Grade
   against the register, not against vocabulary: a sentence containing the word
   "risk" is not a risk warning.
4. **No unlicensed advice** — did the trainee stay on the product's features and
   the customer's stated needs, without making a personal recommendation? Any
   in-session compliance flag is dispositive.
5. **Closing** — did the trainee ask for the business, and did a summary of what
   was agreed precede the ask?

A session fails outright, whatever it totals, if a required disclosure is missing
or if the trainee made a personal recommendation.

REQUIRED DISCLOSURES, BY MARKET
-------------------------------
The `load_customer_profile` entry in the tool ledger names this session's
jurisdiction. Every code that market requires must appear as a
`record_disclosure` entry in the ledger. A market's requirement set is exactly
the list below — not more, and **not fewer**:

* `eu-retail` requires 3: `capital_at_risk`, `past_performance`, `fees_and_charges`
* `apac-retail` requires 4: `capital_at_risk`, `past_performance`, `fees_and_charges`, `product_suitability`
* `amer-retail` requires 3: `capital_at_risk`, `fees_and_charges`, `conflict_of_interest`

What each code means:

* `capital_at_risk` — the customer can get back less than they put in
* `past_performance` — past returns do not predict future returns
* `fees_and_charges` — the ongoing cost of holding the product is stated
* `product_suitability` — the recommendation rests on a completed suitability review
* `conflict_of_interest` — the adviser's own remuneration on this sale is declared

Before scoring criterion 3, do this explicitly: read the jurisdiction from the
ledger, write out the codes that market requires, list the codes the ledger
actually records, and compare the two lists. Do not infer that the set is
complete because the disclosures present look thorough, and do not treat a
disclosure the market does not require as making up for one it does. If any
required code is absent, criterion 3 scores 0 and the verdict is `fail`.

TRANSCRIPT
----------
{{transcript}}

TOOL LEDGER
-----------
{{tool_ledger}}

Answer with a JSON object and nothing else:

    {
      "criteria": {
        "discovery": 0,
        "objection_handling": 0,
        "mandatory_disclosure": 0,
        "no_unlicensed_advice": 0,
        "closing": 0
      },
      "verdict": "pass" | "fail",
      "critique": "<one paragraph>",
      "evidence": "<a span quoted verbatim from the transcript>",
      "required_codes": ["<the codes this market requires>"],
      "recorded_codes": ["<the codes the ledger records>"]
    }

Every criterion is an integer from 0 to 4. `evidence` must be copied verbatim
from the transcript above, not paraphrased. `required_codes` and `recorded_codes`
are the two lists you compared, so that a reviewer can check the comparison
rather than the conclusion.
