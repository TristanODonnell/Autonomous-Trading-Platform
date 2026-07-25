# Portfolio Graphics — Concept Explanations

A study guide for the visuals in this folder. Each entry: what the picture shows, then one level deeper on the underlying software/systems concept, with why it generalizes beyond this codebase.

---

## System Overview

### Hexagonal adapters hero

**What it shows:** A core hexagon (execution logic) surrounded by five interchangeable adapters (live, paper, backtest, shadow, chaos-testing).

**One level deeper:** This is the *Ports and Adapters* pattern (also called Hexagonal Architecture, coined by Alistair Cockburn). The core defines an interface it needs — "something that can submit an order and tell me what happened" — without knowing or caring what's on the other side. Each adapter (`AlpacaBrokerClient`, `SimulatedBrokerClient`) implements that interface differently.

The reason this matters more than it sounds: it inverts the normal dependency direction. Naively, your trading logic would `import alpaca_sdk` directly and call it. That means alpaca_sdk's assumptions (network calls, async behavior, rate limits) leak into your core logic, and you can never run that logic without a real or mocked Alpaca connection. With the interface inverted, the *core* owns the contract, and Alpaca becomes just one plug-in among several. This is the same principle behind why you can swap a SQL database for an in-memory one in unit tests — the business logic depends on an abstraction, not a concrete implementation.

The generalizable lesson: whenever you find yourself writing "we can't test X without a live Y," that's usually a sign X depends directly on a concrete Y instead of an abstraction of Y.

---

## Safety

### Four-gate hero

**What it shows:** Four sequential checks; failing any one stops the process and grays out (visually skips) all checks after it.

**One level deeper:** This is the *Chain of Responsibility* pattern combined with *fail-fast/short-circuit evaluation*. Two design decisions are doing the real work here:

1. **Ordering by cost and specificity.** Cheap, broad checks run first (is the environment even configured for trading?), expensive or narrow checks run last (is the kill switch on?). This isn't arbitrary — if gate 1 already vetoes, you've saved the cost of gates 2–4 entirely.
2. **Each gate raises a *distinct* exception type.** This is a smaller but important idea: when something fails, you want to know *why* without re-deriving it from a log message. `EnvironmentSafetyError` vs `KillSwitchEngagedError` are different types on purpose, so calling code (and humans debugging at 2am) can `except` or `grep` for the specific failure mode instantly.

The short-circuit behavior mirrors how `and` works in most programming languages: `a() and b() and c()` never calls `b()` if `a()` is falsy. The visual literalizes that language-level behavior into an operational, human-legible diagram.

### Order state machine

**What it shows:** Every legal state transition for an order, laid out as nodes and edges, with terminal states visually distinct.

**One level deeper:** This is a *finite state machine (FSM)* implemented as *data* rather than *control flow*. The naive way to build this is scattered `if order.status == "filled": ...` checks throughout the codebase — which means the rules about what's legal are implicit, duplicated, and easy to violate accidentally in one code path while enforcing them in another.

Encoding transitions as a lookup table (`{state: {event: next_state}}`) makes the rule set a single source of truth. Two consequences fall out of this for free:
- **Terminal states enforce themselves.** `VALID_TRANSITIONS[FILLED] = {}` isn't a comment saying "don't transition a filled order" — it's a structural guarantee. Any code that tries raises immediately.
- **Unknown transitions fail loud, not silent.** Broker events can arrive late, duplicated, or out of order (a fill notification for an already-canceled order, for instance). Without an explicit table, a stray event might get silently ignored or, worse, silently applied. With the table, anything not explicitly listed is an *error*, which is the safer default when you don't know what "should" happen.

This pattern — rules as data instead of buried in conditionals — generalizes to permission systems, workflow engines, and anywhere you have a notion of "valid sequences of things."

---

## Research / Experiments / Strategy

### Funnel hero

**What it shows:** A pipeline of stages — generation, cheap simulation, intermediate simulation, walk-forward, Monte Carlo — where each stage only sees survivors of the last, and the funnel narrows.

**One level deeper:** This is a *cascading filter* / *fail-fast pipeline*, and the specific insight is *ordering by (cost × discriminating power)*. You want to spend your compute budget on your most expensive test only on candidates that have already proven they're not obviously bad.

The general form of this shows up everywhere: CI pipelines run linting before unit tests before integration tests before deployment, precisely because linting is cheap and catches a huge fraction of trivial mistakes, while integration tests are slow and should only run on code that's already cleared cheaper bars. The economics are the same reason a hospital does a blood pressure check before an MRI.

The subtlety worth noting: "cheap" and "intermediate" simulation aren't different *kinds* of tests here, they're the same test with different parameters (shorter window, looser thresholds vs. full window, real thresholds). That's a nice trick — you don't need to build two separate systems, you need one system with a dial.

### Walk-forward Gantt inset

**What it shows:** Overlapping windows — 365 days of "training" data followed by 90 days of "testing" data — sliding forward through time across 9 folds.

**One level deeper:** This is *rolling-origin cross-validation*, and it exists to fight a specific failure mode: **overfitting to a single historical period.** If you tune a strategy once against 2020–2023 data and it looks great, you don't actually know if it's a good strategy or if it just happens to match what happened in those specific 3 years (a global pandemic, a rate-hike cycle, etc.).

Standard k-fold cross-validation (common in ML) shuffles data randomly into folds — but you *can't* do that with time series, because it would let a fold "train" on data from the future relative to what it's "testing," which is nonsensical for financial data (you can't have known about a future event when the model was supposedly trained). Walk-forward validation respects the arrow of time: every test window is strictly *after* its corresponding train window, and the whole apparatus slides forward together. A strategy only earns credibility by clearing the bar repeatedly across many different multi-year slices of history, not once.

### Indicator matrix

**What it shows:** 13 indicators, each with a labeled "value domain" (e.g., 0–100, unbounded/zero-centered) and which rule types they're allowed to drive.

**One level deeper:** This is really a **type system implemented as a lookup table instead of a compiler-enforced type.** In a strongly-typed language you might model this as `RSI: BoundedIndicator[0,100]` vs `SMA: UnboundedPriceIndicator`, and the compiler would reject nonsensical combinations at build time. Python doesn't give you that for free, so the domain metadata dictionary is a manual re-implementation of the same idea: "you can't apply a ±10 threshold rule to a value that natively ranges 0–100, because the semantics don't line up" (an RSI reading of 55 isn't "10 points above neutral" in any meaningful sense the way a zero-centered momentum value of 10 is).

The generalizable lesson: when you don't have (or don't want) a type system, a documented domain-compatibility table is the fallback — it's weaker (checked at runtime or by convention, not compile time) but still prevents an entire category of "this technically runs but produces garbage" bugs.

---

## Governance

### Drawdown ladder hero

**What it shows:** Five severity rungs (NORMAL → BREACHED) with an allocation multiplier that drops as severity rises; escalation can jump straight to any rung, recovery steps down one rung at a time.

**One level deeper:** This is a *hysteresis* control pattern, borrowed directly from control systems engineering (a thermostat is the classic example: it doesn't turn the heater on and off exactly at the target temperature — it uses a dead-band, e.g., on below 68°F, off above 72°F, to avoid rapid on/off cycling right at the boundary).

Applied here: if recovery were symmetric with escalation (jump straight back to NORMAL the instant the drawdown ticks back under the threshold), you'd get "flapping" — a portfolio oscillating between full allocation and zero allocation every time performance hovers right around a limit, which is itself destabilizing and costly (transaction costs, missed opportunity). By making recovery *slow* (one rung at a time, gated by a cooldown that gets longer at worse severities, plus a hysteresis band so a marginal improvement doesn't count), the system asymmetrically trusts "things are getting worse" faster than it trusts "things are getting better" — which is the conservative, safety-appropriate choice.

The BREACHED rung requiring a human acknowledgment to exit is a related but distinct idea: an explicit **human-in-the-loop gate** for the worst-case state, so the system can never fully self-heal from its most severe failure mode without someone confirming reality actually matches the data.

### Strategy lifecycle diagram

**What it shows:** Another FSM (approved_research → paper → live, plus reject/retire edges), styled to match the order and universe diagrams.

**One level deeper:** Same FSM-as-data principle as the order state machine, but layered with **role-based authorization per edge** (mentioned in the copy, not fully drawn here): moving into `approved_live` requires admin approval specifically, while earlier transitions can be approved by a researcher or system-risk role. This is *attribute-based access control applied to state transitions* — the "can this happen" question isn't just "is this a legal state transition" but "is this legal state transition being requested by someone with sufficient authority." Two independent axes (state legality, actor authority) both have to clear before a transition executes.

The REJECTED state looping back to PROPOSED (rather than being a dead end like FILLED) is worth noticing precisely *because* it's different from the order machine's terminal states — it shows that "terminal" isn't a universal property of FSMs, it's a property of the specific domain being modeled. A rejected strategy can be reworked and resubmitted; a filled order genuinely cannot be un-filled.

### Blended quality score (illustrative)

**What it shows:** Two curves over time — alpha (weight given to live data) rising, and a blended score shifting as it does.

**One level deeper:** This is the **cold-start problem**, and the blending formula (`alpha × live + (1 − alpha) × backtest`) is a *convex combination* — a weighted average where the weights always sum to 1, sliding smoothly between two information sources as confidence in one of them increases.

The cold-start problem shows up any time you have a fast-but-noisy signal and a slow-but-reliable one and need to make decisions before the reliable one has accumulated enough data. Recommendation systems face this constantly: a brand-new user has no interaction history, so early recommendations lean on demographic/popularity signals (fast, weak), and gradually shift weight toward personalized signals as the user's own history accumulates (slow, strong). The mathematical shape — a smooth, monotonic shift in blend weight rather than a hard cutover — matters because a hard cutover (backtest-only until day 30, then live-only from day 31) would create a visible discontinuity and invites gaming the exact cutover point. A smooth blend degrades gracefully instead.

---

## Portfolio Intelligence

### Black-Litterman flow

**What it shows:** Market weights + risk aversion → an "implied prior" (π) → blended with operator views → posterior returns → optimizer.

**One level deeper:** This is applied **Bayesian inference**. The general Bayesian recipe is: start with a *prior* belief, observe *evidence*, combine them (weighted by how confident you are in each) into a *posterior* belief that's more informed than either alone.

The specific genius of Black-Litterman (the 1990 Goldman Sachs model this is implementing) is *what it picks as the prior*. The naive approach to portfolio optimization asks a human to directly estimate expected returns for every asset — which sounds reasonable but is empirically one of the worst-conditioned inputs you can feed an optimizer: small errors in return estimates get amplified into wild, unstable portfolio weights (a well-known failure mode called "estimation error maximization"). Instead of asking "what do you think AAPL will return next year" cold, Black-Litterman starts from *reverse-engineering* what expected returns would have to be, given current market-cap weights and a risk-aversion parameter, for the market portfolio itself to be the rational, optimal choice. That's the equilibrium prior (π = δΣw). It's not "the truth" — it's a *sane, well-conditioned starting point* that reflects the aggregate wisdom already priced into the market.

Then — and only then — do you let a human nudge specific points away from that prior, weighted by how confident they are. The result is a posterior that's mostly market consensus with your genuine, specific convictions layered in, rather than a portfolio built entirely from scratch on your (probably overconfident) point estimates.

### Run contexts table

**What it shows:** Two columns — contexts the service is allowed to run in, and contexts where it's explicitly forbidden.

**One level deeper:** Checking *both* an allowlist and a blocklist (rather than just one) is a form of **defense in depth** — the idea that a single control, even a good one, can have edge cases or be misconfigured, so you layer independent controls that would each, individually, catch the failure.

Concretely: an allowlist-only check has a subtle gap — what if a new context string gets added somewhere in the codebase and, due to a typo or default fallback, isn't recognized as "not in the allowlist" the way you'd expect (e.g., some fallback logic treats unrecognized values as permitted rather than denied)? A blocklist closes that gap for the *specific known-dangerous* contexts, independent of whatever bug might exist in the allowlist logic. Belt and suspenders — if either check alone has a bug, the other might still catch the dangerous case.

### Ranking radar

**What it shows:** One strategy's score broken into 7 weighted components, plotted as a polygon on a radar chart.

**One level deeper:** This is **multi-criteria decision analysis (MCDA)** made visual. Reducing a strategy to a single composite number (0.68!) is useful for sorting a list, but it destroys information — two strategies can arrive at the same composite score via completely different profiles (one is robust everywhere but mediocre, another is excellent on 3 axes and weak on 4). A radar chart is specifically good at surfacing *shape*, not just *magnitude* — an experienced researcher glancing at a lopsided polygon (e.g., strong on overfitting_resistance, weak on walk_forward_consistency) learns something a single scalar score hides entirely: this candidate's edge might be fragile in one specific dimension, even though its overall grade looks fine.

The general lesson: whenever you're forced to reduce a multi-dimensional evaluation to one number for ranking/sorting purposes, keep the underlying components around and visualize them separately too — the aggregate is for *sorting*, the components are for *understanding*.

### Clustering schematic

**What it shows:** Points (strategies) grouped into rings (clusters) based on similarity, with independent outliers left unringed.

**One level deeper:** This demonstrates why **ranking and diversity are orthogonal concerns.** A ranked top-10 list optimizes for "individually good," but says nothing about whether those 10 are 10 *independent* sources of edge or 2 real ideas each represented 5 times with minor parameter tweaks. If you deploy capital across a "diversified" top-10 that's secretly 2 clusters, you have far less actual diversification than the count suggests — all 5 members of a cluster will likely succeed and fail together, meaning your real effective bet count is closer to 2, not 10.

Clustering (grouping by similarity, unsupervised, without needing labels for "which cluster is this") is exactly the tool for surfacing that hidden redundancy. The specific algorithm sketched here — single-linkage — builds clusters bottom-up by repeatedly merging the two closest points/clusters, which tends to produce elongated, chain-like clusters (good at catching "these are basically variations of one idea" because near-duplicates chain together naturally).

---

## Dataset Layer & Features

### Lineage flowchart

**What it shows:** Raw bars → corporate-action adjustment → adjusted bars (separately versioned) → feature pipeline (gated) → simulation cache.

**One level deeper:** This is an **immutable data pipeline**, and the specific discipline is: *never mutate an artifact in place — always derive a new, separately versioned artifact.* The alternative (mutating raw bars in place when a stock splits, for instance) sounds convenient but destroys your ability to reproduce old results — a backtest run last month against "raw bars" implicitly assumed pre-split prices; if you silently adjust those prices in place today, that old backtest is no longer reproducible even though nothing about the backtest code changed.

Keeping raw and adjusted as separate, independently versioned datasets means any downstream consumer (a feature pipeline, a cached simulation, an audit record) can pin exactly which version of which artifact it depended on, forever. This is the same principle behind why compiled build artifacts get content-hashed (like Docker image digests or npm lockfiles) — reproducibility requires that "the same input" unambiguously means "the same input," not "whatever that name currently points to."

### Hive partition tree

**What it shows:** A literal folder structure — `symbol=AAPL/date=2026-07-14/data.parquet`.

**One level deeper:** This is **partition pruning** made visible. Encoding the partition key directly into the folder path (Hive-style partitioning, originally from Apache Hive/Spark conventions) means a query engine reading "give me AAPL data for July 2026" can skip opening thousands of irrelevant files just by pattern-matching folder names — it never has to read a single byte of MSFT's 2019 data to know it's irrelevant. This is dramatically cheaper than storing everything in one giant file (or table) and filtering by scanning row-by-row. The tradeoff: pick partition keys you'll actually filter by often (symbol, date are natural here since almost every query touches a specific symbol/date range) — a poorly chosen partition key (e.g., partitioning by a rarely-filtered column) gets you the folder-management overhead without the query speedup benefit.

### Universe governance state diagram

**What it shows:** Third FSM (CANDIDATE → PROPOSED → ACTIVE → RETIRED), same visual grammar as the order and strategy diagrams.

**One level deeper:** Nothing new mechanically beyond the FSM-as-data principle covered above — but the *design choice to visually match all three* is itself worth noting as a technique: once a reader has learned to parse one of these diagrams (terminal states are visually distinct, arrows show legal moves, color means severity/category), they get the other two "for free" — they don't have to re-learn a visual language three times. This is the same reasoning behind consistent UI design systems: a user who learns one modal's behavior shouldn't have to re-learn a different modal's behavior elsewhere in the same product.

The specific domain point: "a universe that silently drifts makes every backtest optimistic" refers to **survivorship bias** — if your "tradeable universe" for a 2015 backtest is built using *today's* list of companies (which excludes everything that went bankrupt or got delisted since), you're implicitly cheating: you're only testing against symbols you already know survived. Versioning the universe the same rigorous way you version market data prevents that leak.

### Three-column separation

**What it shows:** Corporate actions, survivorship, and ticker lifecycle shown side by side with no connecting arrows.

**One level deeper:** The absence of arrows is the entire point, and it's a real architectural decision, not an oversight: these three concerns are *related in the domain* (they're all "things that happen to a company's identity or tradability over time") but **solving them with one unified system would be a mistake**, because they have different correctness requirements and different failure modes. Corporate action adjustment needs to be applied consistently at processing time; survivorship needs a hard pre-flight gate *and* a softer per-fold check; ticker lifecycle needs cycle-safe graph traversal (a rename chain could theoretically loop). Cramming these into one "CompanyLifecycleService" would create a component that's hard to reason about because it's actually three different problems wearing one trenchcoat. This is a mild but real instance of the **Single Responsibility Principle** — not "one class, one method" as it's often caricatured, but "one class, one reason to change."

---

## Testing Suite

### Stat tile row

**What it shows:** Raw counts — 4,080 tests, 362 files, 153 safety tests, near-zero skip debt.

**One level deeper:** The interesting concept isn't the numbers themselves but *why "skip/xfail debt" is tracked as a metric at all*. `@pytest.skip` and `@pytest.xfail` exist as legitimate escape hatches (a test that's known-broken but you don't want blocking the whole CI pipeline while you fix it). The problem is they're *silent* by default in most dashboards — a suite can accumulate hundreds of skipped tests over years, each individually justified at the time, until "green CI" no longer means what it used to mean, because a large fraction of what would have caught real bugs simply never runs. Treating skip/xfail count as a tracked, near-zero-target metric (rather than an invisible accumulation) is the fix — it turns an implicit, decaying guarantee into an explicit, monitored one.

### Schema-drift two-tier diagram

**What it shows:** A fast tier comparing contract code to ORM code directly, and a slower tier comparing ORM code to a real running Postgres database.

**One level deeper:** This addresses a specific and sneaky bug class: **two independent representations of the same thing silently diverging.** A Pydantic contract and a SQLAlchemy ORM model both describe "what a Fill looks like," but they're two separate pieces of code — nothing *forces* them to agree. If someone adds a field to one and forgets the other, everything might still run fine in tests that only exercise one side, and the mismatch only surfaces at runtime when real data flows through both.

The two-tier design catches two *different* versions of this problem:
- **Fast tier** catches "the two pieces of Python code disagree with each other" — cheap, no infrastructure, but it can't tell you whether either one actually matches the real database schema (e.g., if a migration was hand-edited or partially applied).
- **Integration tier** catches "the code disagrees with the actual database" by literally running a migration against real Postgres and diffing. This is the only tier that can catch a broken or incompletely-applied migration, because it's the only tier that touches a real database at all.

Neither tier alone is sufficient — the fast tier is blind to real-database drift, and running only the slow tier on every commit would be prohibitively expensive. Together they cover the full failure space at acceptable cost.

### SQLite vs Postgres lane comparison

**What it shows:** Local dev uses fast in-memory SQLite; CI uses a real Postgres container; both run the identical test suite.

**One level deeper:** This is the classic **fidelity vs. speed tradeoff**, resolved by *not choosing* — running both, at different points in the loop. SQLite is fast enough to run on every save during local development (tight feedback loop), but it isn't Postgres — it has different concurrency behavior, different type coercion rules, different constraint enforcement in some edge cases. If you only tested against SQLite, you could ship code that passes every test locally and then breaks in production because of a Postgres-specific behavior SQLite quietly papered over.

The load-bearing insight (explicitly called out in the copy) is that this split is *only* safe because of the schema-drift integration tier described above — without something explicitly verifying that the SQLite-tested code path also holds against real Postgres, "all green locally" would be a false signal. The two-lane setup and the schema-drift check aren't separate ideas; the second is what justifies trusting the first.

---

## Observability

### Instrument breakdown

**What it shows:** A stacked bar of 261 metrics: 129 histograms, 126 counters, 4 gauges, 2 up-down counters.

**One level deeper:** These are the four standard OpenTelemetry/Prometheus-style metric instrument types, and each answers a fundamentally different question:
- **Counter** — monotonically increasing, only goes up (e.g., "total orders submitted"). Answers "how many, cumulatively."
- **Histogram** — records a *distribution* of observed values (e.g., "order execution latency"), letting you later ask "what's the p50/p95/p99," not just "what's the average" (which can hide a long tail of bad outcomes behind a comfortable-looking mean).
- **Gauge** — a value that can go up or down and represents a *current* state (e.g., "current open positions"), sampled at read time rather than accumulated.
- **Up-down counter** — like a counter but can decrement (e.g., "active connections"), useful when you want cumulative-style aggregation but the quantity genuinely rises and falls.

The lean toward histograms specifically matters because *averages lie about tails*. If order latency averages 50ms, that sounds fine — but if the p99 is 4 seconds, there's a real, painful problem for 1% of orders that a single average number completely hides. A system relying heavily on histograms is one that's been built by people who've been burned by trusting averages before.

### Stamping chain

**What it shows:** A shared `RuntimeContext` → `tracing.py` → span attributes (`ratp.correlation_id`, etc.) → Grafana.

**One level deeper:** This is **ambient/implicit context propagation**, implemented via Python's `contextvars` module. The problem it solves: in a system with many nested function calls across async boundaries and thread pools, you want every log line and trace span to carry the same "which run/request/job is this" identifiers — but explicitly threading an `correlation_id` parameter through every single function signature in the codebase is both tedious and error-prone (one function forgets to pass it along, and you get an orphaned span that can't be correlated with the rest of the trace).

`contextvars` solves this the way thread-local storage solves a similar problem in synchronous code, but correctly across `async`/`await` boundaries (which plain thread-locals don't handle correctly, since a single thread can interleave multiple concurrent coroutines). You set the context once, near the top of a request/job, and every nested call — no matter how deep, no matter how many `async def` layers down — can read the same ambient values without having to receive them as an explicit argument. The tradeoff is that it's implicit "magic" (harder to trace by reading code alone, since the values aren't visible in function signatures) — a real cost, worth paying here because the alternative (parameter-threading through hundreds of call sites) is worse in practice.

### Config-freeze timeline

**What it shows:** A config value read once at the start of a run and held constant, with a mid-run edit attempt marked as not landing.

**One level deeper:** This is the **snapshot isolation** pattern — the same idea that underlies database transaction isolation levels (specifically, "repeatable read" or "snapshot isolation," where a transaction sees a consistent view of the world as of when it started, even if other transactions commit changes concurrently). Applied here at the application level rather than the database level: instead of trusting "read from the database" to always be safe, the code deliberately reads once and caches, making external mutation *structurally impossible* to observe mid-run — not because of a lock or a check, but because the second read that *would* observe the change simply doesn't exist in the code.

This is a stronger guarantee than "we added a check to prevent mid-run edits from mattering" — a check can have bugs or be bypassed; a code path that literally isn't there can't misbehave. When you can convert a runtime invariant into a structural fact about the code's shape, that's usually the more robust choice.

### Chaos matrix

**What it shows:** A literal 9×6 grid mapping failure-injection functions to the subsystem categories they target.

**One level deeper:** This is **fault injection testing** (sometimes called chaos engineering when done in production-like environments, following the lineage of Netflix's Chaos Monkey). The core idea: instead of only testing the happy path and hoping error-handling code works when it matters, you deliberately trigger the bad conditions — a broker mismatch, a missing bar, a governance trigger — in a controlled way and verify the system responds correctly. Bugs in error-handling code are notoriously common precisely *because* error paths are rarely exercised naturally; a code path that only runs during a real production incident is a code path that's effectively untested until the incident happens for real, unless you go out of your way to simulate it beforehand.

Writing injected incidents into real database tables (rather than mocking the failure at the test-assertion level) matters for the same reason described elsewhere in this doc: it exercises the actual downstream consumers of that data (dashboards, alerting, governance triggers) rather than just proving the injection function itself ran.

---

## Backtesting & Simulation

### Cash buckets stacked-area (schematic)

**What it shows:** Settled, unsettled, and reserved cash as three separate, shifting bands over time.

**One level deeper:** This models **T+settlement**, a real feature of how securities markets work: when you sell a stock, you don't receive usable cash instantly — funds "settle" after a delay (historically T+2, now often T+1). A backtest that treats "cash" as a single number implicitly assumes settlement is instant, which lets a simulated strategy spend money it hasn't actually received yet — a subtle look-ahead-adjacent bug that would make a backtest's results unrealistically good (or let it execute trades a real account literally couldn't afford yet).

"Reserved" cash is a related but distinct concept: money already earmarked for open buy orders that haven't filled yet shouldn't be double-counted as available for a *different* new order. Splitting cash into these three buckets, with "buying power" defined as a hard gate (`settled − reserved`), makes both failure modes structurally impossible rather than relying on downstream code to remember the distinction.

### Order latency timeline

**What it shows:** Two bars — a signal bar (where a decision is made, at its close) and a later execution bar (where the fill happens, at its open).

**One level deeper:** This distinguishes two different ways to model "it takes time for an order to reach the market": **delaying the event in time** vs. **degrading the price at the same moment.** The latter (keep the timestamp the same, just apply a worse price) is a common simplification in cruder backtesting engines, but it's subtly wrong — it implies the strategy had knowledge of a future price movement at decision time, just penalized. Genuinely deferring execution to a later bar's opening price is more physically honest: the order literally didn't exist in the market yet at the moment the strategy "decided" to place it, so it can only interact with prices that come *after* that decision, at whatever the market state happens to be by the time it actually executes. This distinction sounds pedantic but is exactly the kind of subtle backtest-realism gap that can make a strategy look profitable in simulation while failing in live trading, because live trading has no way to fake the second interpretation — real orders always execute after real time has passed.

### Sim/live parity diagram

**What it shows:** The live path folding a slice schedule into one order's metadata; the sim path genuinely scheduling and filling N discrete child orders.

**One level deeper:** This is an honest depiction of a **partial abstraction leak** — a case where two code paths share the same *conceptual* model (both use the same TWAP/VWAP slicer classes to compute *what* the schedule of smaller orders should be) but diverge in *execution* because of a real external constraint (the broker integration doesn't support submitting genuine child orders, so the live path has to approximate by attaching the schedule as metadata on a single order instead).

The valuable thing about this diagram, pedagogically, is that it's **not** a case of "everything is unified, don't worry." It's the opposite — it's explicitly documenting *where* parity breaks down and *why*, so nobody mistakenly assumes backtest and live behavior are identical in this dimension. This is a mature engineering habit: rather than pretending an abstraction is perfectly clean, you draw the actual boundary of where it leaks, so people building on top of it know exactly which guarantees they can and can't rely on.

### Slippage clamp gauge

**What it shows:** A number line with a threshold at 30 samples — below it, fall back to safe defaults; at or above it, use a calibrated value.

**One level deeper:** This is a **minimum sample size gate**, the same underlying statistics as why A/B tests need a minimum number of observations before you trust the result. With very few data points, an estimated parameter (here, a slippage/impact coefficient) has high variance — a handful of unusually good or bad fills can swing the estimate wildly in a direction that has nothing to do with the true underlying relationship. Falling back to a conservative, pre-vetted default below the threshold — rather than using a wild, undersampled estimate — trades a small amount of "we're not using the freshest data" for a large amount of "we're not letting noise masquerade as signal." The number 30 specifically echoes a common (if slightly folk-wisdom) statistical heuristic that sample means start behaving reasonably close to normally distributed above roughly that size (related to the Central Limit Theorem), though the "right" threshold in practice is really a judgment call about acceptable estimation variance, not a hard law.

---

## Cross-cutting patterns worth noticing

A few ideas recur across many of these diagrams — recognizing them as the *same* idea wearing different clothes is probably the highest-value takeaway:

1. **FSM-as-data** (order state machine, strategy lifecycle, universe governance) — encode legal transitions as a lookup table, not scattered conditionals.
2. **Fail-fast / cascading filters** (safety gates, research funnel, schema-drift two-tier) — order checks from cheap+broad to expensive+narrow.
3. **Hysteresis / asymmetric response** (drawdown ladder, config-freeze) — escalate fast, recover slow; or make a guarantee structural instead of checked.
4. **Immutable, versioned artifacts** (dataset lineage, Hive partitions, universe versioning) — never mutate in place; derive new versions so old results stay reproducible.
5. **Bayesian blending of prior + evidence** (Black-Litterman, blended quality score) — combine a stable/weak signal with a fresh/strong one, weighted by confidence, and shift the weighting smoothly as confidence changes.
6. **Defense in depth** (four safety gates, allow+block lists, two-tier schema drift) — stack independent checks so one control's bug doesn't become a single point of failure.
