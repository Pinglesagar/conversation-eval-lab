"""Tests for stage one of test selection: the change analyser.

WHAT THIS DEMONSTRATES
----------------------
This module decides that a scenario *need not run*, which makes it a grader, and
a grader is only worth what its failure modes are worth. Two properties carry
the whole design and both are pinned here rather than asserted in prose:

1.  **It selects nothing for a change that cannot alter behaviour.** Whitespace
    and comments are the cases everyone claims and nobody proves, so they are
    proved: the file text differs, and the analyser still returns zero symbols.
2.  **It selects everything whenever it is unsure.** A rename, a deletion, an
    unparseable file, a non-UTF-8 file, packaging, config, a shared module, a
    corpus file with no id of its own, and a git that will not answer — nine
    ambiguities, nine GLOBAL escalations, each with a recorded reason.

The AST-level tests use no git at all, which keeps them fast and hermetic; the
git-level tests build a throwaway repository per case so the awkward statuses
(R, D, A, plus an untracked file) are exercised against real `git diff` output
rather than a hand-written imitation of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from lab.selection.diff import (
    MODULE_QUALNAME,
    ChangeKind,
    GitUnavailable,
    GlobalReason,
    Layout,
    SymbolKind,
    analyse_changes,
    changed_symbols_between,
    module_name_for,
)

# --------------------------------------------------------------------------
# the AST core, with no git in sight
# --------------------------------------------------------------------------

BASE = textwrap.dedent(
    '''
    """Module docstring."""

    GREETING = "Table for two?"


    class Booker:
        """Takes bookings."""

        def confirm(self, party):
            # confirm the booking
            return f"Booked for {party}"

        def refuse(self):
            return "I cannot do that."


    def helper(x):
        return x + 1
    '''
)


def _qualnames(symbols, kind=None):
    return sorted(s.qualname for s in symbols if kind is None or s.kind is kind)


def test_identical_sources_change_nothing() -> None:
    assert changed_symbols_between(BASE, BASE, "m.py") == []


def test_whitespace_only_change_selects_nothing() -> None:
    """Reformatting must not cost a live run of the suite."""
    reformatted = BASE.replace("    def confirm", "\n\n    def confirm").replace(
        "return x + 1", "return x + 1  "
    )
    assert reformatted != BASE
    assert changed_symbols_between(BASE, reformatted, "m.py") == []


def test_comment_only_change_selects_nothing_and_here_is_the_proof() -> None:
    """The claim is 'comments are invisible', so the test shows the text moved."""
    commented = BASE.replace("# confirm the booking", "# NOTE: reworked in Q3, see ticket")
    assert commented != BASE
    assert "reworked in Q3" in commented
    assert changed_symbols_between(BASE, commented, "m.py") == []


def test_docstring_change_is_a_change_because_a_docstring_can_be_a_prompt() -> None:
    """The documented choice, going the opposite way from comments.

    Agent frameworks build tool schemas out of docstrings, so a changed
    docstring can be a changed prompt. It is treated as a change everywhere, and
    the string-literal diff explains it in the same terms as any other reword.
    """
    changed = BASE.replace('"""Takes bookings."""', '"""Takes bookings and cancellations."""')
    symbols = changed_symbols_between(BASE, changed, "m.py")
    assert "Booker" in _qualnames(symbols, SymbolKind.CLASS)
    literals = [s for s in symbols if s.kind is SymbolKind.STRING_LITERAL]
    assert literals and "reworded" in literals[0].reason


def test_a_changed_body_names_the_function_and_not_its_neighbours() -> None:
    changed = BASE.replace("return x + 1", "return x + 2")
    symbols = changed_symbols_between(BASE, changed, "m.py")
    assert _qualnames(symbols) == ["helper"]
    assert symbols[0].kind is SymbolKind.FUNCTION
    assert symbols[0].change is ChangeKind.MODIFIED
    assert "helper" in symbols[0].reason


def test_a_changed_method_is_attributed_to_the_method_not_the_whole_class() -> None:
    """Otherwise one edit to one method selects everything the class ever touched."""
    changed = BASE.replace('return f"Booked for {party}"', 'return f"Reserved for {party}"')
    symbols = changed_symbols_between(BASE, changed, "m.py")
    assert "Booker.confirm" in _qualnames(symbols)
    assert "Booker" not in _qualnames(symbols, SymbolKind.CLASS)


def test_a_changed_class_shell_is_attributed_to_the_class() -> None:
    changed = BASE.replace("class Booker:", "class Booker(BaseBooker):")
    symbols = changed_symbols_between(BASE, changed, "m.py")
    assert _qualnames(symbols, SymbolKind.CLASS) == ["Booker"]


def test_module_level_code_has_its_own_qualname() -> None:
    changed = BASE.replace("GREETING =", "GREETING_TEXT =")
    symbols = changed_symbols_between(BASE, changed, "m.py")
    module_level = [s for s in symbols if s.kind is SymbolKind.MODULE]
    assert module_level and module_level[0].qualname == MODULE_QUALNAME


def test_added_and_removed_symbols_are_labelled_as_such() -> None:
    added = BASE + "\n\ndef extra():\n    return None\n"
    symbols = changed_symbols_between(BASE, added, "m.py")
    assert [(s.qualname, s.change) for s in symbols if s.kind is SymbolKind.FUNCTION] == [
        ("extra", ChangeKind.ADDED)
    ]

    removed = BASE.replace("    def refuse(self):\n        return \"I cannot do that.\"\n", "")
    symbols = changed_symbols_between(BASE, removed, "m.py")
    kinds = {(s.qualname, s.change) for s in symbols}
    assert ("Booker.refuse", ChangeKind.REMOVED) in kinds


def test_a_reworded_prompt_is_caught_although_no_signature_moved() -> None:
    """The case a file-level or signature-level selector misses entirely."""
    changed = BASE.replace('"I cannot do that."', '"I am not able to help with that."')
    symbols = changed_symbols_between(BASE, changed, "m.py")
    literals = [s for s in symbols if s.kind is SymbolKind.STRING_LITERAL]
    assert len(literals) == 1
    assert literals[0].qualname == "Booker.refuse"
    assert literals[0].change is ChangeKind.MODIFIED
    assert "I cannot do that." in literals[0].reason
    assert "I am not able to help with that." in literals[0].reason


def test_a_reworded_fstring_fragment_is_caught_too() -> None:
    changed = BASE.replace('f"Booked for {party}"', 'f"All set for {party}"')
    literals = [
        s
        for s in changed_symbols_between(BASE, changed, "m.py")
        if s.kind is SymbolKind.STRING_LITERAL
    ]
    assert any("All set for" in s.reason for s in literals)


def test_a_duplicated_literal_counts_as_a_change() -> None:
    """Literals are counted, not setted, so adding a second copy is visible."""
    changed = BASE.replace(
        "def helper(x):\n    return x + 1",
        'def helper(x):\n    _ = "Table for two?"\n    return x + 1',
    )
    literals = [
        s
        for s in changed_symbols_between(BASE, changed, "m.py")
        if s.kind is SymbolKind.STRING_LITERAL
    ]
    assert [s.change for s in literals] == [ChangeKind.ADDED]


def test_a_credential_shaped_literal_is_never_printed_into_a_reason() -> None:
    """Reasons land in CI logs, so the preview redacts before it quotes."""
    before = 'KEY = "placeholder"\n'
    after = 'KEY = "sk-livekeymaterial0123456789abcdefzz"\n'
    symbols = changed_symbols_between(before, after, "m.py")
    literals = [s for s in symbols if s.kind is SymbolKind.STRING_LITERAL]
    assert literals
    for symbol in literals:
        assert "sk-livekeymaterial" not in symbol.reason
        assert "redacted" in symbol.reason


def test_an_added_file_reports_every_symbol_as_added() -> None:
    symbols = changed_symbols_between(None, BASE, "m.py")
    assert {s.change for s in symbols} == {ChangeKind.ADDED}
    assert "Booker.confirm" in _qualnames(symbols)


def test_an_unparseable_side_raises_rather_than_reporting_no_change() -> None:
    """The one thing this must never do is call a broken file unchanged."""
    with pytest.raises(Exception) as caught:
        changed_symbols_between(BASE, "def broken(:\n", "m.py")
    assert "does not parse" in str(caught.value)


def test_symbol_order_is_deterministic() -> None:
    changed = BASE.replace("return x + 1", "return x + 2").replace(
        '"I cannot do that."', '"No."'
    )
    once = changed_symbols_between(BASE, changed, "m.py")
    twice = changed_symbols_between(BASE, changed, "m.py")
    assert once == twice
    assert [s._sort_key() for s in once] == sorted(s._sort_key() for s in once)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("lab/checks/tools.py", "lab.checks.tools"),
        ("lab/__init__.py", "lab"),
        ("scenarios/happy/x.yaml", None),
    ],
)
def test_module_name_for(path: str, expected: str | None) -> None:
    assert module_name_for(path) == expected


# --------------------------------------------------------------------------
# git-level: a throwaway repository per case
# --------------------------------------------------------------------------

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "selection tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "selection tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
}


class Sandbox:
    """A tiny git repository, built one commit at a time."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._git("init", "-q", "-b", "main")

    def _git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
            env=_GIT_ENV,
        )
        return done.stdout

    def write(self, path: str, text: str | bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text, encoding="utf-8")

    def remove(self, path: str) -> None:
        self._git("rm", "-q", path)

    def move(self, old: str, new: str) -> None:
        (self.root / new).parent.mkdir(parents=True, exist_ok=True)
        self._git("mv", old, new)

    def commit(self, message: str = "change") -> None:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def analyse(self, base: str = "HEAD", **kwargs):
        return analyse_changes(base, repo_root=self.root, **kwargs)


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    """A repo with one commit: a package, a scenario, a trace and some docs."""
    box = Sandbox(tmp_path)
    box.write("app/__init__.py", '"""app."""\n')
    box.write("app/booking.py", BASE)
    box.write("app/base.py", "SHARED = 1\n")
    box.write("scenarios/happy/happy-two-covers.yaml", "id: happy-two-covers\ntags: [booking]\n")
    box.write("scenarios/personas/brisk.yaml", "name: brisk\nstyle: terse\n")
    box.write("fixtures/replay_run/traces/happy-two-covers.jsonl", '{"kind": "session_start"}\n')
    box.write("docs/GUIDE.md", "# guide\n")
    box.write("pyproject.toml", "[project]\nname = 'x'\n")
    box.commit("initial")
    return box


def test_a_body_change_selects_symbols_and_stays_narrow(sandbox: Sandbox) -> None:
    sandbox.write("app/booking.py", BASE.replace("return x + 1", "return x + 99"))
    change = sandbox.analyse()
    assert not change.is_global
    assert [s.qualname for s in change.symbols] == ["helper"]
    assert change.symbols[0].module == "app.booking"
    assert change.counts()["files_changed"] == 1


def test_a_comment_change_through_git_selects_nothing_at_all(sandbox: Sandbox) -> None:
    sandbox.write("app/booking.py", BASE.replace("# confirm the booking", "# reworded"))
    change = sandbox.analyse()
    assert change.files  # git saw the file move
    assert change.symbols == ()
    assert change.scenario_ids == ()
    assert not change.is_global


def test_an_edited_scenario_maps_to_its_id(sandbox: Sandbox) -> None:
    sandbox.write(
        "scenarios/happy/happy-two-covers.yaml",
        "id: happy-two-covers\ntags: [booking, closing]\n",
    )
    change = sandbox.analyse()
    assert change.scenario_ids == ("happy-two-covers",)
    assert not change.is_global


def test_an_untracked_new_scenario_is_still_selected(sandbox: Sandbox) -> None:
    """A file the developer has not staged yet is exactly the one being worked on."""
    sandbox.write("scenarios/happy/happy-new-row.yaml", "id: happy-new-row\ntags: [booking]\n")
    change = sandbox.analyse()
    assert "happy-new-row" in change.scenario_ids
    assert change.scenarios[0].change is ChangeKind.ADDED

    ignored = sandbox.analyse(include_untracked=False)
    assert ignored.scenario_ids == ()


def test_a_deleted_scenario_is_reported_as_removed_not_as_global(sandbox: Sandbox) -> None:
    sandbox.remove("scenarios/happy/happy-two-covers.yaml")
    change = sandbox.analyse()
    assert change.scenario_ids == ("happy-two-covers",)
    assert change.scenarios[0].change is ChangeKind.REMOVED
    assert not change.is_global


def test_a_changed_trace_selects_the_scenario_it_records(sandbox: Sandbox) -> None:
    sandbox.write(
        "fixtures/replay_run/traces/happy-two-covers.jsonl",
        '{"kind": "session_start"}\n{"kind": "session_end"}\n',
    )
    change = sandbox.analyse()
    assert change.scenario_ids == ("happy-two-covers",)
    assert "committed trace" in change.scenarios[0].reason


def test_a_repeat_suffixed_trace_selects_both_candidate_ids(sandbox: Sandbox) -> None:
    """`<id>-0.jsonl` is ambiguous, so both readings are selected. Rule A."""
    sandbox.write("fixtures/live_run/traces/happy-two-covers-0.jsonl", "{}\n")
    change = sandbox.analyse()
    assert set(change.scenario_ids) == {"happy-two-covers", "happy-two-covers-0"}


def test_a_deleted_python_module_is_global(sandbox: Sandbox) -> None:
    sandbox.remove("app/booking.py")
    change = sandbox.analyse()
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.DELETED_CODE


def test_a_renamed_python_module_is_global_and_names_both_paths(sandbox: Sandbox) -> None:
    sandbox.move("app/booking.py", "app/reservations.py")
    change = sandbox.analyse()
    assert change.is_global
    trigger = next(g for g in change.globals if g.reason is GlobalReason.RENAMED_PATH)
    assert "app/booking.py" in trigger.detail
    assert trigger.path == "app/reservations.py"


def test_a_moved_scenario_file_is_a_rename_and_keeps_its_id(sandbox: Sandbox) -> None:
    """git reports R100 for a pure move, and the id lives in the file, not the name."""
    sandbox.move(
        "scenarios/happy/happy-two-covers.yaml", "scenarios/booking/happy-two-covers.yaml"
    )
    change = sandbox.analyse()
    assert not change.is_global
    assert change.scenario_ids == ("happy-two-covers",)
    assert change.scenarios[0].change is ChangeKind.RENAMED
    assert "moved from" in change.scenarios[0].reason


def test_a_renamed_and_rewritten_scenario_selects_both_ids(sandbox: Sandbox) -> None:
    """Below git's similarity threshold this arrives as a delete plus an add.

    Which is the harder case, and the one that must not lose the old id: a suite
    or a report keyed on it still exists, and rule A says select both.
    """
    sandbox.move(
        "scenarios/happy/happy-two-covers.yaml", "scenarios/happy/happy-two-covers-v2.yaml"
    )
    sandbox.write(
        "scenarios/happy/happy-two-covers-v2.yaml",
        "id: happy-two-covers-v2\ntags: [booking, closing]\ngoal: something else entirely\n",
    )
    change = sandbox.analyse()
    assert not change.is_global
    assert set(change.scenario_ids) == {"happy-two-covers", "happy-two-covers-v2"}


def test_packaging_is_global(sandbox: Sandbox) -> None:
    sandbox.write("pyproject.toml", "[project]\nname = 'x'\nversion = '2'\n")
    change = sandbox.analyse()
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.PACKAGING


def test_a_shared_module_is_global_but_still_reports_its_symbols(sandbox: Sandbox) -> None:
    """GLOBAL is not an excuse to stop explaining what changed."""
    sandbox.write("app/base.py", "SHARED = 2\n")
    change = sandbox.analyse()
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.SHARED_MODULE
    assert [s.qualname for s in change.symbols] == [MODULE_QUALNAME]


def test_a_corpus_file_with_no_id_of_its_own_is_global(sandbox: Sandbox) -> None:
    """A persona is read by many scenarios, so it cannot be attributed to one."""
    sandbox.write("scenarios/personas/brisk.yaml", "name: brisk\nstyle: clipped\n")
    change = sandbox.analyse()
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.SHARED_SCENARIO_DATA


def test_an_unparseable_python_file_is_global_and_the_reason_is_recorded(
    sandbox: Sandbox,
) -> None:
    sandbox.write("app/booking.py", "def broken(:\n    pass\n")
    change = sandbox.analyse()
    assert change.is_global
    trigger = change.globals[0]
    assert trigger.reason is GlobalReason.UNPARSEABLE
    assert "does not parse" in trigger.detail
    assert "line 1" in trigger.detail


def test_a_non_utf8_python_file_is_global_rather_than_a_crash(sandbox: Sandbox) -> None:
    sandbox.write("app/booking.py", b"\xff\xfe\x00x = 1\n")
    change = sandbox.analyse()
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.UNREADABLE


def test_documentation_outside_a_package_is_inert(sandbox: Sandbox) -> None:
    sandbox.write("docs/GUIDE.md", "# guide\n\nmore words\n")
    change = sandbox.analyse()
    assert not change.is_global
    assert change.inert == ("docs/GUIDE.md",)
    assert change.symbols == ()


def test_markdown_inside_a_python_package_is_not_inert(sandbox: Sandbox) -> None:
    """Packaged markdown is how a prompt ships; it is behaviour, not documentation."""
    sandbox.write("app/prompt_v1.md", "You are a booking agent.\n")
    change = sandbox.analyse()
    assert change.inert == ()
    assert change.is_global
    assert change.globals[0].path == "app/prompt_v1.md"
    assert change.globals[0].reason is GlobalReason.SHARED_MODULE
    assert "read at run time" in change.globals[0].detail


def test_an_unrecognised_path_defaults_to_global(sandbox: Sandbox) -> None:
    sandbox.write("deploy/agent.json", '{"model": "x"}\n')
    change = sandbox.analyse()
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.UNCLASSIFIED


def test_an_added_file_with_no_trace_is_reported_for_stage_two_to_widen(
    sandbox: Sandbox,
) -> None:
    """Stage 1's job is to say what appeared; only stage 2 knows it has no trace."""
    sandbox.write("app/upsell.py", 'def pitch():\n    return "Dessert?"\n')
    change = sandbox.analyse()
    assert {s.qualname for s in change.symbols} == {MODULE_QUALNAME, "pitch", "pitch"}
    assert {s.change for s in change.symbols} == {ChangeKind.ADDED}
    assert change.symbols[0].module == "app.upsell"


def test_a_bad_ref_fails_safe_by_default_and_raises_only_when_asked(
    sandbox: Sandbox,
) -> None:
    change = sandbox.analyse("no-such-ref-exists")
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.GIT_UNAVAILABLE
    with pytest.raises(GitUnavailable):
        sandbox.analyse("no-such-ref-exists", strict=True)


def test_a_directory_that_is_not_a_repository_fails_safe(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    change = analyse_changes("HEAD~1", repo_root=outside)
    assert change.is_global
    assert change.globals[0].reason is GlobalReason.GIT_UNAVAILABLE


def test_the_layout_is_overridable_so_the_policy_is_visible(sandbox: Sandbox) -> None:
    """A team that will not treat `base.py` as shared can say so in one place."""
    sandbox.write("app/base.py", "SHARED = 3\n")
    narrowed = Layout(shared_basenames=frozenset({"__init__.py"}))
    change = sandbox.analyse(layout=narrowed)
    assert not change.is_global
    assert [s.qualname for s in change.symbols] == [MODULE_QUALNAME]


def test_two_analyses_of_the_same_state_are_byte_identical(sandbox: Sandbox) -> None:
    sandbox.write("app/booking.py", BASE.replace("return x + 1", "return x + 3"))
    sandbox.write("scenarios/happy/happy-two-covers.yaml", "id: happy-two-covers\ntags: []\n")
    assert sandbox.analyse().to_dict() == sandbox.analyse().to_dict()


def test_explain_carries_denominators_and_never_understates_the_total(
    sandbox: Sandbox,
) -> None:
    """A naked percentage is a defect; a truncated listing must not read as small."""
    body = BASE
    for name in range(30):
        body += f"\n\ndef generated_{name}():\n    return {name}\n"
    sandbox.write("app/booking.py", body)
    change = sandbox.analyse()
    text = change.explain(limit=5)
    assert f"{len(change.symbols)} changed symbol(s)" in text
    assert "more symbol(s)" in text
    assert "1/1 file(s)" in text
    assert len(change.symbols) == 30


def test_this_repository_analyses_without_credentials_or_network() -> None:
    """A smoke test against the real history: the default path must just work.

    Deliberately unassertive about *what* changed — that would break on the next
    commit. What is pinned is that the analyser runs here, offline, and returns
    a well-formed result whose counts agree with its own lists.
    """
    change = analyse_changes("HEAD~1", head_ref="HEAD", repo_root=Path(__file__).parent.parent)
    counts = change.counts()
    assert counts["files_changed"] == len(change.files)
    assert counts["symbols_changed"] == len(change.symbols)
    assert counts["global_triggers"] == len(change.globals)
    assert isinstance(change.explain(), str)


def test_symbols_expose_the_join_key_stage_two_publishes(sandbox: Sandbox) -> None:
    """`path::qualname` on both sides, so the join is an intersection.

    The trace-derived map records a definition site as `path::qualname`. If the
    two stages disagreed on that string the wiring would need a translation
    layer, and a translation layer between a grader's two halves is where
    silent mis-selection lives.
    """
    sandbox.write("app/booking.py", BASE.replace("return x + 1", "return x + 4"))
    change = sandbox.analyse()
    assert change.locations == ("app/booking.py::helper",)
    assert change.changed_paths == ("app/booking.py",)
    assert change.symbols[0].location == "app/booking.py::helper"


def test_a_module_level_change_carries_a_key_no_definition_site_can_match(
    sandbox: Sandbox,
) -> None:
    """Deliberate: `<module>` must be widened to the whole file, not dropped."""
    sandbox.write("app/booking.py", BASE.replace("GREETING =", "GREETING_LINE ="))
    change = sandbox.analyse()
    assert f"app/booking.py::{MODULE_QUALNAME}" in change.locations


def test_the_command_line_prints_a_report_and_does_not_gate(
    sandbox: Sandbox, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 1 reports; it never fails a build on its own finding."""
    from lab.selection.diff import main

    sandbox.write("app/booking.py", BASE.replace("return x + 1", "return x + 5"))
    monkeypatch.chdir(sandbox.root)
    assert main(["--base", "HEAD"]) == 0
    text = capsys.readouterr().out
    assert "changed symbol(s)" in text
    assert "helper" in text

    assert main(["--base", "HEAD", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["is_global"] is False
    assert payload["counts"]["symbols_changed"] == len(payload["symbols"])
