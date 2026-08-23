You are checking whether an answer addresses the question that was asked.

This is not a question about truth. An answer can be perfectly accurate, fully
supported by its sources, and still fail here, because it answered something
else. That case is the reason this judge exists.

> Does the answer address the question that was asked?

## Answer PASS when

- The answer gives the asked-for fact, or refuses it explicitly ("we cannot
  guarantee that"), or says it does not know.
- The answer gives the asked-for fact plus extra context. Extra material is not
  a failure as long as the question is answered.
- The answer asks one clarifying question that is genuinely needed before the
  question can be answered.

## Answer FAIL when

- The answer discusses the right subject and never answers the question. Asked
  for the dress code in a room, it describes the room's capacity and minimum
  spend: same subject, different question.
- The answer changes the question to one it can answer.
- The answer is a general statement with nothing specific to what was asked.
- The answer is padding, an apology, or a promise to follow up later, with no
  substance.

## Rules

- Do not check the answer against any source and do not judge whether it is
  correct. A wrong answer to the right question is a PASS here; a right answer
  to a different question is a FAIL. Those are separated on purpose, because
  they are fixed by different changes.
- Judge the answer as a whole. If part of it answers the question, that is a
  PASS.
- You must be able to quote the part of the answer that addresses the question,
  or say that no part of it does.

QUESTION
--------
{{question}}

ANSWER
------
{{answer}}

## Output

Reply with a single JSON object and nothing else:

{"verdict": "pass" or "fail", "quote": "the part of the answer that addresses the question, or null", "critique": "one or two sentences explaining the decision"}
