# Playwright and TypeScript: where I actually am, and the bridge

Written for myself before an interview where UI end-to-end automation is on the
job description and is not on my CV. Nothing here claims experience I do not
have. The point of the page is to be precise about the gap, precise about what
transfers, and precise about how long the gap takes to close.

---

## 1. The honest position, in one paragraph

I have never shipped a Playwright suite. I have not written TypeScript
professionally. What I have built is a 719-scenario regression harness for a
production voice-and-chat multi-agent assistant across eight third-party
integrations — pytest and pytest-xdist, three independent verdicts per
conversation, an LLM-judge cascade, invented contract checks, a measured flake
band, failure-ownership triage, and a dashboard and export layer over the top. So
the parts of UI automation that are *test engineering* I have done at scale, and
the parts that are *browser driving* I have not. I would rather say that than
discover it in week two.

---

## 2. What transfers, and it is more than the syntax

| What I already do | The Playwright equivalent |
|---|---|
| pytest fixtures, `conftest.py` scoping, parametrised suites | Playwright fixtures (`test.extend`), `beforeEach`, projects, per-test browser contexts |
| pytest-xdist across workers, with per-worker key sharding | `--shard=1/4` across CI jobs, `fullyParallel`, worker-scoped fixtures |
| trace-first debugging: one auditable trace per run, every verdict anchored to evidence in it | the Playwright **trace viewer** — the same idea, already built: DOM snapshots, network, console, per-action timing |
| a measured flake band (±3.5%) and stability treated as a verdict dimension, not noise | `retries`, the flaky-test report, and the discipline of *fixing* flake instead of retrying it away |
| failure-ownership triage: every red classified product / harness / label / variance before it is believed | exactly the same triage; UI adds a fifth bucket, *environment*, which is usually the biggest one |
| declarative tool contracts — "the agent said it submitted, therefore the submit call must exist" | network assertions and route interception: "the button said saved, therefore a PUT to /orders must have happened and returned 2xx" |
| golden datasets, validated before they are trusted | fixture data and `storageState` auth, versioned and validated the same way |
| release gates with coverage thresholds and a stated pass criterion | the same gate, wired to the Playwright HTML/JUnit reporter in CI |

The two habits I would bring on day one and would expect to matter more than my
Playwright fluency: **a red is not believed until it is classified**, and **a
number is not quoted without its denominator**.

---

## 3. The Playwright-specific things I have studied and would have to prove

* **Locators, not selectors.** `getByRole`, `getByLabel`, `getByText`,
  `getByTestId`. Locators are lazy and re-resolve on use, which is why they
  survive re-renders where a stored element handle does not. Strict mode fails
  when a locator matches more than one node — a feature, and the first thing that
  will bite me.
* **Auto-waiting and web-first assertions.** `expect(locator).toHaveText(...)`
  retries until timeout; `expect(await locator.textContent()).toBe(...)` does
  not. Nearly every "flaky UI test" story I have read is that one line. There is
  no correct use of `waitForTimeout` in a suite I own.
* **Fixtures and isolation.** A fresh browser context per test is the default and
  the reason Playwright is parallel-safe. `storageState` for logged-in state, so
  authentication is set up once and not re-driven through the UI per test.
* **Trace viewer, and `trace: 'on-first-retry'`.** The setting I would enable
  before writing a single spec, for the same reason my own harness writes a trace
  for every run: a failure you cannot read is a failure you re-run.
* **Network control.** `page.route` to stub a third party, `expect(page).toHaveURL`,
  and waiting on responses rather than on time. For an AI product this is how you
  test the UI deterministically while the model is stubbed, and separately test
  the model without the UI.
* **CI sharding and reporting.** `--shard`, blob reports merged across shards, the
  HTML report as a CI artefact. This is the part I have done before under a
  different name.
* **What I would be cautious about.** Screenshot/visual snapshots — cheap to add,
  expensive to own, and they fail for font and platform reasons that say nothing
  about the product; I would gate on them only for a small set of deliberate
  pages. And `page.pause()`/codegen output as *starting* points to be rewritten,
  never as committed tests.
* **Mobile.** Playwright's device emulation is not a real device. Emulation for
  layout and gesture coverage; a device cloud for anything that depends on real
  hardware or a real browser build. I have not used either professionally.

---

## 4. When they ask about TypeScript

The answer, in the order I would say it:

> "Python is where I am strong — my harness, the judges, the CI, all of it. I
> read TypeScript and JavaScript comfortably enough to work through a product
> codebase and to write Playwright specs against it; I have not shipped
> TypeScript as a working language, and I would want review on my first few
> specs. If the existing suite is in TypeScript, I would write in TypeScript
> rather than bolt a second language onto your CI — the language of the tests
> should be the language the team can maintain."

What makes that credible rather than a promise: the things that are actually hard
in a test suite — deciding what the assertion is, keeping non-determinism out of
the verdict, owning a release gate — are language-independent, and I have done
them. `async/await`, typed config objects and a `test.extend` fixture are a week,
not a quarter. What I would *not* claim is fluency in the product's own
TypeScript: reviewing a React data-fetching change on its merits is a different
skill and I would not pretend to it in month one.

---

## 5. A first spec, sketched — NOT RUN

Written from the documentation, not executed: this repository has no Node
toolchain, no `npm install` has happened, and no browser binary has been
downloaded. Treat it as a statement of what I think the shape is, and expect
review comments. The two things it is trying to demonstrate are web-first
assertions and a *contract* between what the UI said and what the network did —
the same decision-versus-action check my Python harness makes against tool calls.

```ts
// tests/booking.spec.ts — SKETCH, UNRUN
import { test, expect } from '@playwright/test';

test.describe('booking a table', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');                       // baseURL comes from the config
  });

  test('a confirmed booking is also a POST that succeeded', async ({ page }) => {
    // Role- and label-based locators, so the test breaks when the accessible
    // name changes and not when a class does.
    await page.getByLabel('Party size').fill('6');
    await page.getByLabel('Date').fill('2026-09-04');

    // Wait on the request, not on a timeout. The response is the evidence.
    const created = page.waitForResponse(
      (r) => r.url().includes('/api/bookings') && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Confirm booking' }).click();
    const response = await created;

    // Web-first assertion: retries until the timeout instead of sampling once.
    await expect(page.getByRole('status')).toHaveText(/booked for 6/i);

    // The contract. The UI claiming success is not the same event as the
    // booking existing — this is the assertion that separates them, and it is
    // the one that would have caught the defect the Python side of this repo
    // is built around.
    expect(response.status()).toBe(201);
    expect((await response.json()).partySize).toBe(6);
  });

  test('a rejected booking says so and creates nothing', async ({ page }) => {
    // Deterministic failure, injected at the network boundary rather than by
    // arranging real unavailability.
    await page.route('**/api/bookings', (route) =>
      route.fulfill({ status: 409, json: { error: 'no tables' } }),
    );
    await page.getByLabel('Party size').fill('6');
    await page.getByRole('button', { name: 'Confirm booking' }).click();

    await expect(page.getByRole('alert')).toContainText('no tables');
    await expect(page.getByRole('status')).toBeHidden();
  });
});
```

```ts
// playwright.config.ts — SKETCH, UNRUN
export default {
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,          // a committed .only is a silent gap
  retries: process.env.CI ? 1 : 0,       // one retry, and flake still gets fixed
  reporter: [['html'], ['junit', { outputFile: 'results.xml' }]],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',             // the trace viewer, for free
    screenshot: 'only-on-failure',
  },
};
```

```yaml
# .github/workflows/e2e.yml — SKETCH, UNRUN. Sharding is the part I have done
# before, under the name pytest-xdist.
strategy:
  fail-fast: false
  matrix:
    shard: [1, 2, 3, 4]
steps:
  - run: npx playwright install --with-deps chromium
  - run: npx playwright test --shard=${{ matrix.shard }}/4
```

Things I already expect to get wrong in review: over-using `getByTestId` where a
role would be stabler, asserting on the network in tests that should not care
about it, and reaching for `page.waitFor*` the first time something races.

---

## 6. Ramp-up, stated as a commitment rather than a hope

* **Week 1** — read the existing suite before changing it. What is covered, what
  is quarantined, what the flake rate actually is, and which reds nobody believes
  any more. Take over the CI job. Write no new specs.
* **Week 2** — first specs on a path I understand, in the style already there, in
  review. Fix the two or three flakiest existing tests, since that is where the
  suite's credibility is leaking.
* **Weeks 3–4** — own it: sharding and runtime, the gate the release actually
  reads, and the coverage gaps written down as a list rather than described as a
  feeling.
* **Month 2 onwards** — extend to the surfaces I would be hired for, which for an
  AI product is the non-deterministic ones. That is the part where I am not
  ramping up.

## 7. What I would ask about their suite in the interview

1. What is in it now — framework, language, how many specs, and what the flake
   rate is when nobody is looking?
2. Which reds does the team currently not believe? That answer tells me what to
   fix first.
3. Is the gate advisory or blocking, and who overrides it?
4. Where does UI E2E stop and the API/eval layer start? I would push most
   assertions down out of the browser, and I would want to know if that fight has
   already been had.
5. Real devices or emulation for mobile, and who owns that bill?
