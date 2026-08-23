You are checking whether one retrieved passage was worth retrieving.

> Does this passage contain information that helps answer the question?

## Answer PASS when

- The passage states part or all of the answer.
- The passage states a condition, exception or limit that a correct answer would
  have to mention.

## Answer FAIL when

- The passage is about the same general subject but contains nothing the answer
  needs. A passage about cancelling with no deposit does not help a question
  about a party of ten that has paid one.
- The passage is on an unrelated topic.
- The passage would only mislead: it states a rule for a different case than the
  one asked about.

## Rules

- One passage at a time. Do not consider what the other retrieved passages said,
  and do not reward a passage for being adjacent to a useful one.
- Partial help is a PASS. A passage carrying one of the two facts an answer needs
  was worth retrieving.
- You must be able to quote the sentence in the passage that helps.

QUESTION
--------
{{question}}

PASSAGE
-------
{{context}}

## Output

Reply with a single JSON object and nothing else:

{"verdict": "pass" or "fail", "quote": "the sentence that helps, or null", "critique": "one or two sentences explaining the decision"}
