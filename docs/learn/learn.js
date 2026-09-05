/* conversation-eval-lab — the guided tour.
 *
 * Four jobs: switch lessons, define every term on click, remember where you got
 * to, and build the glossary from the same definitions the popovers use — so a
 * word can never mean one thing in a lesson and another in the index.
 *
 * No dependencies, no network, works from file://.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ terms
   * One definition per term: `plain` is the everyday sentence, `real` is what it
   * is in this repository, `where` is the file to open. The glossary lesson is
   * generated from this object, so adding a term here adds it everywhere.
   */
  var TERMS = {
    harness: { t: "Harness", plain: "The test rig. Not the thing being tested — the thing doing the testing.", real: "Everything under lab/: the clock, the trace, the checks, the judges, the reporter. It knows nothing about advisers or restaurants.", where: "lab/" },
    sut: { t: "System under test (SUT)", plain: "The thing being examined. In a driving test, the learner driver.", real: "Two of them ship with the repo so the harness can be shown catching real bugs: roleplay/ (an advisory coach) and tablemate/ (a restaurant booking bot). Both carry documented, deliberate defects.", where: "roleplay/, tablemate/" },
    trace: { t: "Trace", plain: "The recording of everything that happened during one conversation, in order. Like a dashcam plus a receipt.", real: "An ordered list of events with an injected clock, saved as one JSONL file. Every score, every timing figure and every check reads this and nothing else.", where: "lab/trace/schema.py" },
    event: { t: "Event", plain: "One thing that happened. Somebody spoke; a tool was called; audio arrived.", real: "A record with four fields: ts (when), kind (what sort), actor (who), payload (the details).", where: "lab/trace/schema.py" },
    eventkind: { t: "Event kind", plain: "The category of a thing that happened — the seventeen sorts of line the recorder can write.", real: "String constants on EventKind: session_start, caller_utterance, tool_call, transcript_in and so on. Deliberately strings, not a closed enum, so a new adapter can emit a kind this version has never seen without the file failing to load.", where: "lab/trace/schema.py" },
    jsonl: { t: "JSONL", plain: "A text file where each line is one self-contained record. You can read it with your eyes, or with any tool.", real: "The trace format: one JSON object per line, appended in order. A trace can be grepped, diffed in a pull request, and replayed years later.", where: "lab/trace/io.py" },
    actor: { t: "Actor", plain: "Who did the thing: the caller, the agent, the system.", real: "A field on every event. It is how a check can say 'the agent promised' rather than 'somebody promised'.", where: "lab/trace/schema.py" },
    payload: { t: "Payload", plain: "The details attached to an event — the actual words, the tool's arguments, the confidence score.", real: "A free-form dictionary on each event. Contracts read named keys out of it.", where: "lab/trace/schema.py" },
    adapter: { t: "Adapter", plain: "The small piece of code that plugs your agent into the harness. Like a travel plug: your appliance is unchanged, the socket is unchanged, the adapter bridges them.", real: "Anything satisfying the Trainee protocol — two methods, open() and reply(). Named by a dotted path in an environment variable, so nothing is imported or subclassed.", where: "roleplay/runtime.py, examples/adapters/" },
    protocol: { t: "Protocol", plain: "A promise about shape, not a parent to inherit from. 'If your object has these two methods, it fits.'", real: "typing.Protocol. The harness never asks your class to extend a base class, which is why pointing it at somebody else's agent needs no changes to that agent.", where: "roleplay/runtime.py" },
    factory: { t: "Factory", plain: "A function that builds the thing, rather than the thing itself.", real: "LAB_TRAINEE_FACTORY holds a dotted path to a function. The harness imports it at run time and calls it to get a trainee for each session.", where: "roleplay/live.py" },
    scenario: { t: "Scenario", plain: "One test case, written as a plain text file a non-programmer can read and edit.", real: "A YAML file with an id, a customer, what the trainee says, what a human thinks the verdict should be, and the assertions. 195 of them live under scenarios/.", where: "scenarios/" },
    corpus: { t: "Corpus", plain: "The whole collection of test cases, kept together and checked as a set.", real: "scenarios/, loaded and validated against a schema with closed vocabularies — so a typo in a tag is a loud load error, not a silently empty test run.", where: "scenarios/, evallab validate" },
    persona: { t: "Persona", plain: "The character the AI customer plays: cautious, aggressive, in a hurry.", real: "A profile with concerns, objections and a verbosity setting. The persona decides the move; a separate voice decides the words, so the two cannot drift into being two customers.", where: "roleplay/persona.py, scenarios/*/customers/" },
    gatedfact: { t: "Gated fact", plain: "Something the simulated customer knows but will not say until asked properly.", real: "How a scenario can test whether the agent actually elicited information rather than being handed it.", where: "lab/simulator/persona.py" },
    contract: { t: "Contract", plain: "An inspector with one specific question. 'Was that promise kept?' 'Was the same thing asked twice?'", real: "Six declarative classes that each read the trace and return a pass, a fail, or an explicit 'this did not apply'. They decide on position in the event stream, never on wall-clock time.", where: "lab/checks/contracts.py" },
    vacuous: { t: "Vacuous pass", plain: "A check that passed because it never actually looked at anything. The most dangerous kind of green.", real: "Tracked explicitly: the engine counts checks that were satisfied trivially, so a suite cannot get quieter and look healthier.", where: "lab/checks/engine.py" },
    judge: { t: "Judge (LLM-as-judge)", plain: "Using an AI to mark another AI's work — like hiring an examiner. Useful, and useless until you have checked the examiner.", real: "A prompt plus a strict parser. It cannot gate anything until its calibration report clears a threshold.", where: "lab/judges/judge.py" },
    calibration: { t: "Calibration", plain: "Testing the tester. You give the examiner papers whose correct marks you already know, and see how many they get right.", real: "Hand-labelled items scored by the judge, producing true-positive rate, true-negative rate, precision, F1 and Cohen's kappa. Version 1 of one judge caught 2 of 8 real failures and was refused.", where: "lab/judges/calibration.py" },
    tpr: { t: "TPR / TNR", plain: "Of the things that really were wrong, how many did it catch (TPR). Of the things that really were fine, how many did it leave alone (TNR).", real: "The two numbers the registry gate reads. Both must clear 0.85 or require_calibrated() raises and the build stops.", where: "lab/judges/registry.py" },
    gate: { t: "Gate", plain: "A rule that can stop the build. Not a warning — a no.", real: "Two senses here. In CI: a check whose failure is fatal. In scoring: a requirement that cannot be traded off against points, so a session with a failed gate fails at any score.", where: "lab/judges/registry.py, roleplay/scorecard.py" },
    rubric: { t: "Rubric", plain: "The mark scheme the product shipped with: five things, four points each, twenty in total.", real: "roleplay/rubric_v1.md and RubricScorer. It carries three deliberate defects, including scoring compliance by counting keywords.", where: "roleplay/scorer.py" },
    scorecard: { t: "Scorecard", plain: "The better mark scheme: twenty-eight specific behaviours, each with a source and a stated denominator.", real: "roleplay/scorecard.py — 28 KPIs in seven groups. Eight are gates worth zero points, so a compliance requirement can never be averaged away.", where: "roleplay/scorecard.py" },
    kpi: { t: "KPI", plain: "One thing being measured, written as an observable behaviour rather than a vibe.", real: "Each row names its detector, its denominator, and the business metric it is a leading indicator for. A row that cannot name all three is refused at import.", where: "roleplay/scorecard.py" },
    denominator: { t: "Denominator", plain: "The 'out of what'. '20%' means nothing; '4 out of 20' means something.", real: "A house rule: every rate in this repository carries its denominator, in code and in every report. A naked percentage is treated as a defect.", where: "lab/report/report.py" },
    ledger: { t: "Ledger", plain: "The product's own written record of what it did — the disclosures it logged, the flags it raised.", real: "tool_call events in the trace. Reading the ledger instead of the transcript is the difference between the shipped rubric and the cited gate.", where: "roleplay/runtime.py" },
    register: { t: "Register", plain: "The official list of things that must be said, and the approved wordings.", real: "Two kinds. A demo disclosure register keyed by market, and four cited regulatory registers with paragraph citations.", where: "roleplay/register.py, scenarios/advisory/registers/" },
    regime: { t: "Regime", plain: "Which country's rulebook you are being judged against.", real: "Four cited registers: FCA, MAS, Reg BI and SFC/IA. The same transcript can pass under one and fail under another, which is the point.", where: "roleplay/regime_eval.py" },
    tts: { t: "TTS", plain: "Text to speech. Turning a written sentence into audio.", real: "ElevenLabs in this repo. Every adviser and customer line in the recorded calls was really synthesised.", where: "lab/voice/engines/" },
    stt: { t: "STT / ASR", plain: "Speech to text. Turning audio back into a written sentence.", real: "Deepgram. It returns two transcripts: a prettified one with punctuation, and a raw one without. The raw one is what gets graded.", where: "lab/voice/engines/" },
    wer: { t: "WER", plain: "Word error rate — how wrong a transcript is, as a fraction of the words.", real: "Reported as two numbers, raw and normalised, because perfect recognition scores about 50% raw against a synthesis reference. Quoting the raw figure alone is the trap the file is named after.", where: "lab/voice/wer.py" },
    smartformat: { t: "smart_format", plain: "The recogniser's option that adds punctuation and tidies numbers.", real: "Off for the graded transcript, because the prettified string fabricates a word error rate. Turning it off removes every question mark — which is how a question detector that looks for '?' scored a call of five questions at zero.", where: "lab/voice/engines/" },
    fixture: { t: "Fixture", plain: "A saved recording of a real run, kept in the repository so the test can be repeated for free.", real: "Everything under fixtures/. Recorded once by a script that spends money; replayed forever by tests that spend nothing.", where: "fixtures/, scripts/" },
    replay: { t: "Replay", plain: "Re-running a saved conversation instead of holding a new one.", real: "evallab replay re-checks committed traces with no agent and no keys. It is blind to a prompt change by design, which is stated as a limitation rather than hidden.", where: "lab/cli.py" },
    passk: { t: "pass^k", plain: "Run the same test k times. Did it pass every single time?", real: "Because an AI can pass once by luck. A test that passes sometimes gets its own verdict, FLAKY — never counted as a pass.", where: "lab/simulator/passk.py" },
    flaky: { t: "FLAKY", plain: "A third answer beside pass and fail: it did not do the same thing twice.", real: "A first-class verdict. Treating unstable as passing is how a suite becomes decorative.", where: "lab/simulator/passk.py" },
    flakeband: { t: "Flake band", plain: "How much wobble the harness itself has, measured, so you know which wobble is the agent's.", real: "Measured rather than assumed, from repeated identical runs.", where: "lab/simulator/flake_band.py" },
    clock: { t: "Injected clock", plain: "Instead of asking the computer what time it is, the code is handed a clock. In a test you hand it a fake one.", real: "Why every timing figure here is testable, and why a check decides on event position rather than timestamp.", where: "lab/clock.py" },
    rag: { t: "RAG", plain: "An AI that looks things up in documents before answering.", real: "ragcheck/ scores the looking-up separately from the answering, and never averages the two into one number.", where: "ragcheck/" },
    groundedness: { t: "Groundedness", plain: "Is every claim in the answer actually supported by the documents it found?", real: "Scored per claim, separately from retrieval quality. A perfect retrieval and a fabricated answer must not average into a good score.", where: "ragcheck/" },
    selection: { t: "Test selection", plain: "Working out which tests a code change could possibly affect, so you can run those first.", real: "Derived from traces, and fail-safe: if it is unsure, it includes the test. It measures its own miss rate and refuses to gate if that rate is too high.", where: "lab/selection/" },
    workbook: { t: "The scenario workbook", plain: "The same test cases as a spreadsheet, so the person who knows the domain can write them without meeting YAML.", real: "Four sheets — Scenarios, Turns, Assertions, and a Reference sheet listing every legal value. An Excel row gets no validation of its own: it is converted into exactly the mapping the YAML parser produces and handed to the same model, so there is one rulebook and no second copy to drift.", where: "roleplay/excel_corpus.py, make scenarios-excel" },
    sourceoftruth: { t: "Source of truth", plain: "When the same thing is written in two places, the one that wins.", real: "YAML, not the spreadsheet. A .xlsx does not diff in a pull request, two people editing it cannot merge, git blame stops working, and Excel quietly turns a code like 007 into 7. So scenarios are written in the workbook and committed as YAML: export, edit, import, review the diff, commit.", where: "roleplay/excel_corpus.py write_yaml()" },
    ci: { t: "CI gate", plain: "The automatic check that runs before code is allowed in.", real: "An ordered offline gate, cheapest first, stopping at the first failure. Some steps require artefacts to regenerate byte for byte.", where: "Makefile, docs/GATES.md" }
  };

  /* ---------------------------------------------------------------- helpers */
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var lessons = $$(".lesson");
  var links = $$(".rail__link");
  var pane = $(".pane");
  var seen = {};

  function store(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private window */ } }
  function load(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }

  function show(id, push) {
    var found = false;
    lessons.forEach(function (l) {
      var on = l.id === id;
      l.classList.toggle("is-on", on);
      if (on) found = true;
    });
    if (!found) return false;
    links.forEach(function (a) {
      var on = a.getAttribute("data-go") === id;
      a.classList.toggle("is-on", on);
      if (on) a.classList.add("is-seen");
    });
    seen[id] = 1;
    store("cel-learn-last", id);
    store("cel-learn-seen", Object.keys(seen).join(","));
    progress();
    pager(id);
    if (push && window.location.hash !== "#" + id) {
      try { history.replaceState(null, "", "#" + id); } catch (e) { window.location.hash = id; }
    }
    if (push) window.scrollTo({ top: 0, behavior: "auto" });
    $(".rail").classList.remove("is-open");
    return true;
  }

  function progress() {
    var n = Object.keys(seen).length, total = lessons.length;
    var bar = $(".rail__bar i"), count = $(".rail__count");
    if (bar) bar.style.width = Math.round((n / total) * 100) + "%";
    if (count) count.textContent = n + " of " + total + " read";
  }

  function pager(id) {
    var order = lessons.map(function (l) { return l.id; });
    var i = order.indexOf(id);
    var lesson = $("#" + id);
    var prev = $(".pager [data-prev]", lesson), next = $(".pager [data-next]", lesson);
    function label(btn, j, dir) {
      if (!btn) return;
      if (j < 0 || j >= order.length) { btn.disabled = true; btn.innerHTML = "<i>" + dir + "</i>—"; return; }
      btn.disabled = false;
      btn.setAttribute("data-go", order[j]);
      var title = $("#" + order[j] + " h1");
      btn.innerHTML = "<i>" + dir + "</i>" + (title ? title.textContent : order[j]);
    }
    label(prev, i - 1, "Previous");
    label(next, i + 1, "Next");
  }

  /* ------------------------------------------------------------- term popup */
  var pop = document.createElement("div");
  pop.className = "pop";
  pop.hidden = true;
  pop.setAttribute("role", "dialog");
  document.body.appendChild(pop);

  function openTerm(btn) {
    var key = btn.getAttribute("data-t");
    var d = TERMS[key];
    if (!d) return;
    pop.innerHTML =
      "<b>" + d.t + "</b><p style='margin:0 0 .5rem'>" + d.plain + "</p>" +
      "<p style='margin:0;color:#CFCBC2'>" + d.real + "</p>" +
      (d.where ? "<em>" + d.where + "</em>" : "");
    pop.hidden = false;
    var r = btn.getBoundingClientRect();
    var w = Math.min(pop.offsetWidth, window.innerWidth - 24);
    var left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12));
    var top = r.bottom + 10;
    if (top + pop.offsetHeight > window.innerHeight - 12) top = Math.max(12, r.top - pop.offsetHeight - 10);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }
  function closeTerm() { pop.hidden = true; }

  document.addEventListener("click", function (e) {
    var term = e.target.closest ? e.target.closest(".t") : null;
    if (term) { e.preventDefault(); openTerm(term); return; }
    if (!e.target.closest || !e.target.closest(".pop")) closeTerm();
    var go = e.target.closest ? e.target.closest("[data-go]") : null;
    if (go) { e.preventDefault(); show(go.getAttribute("data-go"), true); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeTerm();
    if (e.target && /input|textarea/i.test(e.target.tagName)) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      var on = lessons.filter(function (l) { return l.classList.contains("is-on"); })[0];
      if (!on) return;
      var btn = $(".pager [data-" + (e.key === "ArrowRight" ? "next" : "prev") + "]", on);
      if (btn && !btn.disabled) show(btn.getAttribute("data-go"), true);
    }
  });
  window.addEventListener("resize", closeTerm);

  /* ------------------------------------------------------------- glossary */
  var glossHost = $("[data-glossary]");
  if (glossHost) {
    var keys = Object.keys(TERMS).sort(function (a, b) { return TERMS[a].t.localeCompare(TERMS[b].t); });
    glossHost.innerHTML = keys.map(function (k) {
      var d = TERMS[k];
      return "<div><dt>" + d.t + "</dt><dd>" + d.plain + " <span style='color:#9E9B93'>" + d.real + "</span>" +
        (d.where ? "<em>" + d.where + "</em>" : "") + "</dd></div>";
    }).join("");
    var filter = $("[data-filter]");
    if (filter) {
      filter.addEventListener("input", function () {
        var q = filter.value.toLowerCase();
        $$("div", glossHost).forEach(function (row) {
          row.style.display = row.textContent.toLowerCase().indexOf(q) === -1 ? "none" : "";
        });
      });
    }
    var count = $("[data-glosscount]");
    if (count) count.textContent = String(keys.length);
  }

  /* ---------------------------------------------------------------- start */
  var toggle = $(".railtoggle");
  if (toggle) toggle.addEventListener("click", function () { $(".rail").classList.toggle("is-open"); });

  (load("cel-learn-seen") || "").split(",").forEach(function (id) { if (id) seen[id] = 1; });
  links.forEach(function (a) { if (seen[a.getAttribute("data-go")]) a.classList.add("is-seen"); });

  var start = (window.location.hash || "").replace("#", "") || load("cel-learn-last") || lessons[0].id;
  if (!show(start, false)) show(lessons[0].id, false);
  window.addEventListener("hashchange", function () {
    var id = (window.location.hash || "").replace("#", "");
    if (id) show(id, false);
  });
})();
