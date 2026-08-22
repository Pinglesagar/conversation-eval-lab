"""Tests for personas, goals and their YAML loading.

WHAT THIS DEMONSTRATES
----------------------
The scenario definition is the part of an eval suite most likely to be edited by
someone in a hurry, so the validation of it is worth testing properly. The
load-bearing case here is `test_goal_rejects_a_gated_fact_that_does_not_exist`: a
typo in `on_request_only` would otherwise create a fact the agent can never be
asked for, and the scenario would pass for the wrong reason — a silent green,
which is the failure mode this whole repo exists to prevent.
"""

from __future__ import annotations

import pytest

from lab.simulator.persona import (
    RELUCTANT_BELOW,
    CallerProfile,
    Goal,
    Persona,
    load_yaml_mapping,
)

PROFILE_YAML = """
persona:
  name: brisk_regular
  style: You are a regular customer in a hurry. Clipped, polite, no small talk.
  verbosity: terse
  cooperativeness: 1.0
  accent: en-GB-south
  notes: The common case, and the one an agent should never fail.
goal:
  intent: book a table for six on Friday
  facts:
    party_size: six
    date: Friday
    time: half seven
    dietary: one of us is coeliac
  on_request_only:
    - dietary
  ask_patterns:
    dietary:
      - dietary
      - allergies
      - anything we should know
  reply_templates:
    dietary: "Actually yes — {value}."
  success_criteria:
    - a booking exists for six on Friday
    - the dietary requirement reaches the booking notes
"""


def _profile(tmp_path, text: str = PROFILE_YAML) -> CallerProfile:
    path = tmp_path / "caller.yaml"
    path.write_text(text, encoding="utf-8")
    return CallerProfile.from_yaml(path)


def test_profile_loads_from_yaml(tmp_path) -> None:
    profile = _profile(tmp_path)
    assert profile.persona.name == "brisk_regular"
    assert profile.persona.verbosity == "terse"
    assert profile.goal.intent == "book a table for six on Friday"
    assert profile.goal.gated_keys() == ["dietary"]
    assert profile.goal.volunteered_keys() == ["party_size", "date", "time"]


def test_gated_facts_are_asked_for_only_by_declared_patterns(tmp_path) -> None:
    goal = _profile(tmp_path).goal
    assert goal.is_asked_for("dietary", "Any DIETARY requirements at all?") is True
    assert goal.is_asked_for("dietary", "And what time would you like?") is False
    # No declared pattern means no match, rather than a guess based on the key
    # name — the caller's behaviour must not depend on how a fact was spelled.
    assert goal.is_asked_for("party_size", "how many in the party?") is False


def test_reply_template_is_used_when_the_caller_speaks_a_fact(tmp_path) -> None:
    goal = _profile(tmp_path).goal
    assert goal.spoken("dietary") == "Actually yes — one of us is coeliac."
    assert goal.spoken("party_size") == "six"


def test_goal_rejects_a_gated_fact_that_does_not_exist() -> None:
    with pytest.raises(ValueError, match="on_request_only names facts that do not exist"):
        Goal(intent="book", facts={"date": "Friday"}, on_request_only=["dietry"])


def test_goal_rejects_ask_patterns_and_templates_for_unknown_facts() -> None:
    with pytest.raises(ValueError, match="ask_patterns names facts"):
        Goal(intent="book", facts={"date": "Friday"}, ask_patterns={"nope": ["x"]})
    with pytest.raises(ValueError, match="reply_templates names facts"):
        Goal(intent="book", facts={"date": "Friday"}, reply_templates={"nope": "{value}"})


def test_reply_template_must_interpolate_the_value() -> None:
    with pytest.raises(ValueError, match=r"reply_templates must contain"):
        Goal(
            intent="book",
            facts={"date": "Friday"},
            reply_templates={"date": "some Friday or other"},
        )


def test_cooperativeness_has_a_stated_deterministic_effect() -> None:
    eager = Persona(name="eager", style="helpful", cooperativeness=1.0)
    reluctant = Persona(name="distracted", style="distracted", cooperativeness=0.2)
    assert eager.asks_required == 1
    assert reluctant.asks_required == 2
    assert reluctant.is_reluctant and not eager.is_reluctant
    # The threshold is a published constant, so a scenario author can tell which
    # values are behaviourally different.
    assert Persona(name="edge", style="s", cooperativeness=RELUCTANT_BELOW).asks_required == 1


def test_cooperativeness_is_bounded() -> None:
    with pytest.raises(ValueError):
        Persona(name="over", style="s", cooperativeness=1.5)


def test_prompt_includes_the_disclosure_rule_and_the_end_sentinel(tmp_path) -> None:
    profile = _profile(tmp_path)
    prompt = profile.system_prompt()
    # The gated fact must be present (the caller knows it) and explicitly gated.
    assert "one of us is coeliac" in prompt
    assert "unless you are asked for it directly" in prompt
    assert "[END OF CALL]" in prompt
    # Volunteered facts are offered up front.
    assert "State these up front" in prompt


def test_trace_metadata_records_keys_but_not_gated_values(tmp_path) -> None:
    metadata = _profile(tmp_path).trace_metadata()
    assert metadata["persona"] == "brisk_regular"
    assert metadata["goal_gated_keys"] == ["dietary"]
    assert metadata["goal_fact_keys"] == ["date", "dietary", "party_size", "time"]
    # The point of a gated fact is that the agent has to ask for it. Writing its
    # value into the trace would put the answer next to the transcript in every
    # bug report the trace gets pasted into.
    assert "coeliac" not in repr(metadata)


def test_unknown_yaml_field_is_rejected_rather_than_ignored(tmp_path) -> None:
    path = tmp_path / "typo.yaml"
    path.write_text(
        "persona:\n  name: n\n  style: s\n  verbosty: terse\ngoal:\n  intent: i\n",
        encoding="utf-8",
    )
    # extra="forbid" on the models: a misspelled key that is silently dropped
    # produces a scenario that does not do what its file says it does.
    with pytest.raises(ValueError):
        CallerProfile.from_yaml(path)


def test_non_mapping_yaml_is_a_clear_error(tmp_path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping"):
        load_yaml_mapping(path)


def test_empty_yaml_is_a_clear_error(tmp_path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file is empty"):
        load_yaml_mapping(path)
