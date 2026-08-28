"""Stage one of test selection: what actually changed, as *symbols* not files.

WHAT THIS DEMONSTRATES
----------------------
Selecting which scenarios to run from a file-level diff is too coarse to be
useful and too optimistic to be safe. Too coarse, because a 3,000-line module
touched in one helper selects every scenario that ever entered that module. Too
optimistic, because the interesting change in an agent codebase is usually not a
signature at all — it is a **string**. A reworded prompt, a changed refusal
line, a new tool description: none of them move a function signature, and a
selector that diffs declarations sails straight past the single most common
behavioural change in this kind of system. So this module diffs the *contents*
of functions and classes, and diffs the string literals inside them separately,
because those are the change a naive selector misses.

Everything here is deterministic and offline: `git` plus the standard library's
`ast`. No model, no network, no credential, at import or in the default path.
Stage three — the things static analysis provably cannot see — is a documented
seam, not an LLM. See "WHAT THIS CANNOT SEE" below.

THE COMPARISON IS AN AST DIGEST, WHICH DECIDES THREE AWKWARD CASES FOR US
------------------------------------------------------------------------
Each function, class and module *shell* is reduced to `ast.dump(..., include_
attributes=False)` and the digests are compared. Nested definitions are stripped
from their parent's digest, so a changed method is reported as the method, not
as the whole class. Dropping attributes drops line and column numbers, which
settles the cases every reviewer asks about:

*   **Whitespace-only change** — invisible to the AST, so nothing is selected.
    Reformatting a file does not cost 90 minutes of live runs.
*   **Comment-only change** — comments are not in the AST at all, so nothing is
    selected. `tests/test_selection_diff.py` proves it rather than asserting it.
*   **Docstring change** — a docstring *is* an AST node (a string constant), so
    it counts as a change and the scenarios that reach it are selected.

That last one is a deliberate choice and it goes the other way from comments, so
here is the argument. In this class of system a docstring is not always a
comment: agent frameworks derive tool schemas — the text a model reads when
deciding which tool to call — from the docstring of the tool function. A tool
whose docstring changed is a tool whose prompt changed. Even where that is not
true, docstrings are edited far less often than comments, so the cost of the
conservative choice is small and it lands on the safe side of rule A. The rule
is applied consistently: docstrings count everywhere, comments count nowhere.

FAIL SAFE, ALWAYS (RULE A)
--------------------------
When this module is unsure, it says GLOBAL, which downstream means *run
everything*. Skipping a scenario that should have run is the only unrecoverable
error a selector can make; running a few extra is a bill. Every escalation is
recorded with a reason and a category, never silent:

    unparseable Python            GLOBAL — the syntax error is quoted
    unreadable / non-UTF-8 bytes  GLOBAL — a binary blob is not analysable
    a deleted Python module       GLOBAL — every importer is affected
    a renamed or copied file      GLOBAL — its import path or map key moved
    packaging and config          GLOBAL — pyproject, Makefile, CI, .env
    shared and base modules       GLOBAL — `__init__.py`, `conftest.py`, `base.py`
    a scenario data file with no  GLOBAL — personas and other shared inputs are
      `id:` of its own                     read by many scenarios
    an unclassifiable path        GLOBAL — the default, not an oversight
    git missing, or a bad ref     GLOBAL — with `strict=True` to raise instead

There is exactly one place where this module trusts a rule instead of evidence,
and it is called out here because an unaudited exception is how fail-safe
designs rot: files whose type nothing loads at run time — `.md`, `.rst`, `.txt`,
`LICENSE` — are classified INERT and select nothing. That exception is itself
derived rather than declared: a markdown file **inside a Python package** (any
ancestor directory holding an `__init__.py`) is *not* inert, because in this
repository packaged markdown is exactly how a judge's prompt is shipped and read
at run time. Editing `docs/` is free. Editing a prompt that lives beside its
code is not.

WHAT THIS CANNOT SEE
--------------------
Stated plainly, because a selector that oversells itself is worse than no
selector. Stage 1 reads text; it cannot read intent or runtime state:

*   a value read from the environment or a config store at run time, where the
    code did not change and the behaviour did
*   a prompt fragment shared by composition, where the fragment moved but the
    literal did not
*   a dependency that exists only in data — a scenario whose behaviour depends
    on a fixture that neither it nor the changed module names
*   an upgraded third-party package, which changes behaviour with an empty diff

The first three all resolve to GLOBAL here whenever the file carrying them is
touched. The fourth is invisible to any diff of this repository, which is why
lockfile and packaging changes are GLOBAL by name. Anything a future stage three
adds must be able to *add* selections; it must never be allowed to remove one.

USAGE
-----
    from lab.selection.diff import analyse_changes

    change = analyse_changes("HEAD~1")          # base ref -> working tree
    change = analyse_changes("main", head_ref="HEAD")
    print(change.explain())

The result is data, not a decision: `change.symbols`, `change.scenario_ids`,
`change.globals`, each carrying a reason, so "why did you run these 40
scenarios?" has an answer that can be printed.

Each changed symbol also exposes `.location`, which is `path::qualname` — the
same key shape stage 2's trace-derived map uses for a definition site, so the
join between the two stages is a set intersection and not a translation.
"""

from __future__ import annotations

import ast
import copy
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

__all__ = [
    "ChangeKind",
    "ChangeSet",
    "ChangedScenario",
    "ChangedSymbol",
    "FileChange",
    "GitUnavailable",
    "GlobalReason",
    "GlobalTrigger",
    "Layout",
    "SymbolKind",
    "analyse_changes",
    "changed_symbols_between",
    "module_name_for",
]

MODULE_QUALNAME = "<module>"
"""Qualname used for module-level code: imports, constants, top-level calls."""

_PREVIEW_CHARS = 60


class ChangeKind(str, Enum):
    """How a file, symbol or scenario changed."""

    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"


class SymbolKind(str, Enum):
    """What kind of thing changed inside a file."""

    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    STRING_LITERAL = "string_literal"


class GlobalReason(str, Enum):
    """Why a change escalated to "run everything".

    Categories rather than free text, so a report can count them and a reviewer
    can see at a glance whether the selector is escalating for good reasons or
    because one badly-classified file is making it useless.
    """

    PACKAGING = "packaging"
    CONFIG = "config"
    SHARED_MODULE = "shared-module"
    SHARED_SCENARIO_DATA = "shared-scenario-data"
    UNPARSEABLE = "unparseable"
    UNREADABLE = "unreadable"
    DELETED_CODE = "deleted-code"
    RENAMED_PATH = "renamed-path"
    UNCLASSIFIED = "unclassified"
    GIT_UNAVAILABLE = "git-unavailable"


class GitUnavailable(RuntimeError):
    """Raised only when `analyse_changes(..., strict=True)` cannot reach git.

    The default is not to raise: an unusable git is an ambiguity like any other
    and resolves to GLOBAL, loudly, so a selector wired into CI degrades to
    running the whole suite instead of crashing the pipeline.
    """


# --------------------------------------------------------------------------
# layout — where this repository keeps the things stage 1 must recognise
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Which paths mean what. Overridable so the rules are auditable, not buried.

    Defaults describe this repository. A team that disagrees with, say, treating
    every `__init__.py` as shared can narrow `shared_basenames` in one visible
    place rather than discovering the policy by reading the code.
    """

    scenario_roots: tuple[str, ...] = ("scenarios",)
    fixture_roots: tuple[str, ...] = ("fixtures",)
    #: A directory named this, under a fixture root, holds per-scenario traces.
    trace_dirname: str = "traces"
    #: Touch one of these and the blast radius is the whole suite.
    packaging_names: frozenset[str] = frozenset(
        {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "MANIFEST.in",
            "Makefile",
            "tox.ini",
            "requirements.txt",
            "requirements-dev.txt",
            "uv.lock",
            "poetry.lock",
        }
    )
    #: Directory prefixes whose contents configure the build or the run.
    config_prefixes: tuple[str, ...] = (".github/", ".circleci/")
    #: Any file whose name starts with one of these is configuration.
    config_name_prefixes: tuple[str, ...] = (".env", "conftest")
    #: Python files that many other modules stand on.
    shared_basenames: frozenset[str] = frozenset(
        {
            "__init__.py",
            "conftest.py",
            "base.py",
            "common.py",
            "shared.py",
            "constants.py",
            "config.py",
            "settings.py",
        }
    )
    #: Suffixes nothing loads at run time — the one trusted rule (see module docs).
    inert_suffixes: frozenset[str] = frozenset({".md", ".rst", ".txt"})
    inert_names: frozenset[str] = frozenset({"LICENSE", "LICENSE.md", "NOTICE"})


DEFAULT_LAYOUT = Layout()


# --------------------------------------------------------------------------
# result types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileChange:
    """One line of `git diff --name-status`, normalised."""

    path: str
    change: ChangeKind
    old_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "change": self.change.value, "old_path": self.old_path}


@dataclass(frozen=True)
class ChangedSymbol:
    """A symbol whose behaviour may have changed, and why we say so.

    `qualname` is dotted within the file (`Class.method`, `outer.inner`), or
    `<module>` for module-level code. `module` is the dotted import path of the
    file when it is Python, which is the key stage 2's trace-derived map is
    expected to join on.
    """

    path: str
    qualname: str
    kind: SymbolKind
    change: ChangeKind
    reason: str
    module: str | None = None

    @property
    def location(self) -> str:
        """`path::qualname` — the key shape the trace-derived map publishes.

        Stage 2 records where each runtime agent or tool name is defined as
        `path::qualname`, so the join between "what changed" and "what a
        scenario exercised" is a set intersection over this string and needs no
        translation layer between the two stages.

        Module-level changes carry the `<module>` qualname, which matches no
        definition site by design: a joiner should widen those to every symbol
        in the same `path`, because module-level code runs on import for
        everything in the file.
        """
        return f"{self.path}::{self.qualname}"

    def __str__(self) -> str:
        return f"{self.location} [{self.kind.value}/{self.change.value}] {self.reason}"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "qualname": self.qualname,
            "kind": self.kind.value,
            "change": self.change.value,
            "reason": self.reason,
            "module": self.module,
        }

    def _sort_key(self) -> tuple[str, str, str, str]:
        return (self.path, self.kind.value, self.qualname, self.change.value)


@dataclass(frozen=True)
class ChangedScenario:
    """A scenario touched directly — its own YAML, or its committed trace."""

    scenario_id: str
    change: ChangeKind
    path: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "change": self.change.value,
            "path": self.path,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GlobalTrigger:
    """One reason the selector refuses to narrow: run everything."""

    path: str
    reason: GlobalReason
    detail: str

    def __str__(self) -> str:
        return f"{self.path} [{self.reason.value}] {self.detail}"

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True)
class ChangeSet:
    """Everything stage 1 knows, with a reason attached to every claim."""

    base_ref: str
    head_ref: str
    repo_root: str
    files: tuple[FileChange, ...] = ()
    symbols: tuple[ChangedSymbol, ...] = ()
    scenarios: tuple[ChangedScenario, ...] = ()
    globals: tuple[GlobalTrigger, ...] = ()
    inert: tuple[str, ...] = ()

    @property
    def is_global(self) -> bool:
        """True when downstream must run the whole suite. Rule A lives here."""
        return bool(self.globals)

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(sorted({s.scenario_id for s in self.scenarios}))

    @property
    def locations(self) -> tuple[str, ...]:
        """Every changed symbol as `path::qualname`, ready to intersect."""
        return tuple(sorted({s.location for s in self.symbols}))

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Repo-relative paths that carry at least one changed symbol."""
        return tuple(sorted({s.path for s in self.symbols}))

    @property
    def modules(self) -> tuple[str, ...]:
        """Dotted import paths of the Python modules with changed symbols."""
        return tuple(sorted({s.module for s in self.symbols if s.module}))

    def symbols_for(self, path: str) -> tuple[ChangedSymbol, ...]:
        return tuple(s for s in self.symbols if s.path == path)

    def counts(self) -> dict[str, int]:
        """Every number this object will ever report, with its denominator."""
        by_kind = Counter(s.kind.value for s in self.symbols)
        by_global = Counter(g.reason.value for g in self.globals)
        return {
            "files_changed": len(self.files),
            "files_inert": len(self.inert),
            "symbols_changed": len(self.symbols),
            "scenarios_touched": len(self.scenario_ids),
            "global_triggers": len(self.globals),
            **{f"symbols_{k}": v for k, v in sorted(by_kind.items())},
            **{f"global_{k}": v for k, v in sorted(by_global.items())},
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "repo_root": self.repo_root,
            "is_global": self.is_global,
            "counts": self.counts(),
            "files": [f.to_dict() for f in self.files],
            "symbols": [s.to_dict() for s in self.symbols],
            "scenarios": [s.to_dict() for s in self.scenarios],
            "globals": [g.to_dict() for g in self.globals],
            "inert": list(self.inert),
        }

    def explain(self, *, limit: int | None = 20) -> str:
        """A human-readable account, with denominators on every count.

        `limit` caps how many symbols are listed; the count above the list is
        always the true total, so a truncated listing can never be mistaken for
        a smaller change than happened.
        """
        n_files = len(self.files)
        lines = [
            f"changes {self.base_ref}..{self.head_ref} in {self.repo_root}",
            f"  {n_files} changed file(s); {len(self.inert)}/{n_files} inert (no run-time effect)",
            f"  {len(self.symbols)} changed symbol(s) across "
            f"{len({s.path for s in self.symbols})}/{n_files} file(s)",
            f"  {len(self.scenario_ids)} scenario(s) touched directly",
        ]
        if self.is_global:
            lines.append(
                f"  GLOBAL: {len(self.globals)} trigger(s) — the whole suite must run"
            )
            for trigger in self.globals[:limit]:
                lines.append(f"    - {trigger}")
            if limit is not None and len(self.globals) > limit:
                lines.append(f"    ... {len(self.globals) - limit} more trigger(s)")
        else:
            lines.append("  GLOBAL: none — narrowing is permitted")
        shown = self.symbols if limit is None else self.symbols[:limit]
        for symbol in shown:
            lines.append(f"    - {symbol}")
        if limit is not None and len(self.symbols) > limit:
            lines.append(f"    ... {len(self.symbols) - limit} more symbol(s)")
        for scenario in self.scenarios:
            lines.append(
                f"    - scenario {scenario.scenario_id} "
                f"[{scenario.change.value}] {scenario.reason}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------
# the AST core — usable with no git at all, which is what makes it testable
# --------------------------------------------------------------------------


class _ParseFailure(Exception):
    """Internal: a side of the diff could not be turned into an AST."""


_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _looks_like_a_credential(text: str) -> bool:
    """Cheap, deliberately over-eager check before any literal is printed.

    A changed string is quoted in a reason, and reasons are printed into CI
    logs. The cost of redacting a harmless literal is an unreadable line; the
    cost of not redacting a live key is an incident.
    """
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return True
    stripped = text.strip()
    if len(stripped) >= 32 and " " not in stripped:
        has_digit = any(c.isdigit() for c in stripped)
        has_alpha = any(c.isalpha() for c in stripped)
        if has_digit and has_alpha:
            return True
    return False


def _preview(text: str) -> str:
    """One-line, length-capped, credential-redacted rendering of a literal."""
    if _looks_like_a_credential(text):
        return "<redacted: looks like a credential>"
    flat = " ".join(text.split())
    if len(flat) > _PREVIEW_CHARS:
        flat = flat[:_PREVIEW_CHARS] + "…"
    return f'"{flat}"'


def _shell(node: ast.AST) -> ast.AST:
    """A copy of `node` with nested definitions removed from its body.

    So that a changed method is attributed to the method, and its class is only
    reported when the class's *own* material — bases, decorators, class-level
    assignments — moved.
    """
    body = getattr(node, "body", None)
    if body is None:
        return node
    trimmed = copy.copy(node)
    trimmed.body = [stmt for stmt in body if not isinstance(stmt, _DEF_NODES)]
    return trimmed


def _digest(node: ast.AST) -> str:
    # include_attributes=False is the whole trick: no line numbers, no columns,
    # therefore no whitespace sensitivity. Comments never reach the AST at all.
    return ast.dump(_shell(node), annotate_fields=True, include_attributes=False)


def _walk_symbols(tree: ast.Module) -> dict[str, tuple[SymbolKind, str]]:
    """qualname -> (kind, digest) for the module and every def and class in it."""
    out: dict[str, tuple[SymbolKind, str]] = {
        MODULE_QUALNAME: (SymbolKind.MODULE, _digest(tree))
    }

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if not isinstance(stmt, _DEF_NODES):
                continue
            qualname = f"{prefix}{stmt.name}"
            kind = SymbolKind.CLASS if isinstance(stmt, ast.ClassDef) else SymbolKind.FUNCTION
            out[qualname] = (kind, _digest(stmt))
            visit(stmt.body, f"{qualname}.")

    visit(tree.body, "")
    return out


def _walk_literals(tree: ast.Module) -> Counter[tuple[str, str]]:
    """(owner qualname, text) -> count, for every string constant in the file.

    Counted rather than setted so that duplicating a line is a change. Owners
    are the enclosing def or class, which is what makes the reason readable:
    "a string in `refusal_line` was reworded" beats "a string somewhere moved".
    Docstrings are string constants and are therefore included — see the module
    docstring for why that is the consistent choice.
    """
    counts: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_NODES):
                visit(child, f"{owner}.{child.name}" if owner != MODULE_QUALNAME else child.name)
                continue
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                counts[(owner, child.value)] += 1
            visit(child, owner)

    visit(tree, MODULE_QUALNAME)
    return counts


def _parse(source: str, path: str, side: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise _ParseFailure(
            f"{side} side of {path} does not parse: {exc.msg} at line {exc.lineno}"
        ) from exc
    except (ValueError, RecursionError) as exc:  # null bytes, absurd nesting
        raise _ParseFailure(f"{side} side of {path} does not parse: {exc}") from exc


def changed_symbols_between(
    old_source: str | None,
    new_source: str | None,
    path: str,
    *,
    module: str | None = None,
) -> list[ChangedSymbol]:
    """Diff two versions of one Python file into changed symbols, with reasons.

    `old_source=None` means the file was added; `new_source=None` means it was
    deleted (callers escalate deletions to GLOBAL, but the function is defined
    for completeness and tested). Raises `_ParseFailure` — caught by the caller
    and turned into a GLOBAL trigger — when either side is not parseable, so an
    unparseable file can never be mistaken for an unchanged one.

    Ordering of the result is deterministic: by path, kind, qualname, change.
    """
    old_tree = _parse(old_source, path, "old") if old_source is not None else None
    new_tree = _parse(new_source, path, "new") if new_source is not None else None

    old_symbols = _walk_symbols(old_tree) if old_tree is not None else {}
    new_symbols = _walk_symbols(new_tree) if new_tree is not None else {}
    old_literals = _walk_literals(old_tree) if old_tree is not None else Counter()
    new_literals = _walk_literals(new_tree) if new_tree is not None else Counter()

    found: list[ChangedSymbol] = []

    def emit(qualname: str, kind: SymbolKind, change: ChangeKind, reason: str) -> None:
        found.append(
            ChangedSymbol(
                path=path,
                qualname=qualname,
                kind=kind,
                change=change,
                reason=reason,
                module=module,
            )
        )

    for qualname in sorted(set(old_symbols) | set(new_symbols)):
        before = old_symbols.get(qualname)
        after = new_symbols.get(qualname)
        if before is None and after is not None:
            kind, _ = after
            emit(qualname, kind, ChangeKind.ADDED, f"{kind.value} {qualname} was added")
        elif after is None and before is not None:
            kind, _ = before
            emit(qualname, kind, ChangeKind.REMOVED, f"{kind.value} {qualname} was removed")
        elif before is not None and after is not None and before[1] != after[1]:
            kind = after[0]
            if before[0] is not after[0]:
                emit(
                    qualname,
                    kind,
                    ChangeKind.MODIFIED,
                    f"{qualname} changed from {before[0].value} to {kind.value}",
                )
            elif qualname == MODULE_QUALNAME:
                emit(
                    qualname,
                    kind,
                    ChangeKind.MODIFIED,
                    "module-level code changed (imports, constants or top-level calls)",
                )
            else:
                emit(
                    qualname,
                    kind,
                    ChangeKind.MODIFIED,
                    f"{kind.value} {qualname} changed body, signature or decorators "
                    "(comments and formatting excluded)",
                )

    found.extend(
        _literal_changes(old_literals, new_literals, path=path, module=module)
    )
    found.sort(key=ChangedSymbol._sort_key)
    return found


def _literal_changes(
    old_literals: Counter[tuple[str, str]],
    new_literals: Counter[tuple[str, str]],
    *,
    path: str,
    module: str | None,
) -> list[ChangedSymbol]:
    """String-literal diff, paired per owner so a reword reads as a reword.

    The case this exists for: a prompt is edited. No signature moved, no call
    graph changed, and a selector that only diffs declarations would report
    nothing at all while the agent's behaviour changed underneath it.
    """
    removed = old_literals - new_literals
    added = new_literals - old_literals
    if not removed and not added:
        return []

    by_owner_removed: dict[str, list[str]] = {}
    by_owner_added: dict[str, list[str]] = {}
    for (owner, text), count in sorted(removed.items()):
        by_owner_removed.setdefault(owner, []).extend([text] * count)
    for (owner, text), count in sorted(added.items()):
        by_owner_added.setdefault(owner, []).extend([text] * count)

    out: list[ChangedSymbol] = []
    for owner in sorted(set(by_owner_removed) | set(by_owner_added)):
        gone = by_owner_removed.get(owner, [])
        new = by_owner_added.get(owner, [])
        paired = min(len(gone), len(new))
        for before, after in zip(gone[:paired], new[:paired]):
            out.append(
                ChangedSymbol(
                    path=path,
                    qualname=owner,
                    kind=SymbolKind.STRING_LITERAL,
                    change=ChangeKind.MODIFIED,
                    reason=(
                        f"string literal in {owner} was reworded: "
                        f"{_preview(before)} -> {_preview(after)}"
                    ),
                    module=module,
                )
            )
        for text in gone[paired:]:
            out.append(
                ChangedSymbol(
                    path=path,
                    qualname=owner,
                    kind=SymbolKind.STRING_LITERAL,
                    change=ChangeKind.REMOVED,
                    reason=f"string literal removed from {owner}: {_preview(text)}",
                    module=module,
                )
            )
        for text in new[paired:]:
            out.append(
                ChangedSymbol(
                    path=path,
                    qualname=owner,
                    kind=SymbolKind.STRING_LITERAL,
                    change=ChangeKind.ADDED,
                    reason=f"string literal added in {owner}: {_preview(text)}",
                    module=module,
                )
            )
    return out


# --------------------------------------------------------------------------
# path classification
# --------------------------------------------------------------------------


def module_name_for(path: str) -> str | None:
    """Dotted import path for a Python file, or None if it is not one.

    `lab/checks/tools.py` -> `lab.checks.tools`; `lab/__init__.py` -> `lab`.
    The join key stage 2's trace-derived map is expected to use.
    """
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return None
    parts = list(pure.parts)
    parts[-1] = pure.stem
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _under(path: str, roots: tuple[str, ...]) -> bool:
    pure = PurePosixPath(path)
    return any(pure.parts[:1] == (root,) for root in roots)


def _is_inert(path: str, layout: Layout, package_dirs: frozenset[str]) -> bool:
    """Documentation and other files nothing loads at run time.

    Not inert when the file sits inside a Python package: packaged markdown in
    this repository is how a judge's prompt is shipped, and a prompt is the most
    behaviour-carrying text there is.
    """
    pure = PurePosixPath(path)
    if pure.name not in layout.inert_names and pure.suffix not in layout.inert_suffixes:
        return False
    parent = pure.parent
    while True:
        if str(parent) in package_dirs:
            return False
        if parent == PurePosixPath("."):
            return True
        parent = parent.parent


def _scenario_id_from_yaml(text: str | None) -> str | None:
    """The `id:` of a scenario file, read without importing a YAML parser.

    Stage 1 is stdlib-only on purpose — it must run in a hook or a clean clone
    with nothing installed. A top-level `id:` in this corpus is always at column
    zero, and every one of the 164 scenario files whose id was checked matches
    its own filename stem, so the stem is a safe cross-check but not a
    substitute: a file with no id of its own is shared data, not a scenario, and
    the caller escalates it.
    """
    if text is None:
        return None
    match = re.search(r"^id:[ \t]*[\"']?([A-Za-z0-9][A-Za-z0-9._\-]*)[\"']?[ \t]*$", text, re.M)
    return match.group(1) if match else None


def _trace_scenario_ids(path: str, layout: Layout) -> tuple[str, ...]:
    """Scenario ids a committed trace file could belong to.

    Repeat runs are recorded as `<scenario-id>-0.jsonl`, `-1`, `-2`. A trailing
    numeric suffix is therefore ambiguous — a scenario could genuinely end in a
    digit — so both candidates are returned. Ambiguity resolves toward running
    more, which is rule A applied to a filename.
    """
    pure = PurePosixPath(path)
    if pure.suffix != ".jsonl" or layout.trace_dirname not in pure.parts:
        return ()
    stem = pure.stem
    stripped = re.sub(r"-\d+$", "", stem)
    return (stem,) if stripped == stem else (stem, stripped)


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitUnavailable(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise GitUnavailable(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def _repo_root(start: Path) -> Path:
    out = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise GitUnavailable(f"{start} is not inside a git work tree: {out.stderr.strip()}")
    return Path(out.stdout.strip())


_STATUS = {
    "A": ChangeKind.ADDED,
    "M": ChangeKind.MODIFIED,
    "D": ChangeKind.REMOVED,
    "R": ChangeKind.RENAMED,
    "C": ChangeKind.RENAMED,
}


def _parse_name_status(raw: str) -> list[FileChange]:
    """`git diff --name-status -M` into FileChange, unknown statuses included.

    T (type change) and U (unmerged) have no sensible symbol-level reading, so
    they are surfaced as MODIFIED files with no old path and picked up by the
    unclassified branch downstream, which is GLOBAL.
    """
    changes: list[FileChange] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        code = fields[0].strip()
        kind = _STATUS.get(code[:1])
        if kind is ChangeKind.RENAMED and len(fields) >= 3:
            changes.append(FileChange(path=fields[2], change=kind, old_path=fields[1]))
            continue
        if kind is None:
            # T, U, X, or anything a future git invents.
            changes.append(FileChange(path=fields[-1], change=ChangeKind.MODIFIED))
            continue
        changes.append(FileChange(path=fields[1], change=kind))
    return changes


def _read_side(repo_root: Path, ref: str | None, path: str) -> str | None:
    """Text of `path` at `ref`, or from the working tree when `ref` is None.

    Returns None when the path does not exist on that side. Raises
    `_ParseFailure` for bytes that are not UTF-8 — a binary blob is not
    analysable and the caller escalates rather than guessing.
    """
    if ref is None:
        target = repo_root / path
        if not target.is_file():
            return None
        raw = target.read_bytes()
    else:
        try:
            raw = _git_bytes(repo_root, "show", f"{ref}:{path}")
        except GitUnavailable:
            return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ParseFailure(f"{path} at {ref or 'the working tree'} is not UTF-8 text: {exc}")


def _package_dirs(repo_root: Path, ref: str | None) -> frozenset[str]:
    """Directories that are Python packages, derived from tracked `__init__.py`.

    Used only by the inert rule, and derived rather than declared so nobody has
    to maintain a list of "directories where markdown is really a prompt".
    """
    try:
        if ref is None:
            listing = _git(repo_root, "ls-files", "--", "*__init__.py")
        else:
            listing = _git(repo_root, "ls-tree", "-r", "--name-only", ref)
    except GitUnavailable:
        return frozenset()
    dirs = set()
    for line in listing.splitlines():
        if line.endswith("__init__.py"):
            dirs.add(str(PurePosixPath(line).parent))
    return frozenset(dirs)


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------


@dataclass
class _Accumulator:
    symbols: list[ChangedSymbol] = field(default_factory=list)
    scenarios: list[ChangedScenario] = field(default_factory=list)
    globals: list[GlobalTrigger] = field(default_factory=list)
    inert: list[str] = field(default_factory=list)

    def go_global(self, path: str, reason: GlobalReason, detail: str) -> None:
        self.globals.append(GlobalTrigger(path=path, reason=reason, detail=detail))


def analyse_changes(
    base_ref: str = "HEAD~1",
    *,
    head_ref: str | None = None,
    repo_root: str | Path | None = None,
    layout: Layout = DEFAULT_LAYOUT,
    strict: bool = False,
    include_untracked: bool = True,
) -> ChangeSet:
    """What changed between `base_ref` and `head_ref` (default: the working tree).

    Deterministic, offline, stdlib-only. Never raises for an ordinary problem —
    a missing ref, a broken git, an unparseable file all resolve to GLOBAL with
    a recorded reason — unless `strict=True`, which turns git failures into
    `GitUnavailable` for callers that would rather stop than over-run.

    Untracked files are included as additions by default when comparing against
    the working tree, so a new scenario that has not been staged yet is still
    selected. Set `include_untracked=False` to compare only what git tracks.
    """
    start = Path(repo_root) if repo_root is not None else Path.cwd()
    head_label = head_ref if head_ref is not None else "<working tree>"

    try:
        root = _repo_root(start)
        diff_args = ["diff", "--name-status", "-M", base_ref]
        if head_ref is not None:
            diff_args.append(head_ref)
        raw = _git(root, *diff_args)
    except GitUnavailable as exc:
        if strict:
            raise
        return ChangeSet(
            base_ref=base_ref,
            head_ref=head_label,
            repo_root=str(start),
            globals=(
                GlobalTrigger(
                    path="<repository>",
                    reason=GlobalReason.GIT_UNAVAILABLE,
                    detail=(
                        f"could not read the diff, so nothing may be skipped: {exc}"
                    ),
                ),
            ),
        )

    files = _parse_name_status(raw)
    known = {f.path for f in files}
    if head_ref is None and include_untracked:
        try:
            for line in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
                if line.strip() and line not in known:
                    files.append(FileChange(path=line, change=ChangeKind.ADDED))
        except GitUnavailable:  # pragma: no cover - ls-files after a good diff
            pass
    files.sort(key=lambda f: (f.path, f.change.value))

    package_dirs = _package_dirs(root, head_ref)
    acc = _Accumulator()
    for change in files:
        _classify(change, root, base_ref, head_ref, layout, package_dirs, acc)

    acc.symbols.sort(key=ChangedSymbol._sort_key)
    acc.scenarios.sort(key=lambda s: (s.scenario_id, s.path, s.change.value))
    acc.globals.sort(key=lambda g: (g.path, g.reason.value))
    return ChangeSet(
        base_ref=base_ref,
        head_ref=head_label,
        repo_root=str(root),
        files=tuple(files),
        symbols=tuple(acc.symbols),
        scenarios=tuple(acc.scenarios),
        globals=tuple(acc.globals),
        inert=tuple(sorted(acc.inert)),
    )


def _classify(
    change: FileChange,
    root: Path,
    base_ref: str,
    head_ref: str | None,
    layout: Layout,
    package_dirs: frozenset[str],
    acc: _Accumulator,
) -> None:
    """Route one changed file to symbols, a scenario, INERT, or GLOBAL.

    The order of the branches is the policy, so it is written out plainly:
    renames and deletions first because they are about paths rather than
    contents, then packaging and config, then Python, then corpus data, then
    documentation, and finally the default — which is GLOBAL, not "ignore".
    """
    path = change.path
    pure = PurePosixPath(path)
    is_scenario_file = _under(path, layout.scenario_roots) and pure.suffix in {".yaml", ".yml"}

    # --- renames and copies: the path moved, so every key derived from it moved
    if change.change is ChangeKind.RENAMED:
        old = change.old_path or "?"
        if is_scenario_file:
            new_id = _scenario_id_from_yaml(_read_side(root, head_ref, path)) or pure.stem
            old_id = (
                _scenario_id_from_yaml(_read_side(root, base_ref, old))
                or PurePosixPath(old).stem
            )
            acc.scenarios.append(
                ChangedScenario(
                    scenario_id=new_id,
                    change=ChangeKind.RENAMED,
                    path=path,
                    reason=f"scenario file moved from {old}",
                )
            )
            if old_id != new_id:
                acc.scenarios.append(
                    ChangedScenario(
                        scenario_id=old_id,
                        change=ChangeKind.REMOVED,
                        path=old,
                        reason=f"scenario id no longer exists; the file became {path}",
                    )
                )
            return
        acc.go_global(
            path,
            GlobalReason.RENAMED_PATH,
            f"renamed from {old}; import paths and map keys derived from the old "
            "path are stale, so nothing may be skipped",
        )
        return

    # --- deletions
    if change.change is ChangeKind.REMOVED:
        if is_scenario_file:
            old_text = _read_side(root, base_ref, path)
            scenario_id = _scenario_id_from_yaml(old_text)
            if scenario_id is None:
                acc.go_global(
                    path,
                    GlobalReason.SHARED_SCENARIO_DATA,
                    "deleted corpus file with no id of its own; shared inputs are "
                    "read by many scenarios",
                )
                return
            acc.scenarios.append(
                ChangedScenario(
                    scenario_id=scenario_id,
                    change=ChangeKind.REMOVED,
                    path=path,
                    reason="the scenario file was deleted",
                )
            )
            return
        trace_ids = _trace_scenario_ids(path, layout)
        if trace_ids:
            for scenario_id in trace_ids:
                acc.scenarios.append(
                    ChangedScenario(
                        scenario_id=scenario_id,
                        change=ChangeKind.MODIFIED,
                        path=path,
                        reason="its committed trace was deleted; no derived evidence remains",
                    )
                )
            return
        if _is_inert(path, layout, package_dirs):
            acc.inert.append(path)
            return
        acc.go_global(
            path,
            GlobalReason.DELETED_CODE,
            "file deleted; anything that imported or read it is affected and the "
            "new side cannot be analysed",
        )
        return

    # --- packaging and configuration
    if pure.name in layout.packaging_names or (
        len(pure.parts) == 1 and pure.suffix in {".toml", ".cfg", ".ini"}
    ):
        acc.go_global(path, GlobalReason.PACKAGING, "packaging or build definition changed")
        return
    if path.startswith(layout.config_prefixes) or pure.name.startswith(
        layout.config_name_prefixes
    ):
        acc.go_global(path, GlobalReason.CONFIG, "configuration changed; effects are run-time")
        return

    # --- Python
    if pure.suffix == ".py":
        module = module_name_for(path)
        old_text = _read_side_safe(root, base_ref, path, acc)
        if old_text is _UNREADABLE:
            return
        new_text = _read_side_safe(root, head_ref, path, acc)
        if new_text is _UNREADABLE:
            return
        shared = pure.name in layout.shared_basenames
        try:
            symbols = changed_symbols_between(old_text, new_text, path, module=module)
        except _ParseFailure as exc:
            acc.go_global(path, GlobalReason.UNPARSEABLE, str(exc))
            return
        acc.symbols.extend(symbols)
        if shared and symbols:
            acc.go_global(
                path,
                GlobalReason.SHARED_MODULE,
                f"{pure.name} is a shared or base module; "
                f"{len(symbols)} changed symbol(s) here can reach anything",
            )
        return

    # --- the corpus
    if is_scenario_file:
        text = _read_side_safe(root, head_ref, path, acc)
        if text is _UNREADABLE:
            return
        scenario_id = _scenario_id_from_yaml(text)
        if scenario_id is None:
            acc.go_global(
                path,
                GlobalReason.SHARED_SCENARIO_DATA,
                "corpus file with no id of its own (a persona or other shared "
                "input); many scenarios read it",
            )
            return
        acc.scenarios.append(
            ChangedScenario(
                scenario_id=scenario_id,
                change=change.change,
                path=path,
                reason="the scenario's own definition changed",
            )
        )
        return

    if _under(path, layout.fixture_roots):
        trace_ids = _trace_scenario_ids(path, layout)
        if trace_ids:
            for scenario_id in trace_ids:
                acc.scenarios.append(
                    ChangedScenario(
                        scenario_id=scenario_id,
                        change=change.change,
                        path=path,
                        reason="its committed trace changed, so its recorded behaviour did",
                    )
                )
            return
        if _is_inert(path, layout, package_dirs):
            acc.inert.append(path)
            return
        acc.go_global(
            path,
            GlobalReason.UNCLASSIFIED,
            "fixture that cannot be attributed to a single scenario",
        )
        return

    # --- documentation: the one place a rule is trusted instead of evidence
    if _is_inert(path, layout, package_dirs):
        acc.inert.append(path)
        return
    if pure.name in layout.inert_names or pure.suffix in layout.inert_suffixes:
        # Documentation-typed, but shipped inside a Python package. In this
        # repository that is how a judge's prompt, its rubric and its labelled
        # set travel, and all three are read at run time. Packaged prose is code.
        acc.go_global(
            path,
            GlobalReason.SHARED_MODULE,
            "documentation-typed file inside a Python package; packaged text of "
            "this kind (prompts, rubrics, labelled sets) is read at run time",
        )
        return

    # --- the default. Not "ignore".
    acc.go_global(
        path,
        GlobalReason.UNCLASSIFIED,
        "no rule claims this path, so its blast radius is unknown",
    )


class _Unreadable:
    __slots__ = ()


_UNREADABLE = _Unreadable()


def _read_side_safe(
    root: Path, ref: str | None, path: str, acc: _Accumulator
) -> str | None | _Unreadable:
    """`_read_side`, with a non-UTF-8 file escalated to GLOBAL instead of raising."""
    try:
        return _read_side(root, ref, path)
    except _ParseFailure as exc:
        acc.go_global(path, GlobalReason.UNREADABLE, str(exc))
        return _UNREADABLE


# --------------------------------------------------------------------------
# a command line, so stage 1 is inspectable before anything is wired to it
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """`python -m lab.selection.diff [--base REF] [--head REF] [--json]`.

    Deliberately its own entry point rather than an `evallab` subcommand: the
    selection layer is wired into the CLI separately, once all of its stages
    have landed. Exit status is 0 whether or not the change is GLOBAL — this
    prints a finding, it does not gate a build.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m lab.selection.diff",
        description="What changed, as symbols rather than files.",
    )
    parser.add_argument("--base", default="HEAD~1", help="base ref (default: HEAD~1)")
    parser.add_argument(
        "--head", default=None, help="head ref (default: the working tree, untracked included)"
    )
    parser.add_argument("--repo", default=None, help="repository root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    parser.add_argument(
        "--limit", type=int, default=20, help="symbols to list in the text report (default: 20)"
    )
    args = parser.parse_args(argv)

    change = analyse_changes(args.base, head_ref=args.head, repo_root=args.repo)
    if args.json:
        print(json.dumps(change.to_dict(), indent=2, sort_keys=True))
    else:
        print(change.explain(limit=args.limit))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
