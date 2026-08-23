"""RAG evaluation from first principles: retrieval metrics, and grounding.

WHAT THIS PACKAGE IS
--------------------
A small, complete, offline implementation of the metrics a retrieval-augmented
answer is graded on — written out rather than imported from Ragas or DeepEval,
because the point of it is to know exactly what each number counts, what its
denominator is, and where it lies to you.

    ragcheck.corpus       a corpus, and an idf-weighted lexical retriever
    ragcheck.dataset      18 questions with gold chunk ids, validated on load
    ragcheck.retrieval    recall@k, precision@k, MRR, nDCG@k, AP@k
    ragcheck.claims       the deterministic answer -> claims split
    ragcheck.generation   groundedness, answer relevance, context recall/precision
    ragcheck.judges       three judges, subclassed from lab.judges
    ragcheck.offline      a deliberately weak support oracle, so it all runs dry
    ragcheck.calibration  18 hand labels, and the gate that refuses the oracle
    ragcheck.report       both halves, one report
    ragcheck.traces       a RAG turn expressed as a lab.trace.Trace

THE THREE IDEAS WORTH THE READ
------------------------------
**1. A RAG turn is a trace, so nothing had to be rebuilt.** Question, retrieve
tool call, retrieved chunks, answer. Expressed that way, `lab.judges` grades it
unchanged, `lab.judges.calibration` measures the judge unchanged, and a claim
that an answer cited a source becomes the same decision-versus-action contract as
a claim that a booking was made — which, for a disclosure a regulated system says
it gave, is the compliance check.

**2. The retrieval half needs no model, and it bounds the other half.** recall@k
is a ceiling on groundedness: a fact absent from the context cannot be in a
grounded answer. So when a grounding number moves, the first question is whether
retrieval moved, and that question is answerable exactly and offline.

**3. The oracle is a parameter; the arithmetic is not.** Every judged metric here
takes a judge. The one that ships is word overlap, it is wrong in ways this
package names and measures, and its calibration report refuses it as a CI gate.
That is the artefact: not "here is a groundedness score", but "here is a
groundedness score, here is the measured error rate of the instrument that
produced it, and here is the gate that would have stopped you quoting it".

RUNS WITH NO API KEY
--------------------
    python -m ragcheck          the worked examples, end to end
    pytest tests/test_ragcheck_*.py

The live path is one argument: pass a judge built with a real completion to
`ragcheck.report.evaluate` and every metric definition stays exactly as it is.

WHAT IS NOT HERE, DELIBERATELY
------------------------------
Embeddings, and therefore every metric defined as a cosine similarity —
including Ragas's own formulation of answer relevancy. Chunking strategy
evaluation. A vector store. See docs/RAG_NOTES.md, which also lists what I have
and have not done with the published frameworks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from ragcheck.corpus import Corpus, LexicalRetriever, load_corpus
    from ragcheck.dataset import RagDataset, load_cases
    from ragcheck.report import RagReport, evaluate

__all__ = [
    "Corpus",
    "LexicalRetriever",
    "RagDataset",
    "RagReport",
    "evaluate",
    "load_cases",
    "load_corpus",
]

# Lazy, for the reason `lab.judges` is lazy: importing a submodule eagerly from a
# package __init__ puts it in sys.modules before `python -m ragcheck` can execute
# it, and runpy then warns about a double import.
_LAZY: dict[str, tuple[str, str]] = {
    "Corpus": ("ragcheck.corpus", "Corpus"),
    "LexicalRetriever": ("ragcheck.corpus", "LexicalRetriever"),
    "load_corpus": ("ragcheck.corpus", "load_corpus"),
    "RagDataset": ("ragcheck.dataset", "RagDataset"),
    "load_cases": ("ragcheck.dataset", "load_cases"),
    "RagReport": ("ragcheck.report", "RagReport"),
    "evaluate": ("ragcheck.report", "evaluate"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    return getattr(importlib.import_module(module_name), attribute)


def __dir__() -> list[str]:
    return sorted(__all__)
