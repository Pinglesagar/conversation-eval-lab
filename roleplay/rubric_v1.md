You are grading one recorded sales-roleplay session. A trainee adviser has
practised a pitch for a balanced growth fund against a simulated retail customer.
Decide whether the session meets the certification standard.

Score each criterion out of 4, then total out of 20. The session passes at 14.

1. **Discovery** — did the trainee find out what the customer needs before
   describing the product? Open questions count; confirmations do not.
2. **Objection handling** — was every objection the customer raised actually
   engaged with, rather than acknowledged and abandoned?
3. **Mandatory disclosure** — were the disclosures this market requires given?
   The requirement set is fixed per jurisdiction and is recorded in the session's
   disclosure register. Grade against the register, not against vocabulary: a
   sentence containing the word "risk" is not a risk warning.
4. **No unlicensed advice** — did the trainee stay on the product's features and
   the customer's stated needs, without making a personal recommendation? Any
   in-session compliance flag is dispositive.
5. **Closing** — did the trainee ask for the business, and did a summary of what
   was agreed precede the ask?

A session fails outright, whatever it totals, if a required disclosure is missing
or if the trainee made a personal recommendation.

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
      "evidence": "<a span quoted verbatim from the transcript>"
    }

Every criterion is an integer from 0 to 4. `evidence` must be copied verbatim
from the transcript above, not paraphrased.
