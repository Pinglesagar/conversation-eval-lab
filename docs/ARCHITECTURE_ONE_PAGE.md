# The whole system, in one picture

One diagram, one page. If you can draw this on a whiteboard and talk through the eight
pointers underneath, you can explain the entire repository.

---

```mermaid
%%{init: {
  'theme': 'base',
  'themeVariables': {
    'background': '#0C0B0A',
    'primaryColor': '#17140F',
    'primaryTextColor': '#F4EFE9',
    'primaryBorderColor': '#5A4B41',
    'lineColor': '#8A7A6D',
    'secondaryColor': '#17140F',
    'tertiaryColor': '#100E0B',
    'clusterBkg': '#100E0B',
    'clusterBorder': '#3A322B',
    'edgeLabelBackground': '#17140F',
    'fontFamily': 'ui-monospace, SFMono-Regular, Menlo, monospace',
    'fontSize': '15px'
  }
}}%%
flowchart TD
    subgraph INPUT [" "]
        SC["<b>SCENARIO</b><br/><i>what to test</i><br/>195 committed rows"]
        PE["<b>CUSTOMER PERSONA</b><br/><i>who they are talking to</i><br/>facts revealed only when asked"]
    end

    subgraph CONV ["CONVERSATION"]
        direction LR
        ADV["<b>AI ADVISER</b><br/>the system under test<br/><b>◆ SWAPPABLE ◆</b>"]
        CUS["<b>AI CUSTOMER</b><br/>objects, withholds,<br/>refuses to volunteer"]
        ADV <-->|"turn by turn"| CUS
    end

    subgraph VOICE ["VOICE LAYER · optional"]
        direction LR
        TTS["<b>TTS</b><br/>gives it a voice"] --> WAV(("audio")) --> STT["<b>STT</b><br/>hears it back"]
    end

    TRACE[["<b>═══ THE TRACE ═══</b><br/>one ordered event stream<br/><br/>who spoke · what was said<br/>which tool ran · how long silence lasted<br/><br/><i>15 event kinds. Nothing else is read.</i>"]]

    subgraph GRADE ["THREE JUDGEMENTS · all read only the trace"]
        direction LR
        CON["<b>CONTRACTS</b><br/>deterministic<br/><br/>did the action happen?<br/>in order? no re-ask?<br/><br/><i>free · same answer always</i>"]
        JUD["<b>JUDGES</b><br/>model-graded<br/><br/>was the objection<br/>really engaged?<br/><br/><i>must be calibrated first</i>"]
        REG["<b>REGISTERS</b><br/>cited rules<br/><br/>MAS · FCA · Reg BI · SFC<br/>36 entries, 4 regimes<br/><br/><i>same call, opposite verdicts</i>"]
    end

    GATE{"<b>CALIBRATION GATE</b><br/>is the grader itself<br/>good enough to believe?"}

    OUT["<b>SCORECARD + REPORT</b><br/>every rate carries its denominator<br/>every number traces to a turn"]

    CI{{"<b>CI GATE</b><br/>ship / don't ship"}}

    SC --> CONV
    PE --> CUS
    CONV -->|"text mode"| TRACE
    CONV -.->|"voice mode"| VOICE
    VOICE -->|"grades what was HEARD"| TRACE

    TRACE --> CON
    TRACE --> JUD
    TRACE --> REG

    JUD --> GATE
    GATE -->|"below<br/>threshold"| STOP(["<b>REFUSES TO RUN</b><br/>pipeline stops"])
    GATE -->|"passes"| OUT
    CON --> OUT
    REG --> OUT

    OUT --> CI

    classDef hub fill:#1a1a1a,stroke:#D97757,stroke-width:4px,color:#fff
    classDef swap fill:#2a1a14,stroke:#D97757,stroke-width:3px,color:#fff
    classDef gate fill:#2a1a14,stroke:#D97757,stroke-width:2px,color:#fff
    classDef stop fill:#3a1414,stroke:#c0392b,stroke-width:2px,color:#fff
    class TRACE hub
    class ADV swap
    class GATE,CI gate
    class STOP stop
```

---

## The eight pointers, in plain terms

### 1. Everything flows into one place — the trace

**Plain:** every word, every action and every pause gets written to a single list, in
order. Like a flight recorder for a conversation.

**Why it matters:** nothing in the system is allowed to look at the AI directly. They all
read the list instead. That one rule is what makes the other seven possible.

---

### 2. The adviser is swappable — that's the plug-in point

**Plain:** the box marked ◆ SWAPPABLE ◆ can be your chatbot, my chatbot, a rule-based
bot, or a vendor's API. You give the runner a small Python function: message in, response
and tool calls out. That's the whole contract.

**Say this in the room:** *"Everything downstream reads the trace, so the moment your
agent produces one, all my existing checks run against it unchanged. Write the wrapper on
day one and the first honest number arrives the same day."*

---

### 3. The customer is a test instrument, not scenery

**Plain:** the simulated customer holds facts it will only give up **when asked**. It
objects. It doesn't volunteer.

**Why it matters:** a customer that opens with all its details can never catch an adviser
who never asks. The test would pass someone who did no discovery at all. Withholding is
what makes the discovery score mean anything.

---

### 4. Voice is optional, and it grades what was HEARD

**Plain:** the same conversation can be spoken aloud, recorded, and transcribed back —
and then the grader scores **what the microphone heard**, not what was originally typed.

**Why it matters:** that's what production actually does. If the recogniser mishears a
disclosure, the customer never heard it either, so the grade should reflect that.

**The finding this produced:** a call where the adviser asked five questions scored
**0 out of 4 on discovery** — because the question detector looks for a `?`, and the
graded transcript has no punctuation. Text could never have surfaced that.

---

### 5. Three kinds of judgement, and you pick the cheapest that works

**Plain:**

| | What it's good at | What it can't do |
|---|---|---|
| **Contracts** | did the action actually happen | notice paraphrasing — it's literal |
| **Judges** | did they *really* engage that objection | be trusted until you've measured it |
| **Registers** | did this country's required disclosure occur | cover a rule nobody wrote down |

**Why it matters:** most teams reach for an AI judge for everything. Judges are slow,
cost money, and vary run to run. Use a contract wherever a contract will do.

---

### 6. The gate that refuses — the most important box on the page

**Plain:** before an AI grader is allowed to grade anything, you measure how often it
agrees with a human. If it's below the bar, **the pipeline stops**. Not a warning — a
refusal.

**Why it matters:** an unmeasured grader isn't evidence, it's an opinion with a number
attached. This repo caught its own judge missing **3 out of 4** real failures
(TPR 0.250) and the gate blocked it.

**The line that lands:** *"Every eval tool I surveyed lets you write a judge and use its
verdicts immediately. Mine raises an exception."*

---

### 7. The registers are cited rules, not keyword lists

**Plain:** four regulators — Singapore, UK, US, Hong Kong — each with its own list of
what must be said, and each entry pointing at the actual paragraph of the actual rulebook.

**The killer demo:** **the same conversation gets opposite verdicts under two regimes**,
because the requirements genuinely differ. One row produces four different verdicts on
one sentence.

**Why it matters for a company in many markets:** you cannot have one global compliance
checker. This diagram is the proof of why.

---

### 8. The output is auditable, and the CI gate uses it

**Plain:** every rate printed carries its denominator — never "93%", always "93% of
2,132". Every number traces back to a specific turn in a specific call.

**Why it matters:** a percentage with no denominator is unfalsifiable. A reader can't
tell 9 out of 10 from 900 out of 1,000, and those mean very different things.

---

## If you only draw three boxes

Trace in the middle. Arrows in from the conversation. Arrows out to the three graders.

**Then say:** *"Everything reads the middle box and nothing reads the agent — that's why
I can point this at your system, and why the same suite grades text and speech
unchanged."*

That sentence is the architecture.
