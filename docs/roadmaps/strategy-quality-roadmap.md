# Strategy quality roadmap: from a first edge to continuous improvement

_Started 2026-11-25 with the user. Planning only; nothing here is built._

**What this doc is for.** The two active plans answer other questions:
`pre-launch-deployment-plan.md` asks whether the system runs safely and unattended, and
`research-generation-plan.md` asks whether the research pipeline measures honestly. This
doc asks the question both lead to: **does the system find strategies that make money
after costs, and how does it keep finding better ones?** It becomes the main plan as the
deployment track finishes. Until then it holds the definition of "working", the levers,
and their triggers, so they are not lost in chat.

**How it links to the other two.**

| Here | There |
|---|---|
| The Research and Live tiers that define "working" | Pre-launch 7.1b quality benchmarks (they gate `auto`, 3.3(b) and 3.3(c), not go-live) |
| Phase A's trigger: the current families do not pass | Research plan, Step 1b board, "G10, decision point" row |
| Phase B's machinery: champion and challenger, decay, retirement | The bench, weekly review and re-sims (pre-launch 3.3(a), `portfolio-rotation-plan.md`) |
| Phase B's mixing lever | Pre-launch 3.3(c), allocation and governance parameter changes |

---

## 1. What "working" means

A strategy is **working** when all of these hold:

1. It beats buy-and-hold of the names in force on the **holdout**, after costs (G9's
   excess return > 0, information ratio at or above a floor still *to set*).
2. Its Sharpe stays positive after a **deflated Sharpe** adjustment for the number of
   configs tried.
3. It came from a pipeline that **found a planted edge and rejected planted noise** (the
   positive and negative controls), so the result says something about the strategy and
   not about a pipeline bug.
4. It holds up for about **8 to 12 weeks of paper trading**: its live Sharpe against its
   holdout Sharpe (the decay ratio) stays above a floor still *to set*.

Items 1 to 3 are the Research tier of pre-launch 7.1b; item 4 is its Live tier.

## 2. Milestones

| # | Milestone | Means | State |
|---|---|---|---|
| M1 | The pipeline can find an edge | Positive control found, negative control rejected | 🟡 designed 2026-11-25 (noisy next-session oracle at 60 / 55 / 52%, noise × 5, outcomes fixed before the run: research plan, top board "Planted controls, M1"); not built |
| M2 | First strategy passes the Research tier | Section 1, items 1 to 3 | ⚪ every baseline so far lost on the holdout |
| M3 | First strategy passes the Live tier | Section 1, item 4 | ⚪ waits on M2 and 3.3(b) |
| M4 | Three uncorrelated strategies live | Pairwise correlation of daily returns below a level *to set*, each passing M3 | ⚪ waits on M3 |

M1 to M3 are Phase A. M4 starts Phase B.

---

## 3. Phase A: the minimum path to a first working strategy

Only what is needed to reach M3. Everything else in section 5 waits for something to
improve.

1. **Finish the current track first.** The research plan's run, G10 and Step 2 may
   produce a winner from the existing families (momentum, MA crossover, mean reversion,
   factor, composites on 5-minute bars). If they do, go straight to M3.
2. **Rigor (lever 6), cheap, can start any time:** deflated Sharpe on the holdout,
   honest trial counting across a run, and the positive and negative controls (M1).
3. **If the current families do not pass** (the G10 decision point): levers 1 and 2
   together. One **daily, cross-sectional** family (for example 3- to 12-month momentum
   or 1-week reversal, ranked across the universe and rebalanced weekly) on about **10
   years of free daily bars**.
4. **Costs (lever 4), basics:** low turnover by design, trading near the close,
   rebalance bands.

Open question before step 3 is built: how much of the stack assumes 5-minute bars (A's
trading loop, the bench re-sim, the research windows, the bar datasets). A daily
strategy can run inside a 5-minute loop by acting once a day; what else assumes
intraday is to be checked.

## 4. Phase B: the improvement loop

Once one strategy works, improvement is a loop run through the same gates, not a list.

```
add uncorrelated strategies ─► better execution ─► better allocation
            ▲                                             │
            └──────── retire decayed strategies ◄─────────┘
```

1. **More strategies (breadth).** Add strategies that differ in horizon, style or input.
   Judge a newcomer by its **marginal contribution** (how much the portfolio improves
   with it), not by its own score.
2. **Better execution.** Compare real fills with the cost model, recalibrate, then refine
   timing, order types and rebalance bands.
3. **Better allocation (3.3(c)).** Start with equal weight or inverse volatility; adopt
   anything more complex only if it beats simple in a rotation replay.
4. **Retire decayed strategies.** Track live against backtest; retire when the gap
   persists (the review's streak rules are this mechanism).
5. **New inputs.** Paid data is worth it when it opens a new kind of strategy, not a
   slightly better version of one already held.
6. **Sharper search.** Better search methods pay most when tuning families known to work.

**Two rules for the loop:**
- Do not re-optimise live strategies on recent data. Parameters change only through the
  research pipeline and its holdout.
- Every change passes the same gate as the original: Research tier → admission → bench
  → review. The system can then only ratchet up.

---

## 5. The levers

Each lever with its concept, why it helps, what to watch, its cost, and when it is
picked up. Ordered by expected impact for this system.

### Lever 1: horizon and style

- **Concept.** Horizon is how long a position is held; style is what is predicted.
  Time-series rules time one asset; cross-sectional rules rank many and hold the best.
- **Why it helps.** Over minutes the expected move is small next to the cost of a trade;
  over weeks it is larger and trades are far fewer. Intraday large caps are contested by
  market makers and high-frequency firms; days-to-weeks mostly is not. "A beats B" is
  easier to predict than "A goes up", and a ranked portfolio partly cancels the market's
  moves. Documented effects: momentum (3 to 12 months, Jegadeesh and Titman 1993),
  short-term reversal, low volatility; quality and value need fundamentals.
- **Watch.** Published effects weaken after publication (often by a third to a half);
  momentum crashes sharply and rarely (2009); long-only captures part of a long-short
  effect.
- **Cost.** Free data; moderate build time.
- **Picked up:** Phase A step 3 (the G10 decision point).

### Lever 2: more history

- **Concept.** A Sharpe ratio is an estimate whose error shrinks with the square root of
  time: SE ≈ √((1 + SR²/2) / years). For a true Sharpe of 0.5: about 0.75 on 2 years (no
  better than luck), 0.34 on 10, 0.25 on about 18 (two standard errors).
- **Why it helps.** It separates real edges from luck, and longer history covers more
  regimes (2008, 2020, 2022).
- **Watch.** Survivorship bias (free data usually lacks delisted companies, which flatters
  results); old market structure (prices in fractions before 2001); corporate actions
  over long spans.
- **Cost.** Free daily bars over many years; survivorship-free data is paid (typically a
  few hundred dollars a year; check current pricing).
- **Picked up:** Phase A step 3, with lever 1.

### Lever 3: combining weak signals

- **Concept.** The fundamental law of active management (Grinold): IR ≈ IC × √breadth.
  For N strategies of Sharpe s and average correlation ρ, combined Sharpe ≈
  s × √(N / (1 + (N − 1)ρ)). Five strategies of 0.3: 0.67 uncorrelated, 0.39 at ρ = 0.5,
  0.31 at ρ = 0.9.
- **Why it helps.** Small edges are easier to find and more robust than large ones;
  combining smooths bad months.
- **Watch.** Signals must really differ; correlations rise in crises; mean-variance
  weights are fragile, equal weight and inverse volatility are robust.
- **Cost.** Free; needs several working strategies first.
- **Picked up:** Phase B (M4), through 3.3(c).

### Lever 4: costs and execution

- **Concept.** Cost per trade is commission (0 at Alpaca), half the spread, slippage or
  impact, and borrow fees if short. Net = gross − turnover × cost per trade. At 9.5x a
  day and 1 bp, about 24% a year; a weekly 20% rebalance at 5 bp, about 0.5% a year.
- **Why it helps.** The available gross edges are small; cost is the most reliable thing
  to cut.
- **Techniques.** Rebalance bands, the closing auction, limit orders, liquid names.
- **Watch.** Paper fills are optimistic (no real market impact); the cost model is only
  as good as its calibration against real fills.
- **Cost.** Free.
- **Picked up:** the basics in Phase A step 4; calibration in Phase B with real fills.

### Lever 5: better inputs

- **Concept.** An edge comes from information others lack, better processing, a risk
  others avoid, or behavioural and structural habits. Price-only data is what everyone
  has.
- **Inputs.** Volume and liquidity (free, but IEX volume is a sliver of the market);
  earnings dates and surprises (post-earnings drift; dates roughly free, surprises often
  paid); fundamentals (EDGAR free but messy, clean point-in-time paid); macro (FRED,
  CBOE: free); sector (mostly free); alternative and options data (expensive).
- **SIP versus IEX.** IEX is one exchange with a few percent of volume; SIP is the
  consolidated feed. For large-cap prices IEX is usually close; its volume and thin
  intraday bars are not. SIP matters most for intraday strategies and volume signals,
  much less for daily strategies with consolidated daily closes. Alpaca's SIP comes with
  its paid data plan (historically about $100 a month; check current pricing). **If the
  system moves to daily strategies, the SIP switch becomes less urgent**: worth deciding
  before the switch weekend is booked (pre-launch D1 and 7.4).
- **Cost.** Free to paid; each input needs its own pipeline, versioning and
  point-in-time handling.
- **Picked up:** Phase B, when an input opens a new kind of strategy.

### Lever 6: evaluation rigor

- **Concept.** Multiple testing: the best of N zero-edge trials sits about √(2 ln N)
  standard errors above zero. With 2 years (SE ≈ 0.75) and 1,000 configs, the best
  zero-edge config shows a Sharpe near 2.8. The baselines' median 2.03 on evaluation and
  −1.60 on the holdout is that effect.
- **Tools.** Untouched holdout, walk-forward, Monte Carlo (all built); deflated Sharpe
  (Bailey and López de Prado), probability of backtest overfitting, preferring plateaus
  to peaks (R13's neighbour check), and honest trial counts.
- **Why it helps.** It does not make strategies better; it stops bad ones being believed.
- **Cost.** Free, short.
- **Picked up:** Phase A step 2.

### Lever 7: search method

- **Concept.** Grid, random (a strong baseline), successive halving (cheap early
  elimination), Bayesian optimisation (models promising regions), evolutionary (mutates
  and combines rules, so it can build composites a grid would not list).
- **Why it helps.** Finds the best settings faster and cheaper within a family that works.
- **Watch.** A more efficient search overfits more efficiently: pair it with lever 6.
- **Cost.** Build time; compute (a box larger than B costs more, though daily strategies
  are cheap to simulate).
- **Picked up:** G10 and Step 2 now for the current families; Phase B for families known
  to work.

### Summary

| Lever | Free, doable now | Comes with time | Costs money |
|---|---|---|---|
| 1. Horizon and style | Daily cross-sectional family | Daily-bar plumbing | — |
| 2. More history | Free daily bars, 10 years | Regime analysis | Survivorship-free data |
| 3. Combining | — | Once several strategies work | — |
| 4. Costs | Bands, the close, turnover caps | Calibration on real fills | — |
| 5. Inputs | Macro, sectors | Fundamentals pipeline | SIP, clean fundamentals, alternative data |
| 6. Rigor | Deflated Sharpe, trial counts, controls | — | — |
| 7. Search | G10 | Evolutionary composites | A larger box, if needed |

---

## 6. Practical factors

- **Pattern day trader rule.** In a US margin account, frequent day trading has
  historically needed $25,000 of equity, and FINRA has been revising the rule (check the
  current version). An intraday strategy may not be runnable with real money on a small
  account; daily and weekly strategies avoid it.
- **Taxes.** High turnover makes every gain short-term (taxed as ordinary income); only
  holdings over a year get the long-term rate.
- **Shorting.** Long-short cross-sectional strategies need a margin account and
  borrowable shares; long-only is simpler and captures part of the effect.
- **Live validation is slower at low frequency.** A weekly strategy makes about 50
  decisions a year, so months of paper trading say less; longer backtest history (lever
  2) makes up for it.
- **Attention is the scarcest resource.** Levers 1, 2, 4 and 6 are free and compound;
  paid data pays off after them.

## 7. How to read edge

### The holdout

A **holdout** is a slice of history locked away before research starts and never used to
choose or tune anything; it is read **once, at the end**, to check the finished strategy
on data it has never seen. The research layout splits its 24 months into selection (9
months, strategies chosen), evaluation (9 months, survivors tested) and holdout (6 months,
read by no stage). Anything used to make a decision becomes training data, so the holdout
is the only part where luck could not help. **It can be looked at only once:** tweaking a
strategy after a bad holdout result and checking again turns it into training data. That
is why the validator checks that no stage reads it, and why its date moves on a schedule
(each new period is fresh unseen data). The baselines show it working: a median Sharpe of
2.03 on evaluation, -1.60 on the holdout.

### Four questions

Sharpe and drawdown summarise a result; edge is seen with four questions.

| # | Question | Measures |
|---|---|---|
| a | Does it beat simply holding the same stocks? | Excess return over buy-and-hold; information ratio; alpha and beta (return = alpha + beta x market: beta is market exposure, alpha is the edge) |
| b | Is it real, or could luck explain it? | t-stat about Sharpe x sqrt(years) (about 2 is the usual bar); deflated Sharpe |
| c | Did it earn steadily, or in one lucky stretch? | The cumulative excess-return curve (a steady slope is edge; one jump then flat is luck); rolling 6- or 12-month IR; hit rate and payoff |
| d | Are its predictions right? (ranking strategies) | Information coefficient (IC): each period, the correlation between the signal's ranking and the next period's returns; an average of 0.02 to 0.05 that stays positive is a real edge at breadth. IC decay over 1, 5, 20 days gives the holding period |

Live, add the **cone chart** (live cumulative return inside the backtest's predicted
range) and net against gross. Drawdown is a survivability check (depth, length, recovery),
not a test of edge. The one view to keep: the cumulative excess-return curve with a
rolling IR under it.

### History and live time

Most tuning is done on history, limited by compute and attention, not the calendar: with
ten years of daily bars a run takes minutes to hours. Three things history cannot do:

1. **It can be unseen only once.** Every look uses some of it up; the only new unseen data
   is the future, one day per day (hence the moving holdout, and several holdout periods
   from longer history).
2. **A backtest models trading; it is not trading.** Real fills, costs, the live feed and
   code differences show only live. This is what the 8 to 12 weeks of paper are for: to
   check live matches the backtest (the decay ratio), not to prove profit.
3. **The future can differ from the past** (regimes, decay): handled by monitoring, not by
   waiting longer to start.

**Why 24 months of 5-minute bars is still little evidence.** How precisely an average
return can be measured depends on the window's length, not on how many samples are in it:
checking a slightly biased running total every 5 minutes instead of daily does not show
the bias sooner. The exception is a strategy making many independent bets with a real
per-bet edge (market makers validate in weeks). The retail route to that is **breadth, not
speed**: many names ranked at once give many semi-independent bets a week, and the IC
gives one evidence point per rebalance across all of them. High-frequency trading itself
(co-location, direct feeds, microseconds) is out of reach for a retail broker API.

| Phase | Uses | Calendar time | Settles |
|---|---|---|---|
| Research tuning | History | Days to weeks per family | A backtested edge that survives the holdout and deflated Sharpe |
| Paper validation | Live, fake money | 8 to 12 weeks | Live matches the backtest |
| Small real money | Live, small size | A few months | Real fills match paper |
| Confidence in the edge | The live record | Years, never finished | The edge is still there (the decay ratio); allocation sized to the evidence |

**When lever 2 comes.** Not in the current work (24 months of 5-minute IEX bars). A
5-minute strategy cannot be tested on daily bars, so lever 2 is useful only with lever 1:
Phase A step 3, after the research plan's run, G10 and task 5, Step 2 and the first
monthly runs, if the G10 decision point is reached. Loading ten years of free daily bars is
small and could be done earlier, in parallel; decided after the 2026-11-25 run.

### Why an edge would exist

An engine can test a reason; it cannot supply one. Edges come from **risk** (paid for
bearing what others avoid), **behaviour** (under-reaction, panic) or **structure** (forced
flows such as index rebalancing). Each strategy family should carry a written hypothesis
and the predictions it makes (for example: if momentum is under-reaction, it is stronger
in stocks with less analyst coverage); the engine tests the predictions.

**What counts as true.** Two kinds, which behave differently:

- **Data truth** ("AAPL closed at $187.42 that day") is a fact that can be verified: two
  independent sources agree or they do not. The checks under T2 settle it.
- **Edge truth** ("strong 6-month winners tend to beat the rest next month") is a claim
  about the future and can never be verified directly, only supported with growing
  confidence or knocked down. A pattern is treated as real when it **survives honest
  attempts to kill it**: it holds on unseen data (the holdout, then live), stays
  significant after correcting for how many ideas were tried, is consistent across time,
  regimes and names, and has a believable reason to exist. Questions a to d measure
  those tests. Nothing reaches "proven"; a strategy reaches "survived enough to bet on",
  sized to how much it has survived (falsification, after Popper).

**The ceiling is the data, not the engine.** Every analysis of the same history is another
look at it; past a point more analysis finds more false patterns. Good tooling asks better
questions, counts them and corrects for them.

### Data: correctness and quantity

Two different things, often mixed together.

**Correctness (is a price right?)** can be improved without paying:
- **Cross-check against a second free source**: daily closes compared with another free
  daily source, disagreements above a small threshold flagged. Two independent sources
  rarely make the same mistake.
- **Reconcile prices with corporate actions**: every large jump has a split or dividend
  behind it, or is flagged (how NFLX's 10-for-1 split, read as -90%, was found).
- **Coverage rules**: names with too many missing bars are left out (the 0.70 / 0.55
  lines).
- **Point in time**: only what was known on the date (the universe timeline).
- **Survivorship**: include names that later disappeared where possible; free sources are
  weak here, so the limit is recorded.
- **Versioning**: every result names the exact data it came from and can be rerun.

T2 automates most of this list.

**Quantity (how much independent evidence exists)** is the real ceiling and is not a
quality problem: perfect data for ten years still holds ten years of market history. More
independent evidence comes from **longer history** (daily bars as far back as free sources
go), **breadth** (more names and asset kinds, as long as their moves are not copies of each
other) and **time** (each new week is fresh unseen data, which is why the holdout moves).
Paid data mostly buys convenience and survivorship coverage, not a hidden truth.

## 8. Research tooling (T1 to T4)

Standard reports that run on their own and explain their flags in plain language, so the
calculation is automated and reading the reports teaches the concepts. Notebooks stay for
open questions.

| Item | What it looks at | When it runs | What it gives | Build when |
|---|---|---|---|---|
| T1, run tear sheet | Every research run's survivors and their trades | End of every pipeline run, report-only (like the gate report) | Trades by time of day, symbol, holding period, regime; MAE and MFE; gross against net; concentration (top name, top week); alpha and beta; excess-return curve and rolling IR; correlation across candidates. **Flags as sentences**: "62% of the loss comes from TSLA", "positive before costs, negative after", "beta 0.9: mostly market exposure", "all gains in one two-week stretch" | **Next, after the 2026-11-25 run**: cheapest, reuses what a run already produces |
| T2, data-quality report | The market data itself (the bars A publishes and B pulls), not trades | Weekly on B, after the pull | Coverage heatmap (name x day); large returns with no corporate action (would have caught NFLX's split as a -90% day); volume anomalies; new feed holes. Flags become scorecard rows or alerts | Alongside T1 or T3 |
| T3, signal screen | A fixed, versioned library of candidate signals (a number per stock per day, such as 6-month return or last week's fall) | Monthly on B, before the research run, on selection and evaluation data only | Per signal: do high-scoring stocks beat low-scoring ones next period (quintile spread), IC by horizon, decay, implied turnover, by regime. A ranked table; passing signals become strategy families for the pipeline | Before lever 1 (Phase A step 3) |
| T4, live monitor | Three views of each live strategy over the same weeks: live (A's paper fills), re-sim (B's Saturday bench re-sim), research expectation (its holdout) | Weekly | Live below re-sim: execution (fills, costs, bugs, data). Re-sim below research: edge decay. Cone chart, decay ratio, IC drift, live costs against the model. Feeds pre-launch 7.1b's Live tier | With M3 |

**T3's guard.** An automated screen is a data-mining machine by design: screen 200
signals and about 10 look significant by luck. The library is declared in advance and
versioned (no adding signals until one works); a correction for testing many at once
(Benjamini-Hochberg, which controls the share of false discoveries, or the deflated
Sharpe); never the holdout; the screen only filters, the pipeline and holdout confirm.

**T3, worked through.** The analogy: before opening an umbrella stand on cloudy
mornings, check ten years of weather records for whether cloudy mornings were followed by
rain more often than clear ones. T3 checks a clue (a signal) against an outcome (next
week's return) before anyone builds a strategy on the clue. Steps, for the signal
"6-month return":

1. Take one past Monday. From the bars, compute each stock's signal (close that day ÷
   close 126 sessions earlier − 1).
2. Split the stocks by it: top fifth and bottom fifth (top and bottom half in a toy
   example).
3. From the bars, take each stock's return over the following week; average each group.
   One example week: top +0.6%, bottom −0.07%, difference +0.67%.
4. Repeat for every Monday in the history (about 500 over ten years): the average
   difference, how often it is positive, whether it holds in most years.
5. Repeat for every signal in the library (1-month return, last week's return,
   volatility, volume against usual, ...) and rank them.

No orders, fills, cash or positions, only arithmetic on closes, so 30 signals over ten
years take minutes where the pipeline takes hours for one family. Passing signals become
strategy families ("each week hold the top-ranked names"), which then go through the
pipeline for what T3 ignores: costs, fills, turnover, the holdout.

**T4, worked through.** For the same weeks and strategy: live (A's paper fills) −1.2%,
re-sim (B's Saturday simulation of those weeks) −0.4%, expected (the holdout's average
weekly return scaled to the period) +0.3%. Live against re-sim, same strategy and same
market: the 0.8% gap is **execution** (fills, costs, a bug, missing data on A), a plumbing
problem. Re-sim against expected: the simulator traded it right and the market did not
behave as research said: **edge decay or overfitting**, a research problem. Telling the two
apart decides what gets fixed.

**T3 in detail.**

*Indicator, not signal.* In the code a **signal** is the decision: `SignalDirection` is
`BUY`, `SELL` or `FLAT`. T3 tests the **indicator**, the number the decision is made from:

```
bars ──► indicator ──► rule (strategy settings) ──► signal (BUY / SELL / FLAT) ──► order
         T3 tests this                               the pipeline tests the whole chain
```

Each strategy computes its indicator itself from its recent bars on every bar, with the
functions in `strategy/indicators/` (13 today: `distance_from_moving_average`, `z_score`,
`momentum`, `rate_of_change`, `rsi`, `rsi_wilder`, `simple_moving_average`,
`exponential_moving_average`, `rolling_standard_deviation`, `realized_volatility`,
`average_volume`, `volume_ratio`, `volume_spike`). The `feature_engineering/` pipeline
(`ret_1d`, `ret_20d`, `volatility` and so on) is separate and appears to feed the regime
labels; no strategy and not the simulator read it. Mean reversion, for example, computes
`z = (last close − 20-bar average) / 20-bar standard deviation` on every bar
(`mean_reversion_strategy.py`): with an average of $100.00, a typical wiggle of $1.50 and
a last close of $97.00, z = −2.0, and with `buy_below_z` at −2.0 the signal is `BUY`. The
z-score measures distance in wiggles rather than percent, so one threshold fits calm and
jumpy stocks. T3 calls **the same functions**, so it tests exactly the number that is
traded.

*Three shapes of test* (sort by the clue, see whether the outcome differs):

| Shape | Compares | Question | Example |
|---|---|---|---|
| Cross-sectional | Stocks against each other on one date | Do high scorers beat low scorers? | 6-month winners against losers, next week |
| Time-series | One stock against itself on different days | After the indicator is high, is the next return better? | After z ≤ −2, the next hour |
| Event | After an event against normal times | Does the stock behave differently after X? | After a 5% gap down, the next 3 days |

*What T3 is tied to.* **Universe:** the names in force on each date (the universe
timeline), so no name is used before it was on the list (including a stock picked later
because it had done well is look-ahead bias); more names give steadier evidence (6 stocks
in a fifth of 31 against 100 in a fifth of 500). **Trading:** none; each observation is
"the indicator said X, then the price did Y", where the pipeline would record orders,
fills, slippage and cash. **Strategies:** none in particular; it tests the ingredient many
strategies share. A family is an ingredient plus trading rules (mean reversion: "fell far
below its average, so it bounces"; momentum: "rose, so it keeps rising"; MA crossover:
"crossed, so the trend continues"). If the ingredient's average move is below a round
trip's cost, every variant of the family fails, however its rules are tuned.

*Timing.* T3 runs monthly, before the research run, on data that already exists, and does
not read the pipeline's results (T1 does). Inside the history every observation keeps time
order: the indicator uses bars up to the moment, the outcome bars after it, **measured from
the next bar**, because a decision made on a bar's close can only trade on the following
bar.

*The comparison.* Each firing is one row: time, stock, indicator value, next-period
return minus the market's over the same period. All rows, across all stocks and the whole
window, are pooled into one average (one row per 5-minute bar where the condition held for
intraday indicators, at most one per stock per day for daily ones):

```
baseline (all bars, the clue ignored)        +0.01%
conditional (bars where z ≤ −2)              +0.08%
edge                                          +7 bp
```

The **null hypothesis** is that the indicator makes no difference (conditional equals
baseline); the stated prediction must earn its way past it. **Shuffle (permutation) test:**
shuffle the indicator values so each is paired with the wrong future return, recompute the
edge, repeat 1,000 times; the spread of those fake edges is what luck produces (say −3 to
+4 bp), and the real edge must sit outside it. Two readings: the average move, and the hit
rate (53% of firings up against 50% of all bars is small and usable over 10,000 firings).
Both matter, because a good hit rate with small wins and large losses can still lose:
**expectancy** (win rate × average win − loss rate × average loss) decides.

| Question | Compared against | Example | Verdict |
|---|---|---|---|
| 1. Does it predict? | The baseline, and the shuffle range | Edge 7 bp; shuffles −3 to +4 | Outside the luck range |
| 2. Larger than cost? | A round trip (2 bp at 1 bp a trade) | 7 bp against 2 | 5 bp left |
| 3. How long does it last? | The edge at each horizon against the shuffle range | 10 min 3 bp, 1 h 7 bp, 1 day 1 bp | Hold about an hour |
| 4. Consistent? | Slices with edge > 0 (years, regimes, stocks) against 50% | 9 of 10 years, 25 of 31 stocks | Far above chance |

For cross-sectional indicators the baseline is the average of all stocks that period.

*The library of predictions.* Each entry is an indicator **plus a stated prediction**
(direction, horizon, the size it must beat), written before testing, versioned:

| Entry | Indicator | Prediction | Horizon | Must beat |
|---|---|---|---|---|
| MR1 | `z_score(20)` ≤ −2 | rises (reversion) | 1 hour | 2 bp |
| MR2 | `z_score(20)` ≥ +2 | falls (reversion) | 1 hour | 2 bp |
| MOM1 | 12-1 month momentum, top fifth | beats the rest | 1 month | cost |
| REV1 | Last week's return, bottom fifth | rises relative to the rest | 1 week | cost |
| VOL1 | 60-day volatility, bottom fifth | better risk-adjusted return | 1 month | cost |

Each month T3 re-tests **the same rows on more data**; the prediction stays, the evidence
grows. Several predictions per indicator are fine if each is declared first; every row
counts toward the multiple-testing total. A row is never edited after a result (moving
MR1 to −1.5 because −2 failed is tuning to the data): a variation is a **new row** in a new
version. A result in the opposite direction is not a reversed success: it is a new
hypothesis, tested on fresh data. The direction is declared, which also earns the more
sensitive one-sided test.

*Which indicators.* First the 13 that exist: screening them explains why the current
families fail, which more pipeline runs cannot. Then, for lever 1 only, a short list
declared in advance, each with its written reason: 12-1 month momentum (12 months,
skipping the last), 1-week reversal, 60-day volatility (low-volatility effect), distance
from the 52-week high.

*Bulk computation.* The indicator functions return only the latest value (right for a
strategy deciding bar by bar). T3 needs every value in history: calling the function per
bar is exact but slow (fine for 31 names); for 500 names or ten years, a vectorised
version computed as a whole column with pandas, plus a **parity test** that it matches the
per-bar function (streaming against batch).

*What T3 drives.*

```
library ──► T3 monthly ──► per row: PASS · FAIL · FADING
   FAIL    ──► the family it feeds is switched off (or down) in the generator
   PASS    ──► the family stays on; T3's horizon, threshold, stocks and regimes narrow
               its search (fewer configs, fewer lucky survivors)
   FADING  ──► live strategies built on it are flagged, before their P&L can show it
   new idea ─► a new row in the next library version, tested the next month
```

Report-only first (T3 recommends, the user decides); automatic family weights only after
its calls have proved right for a few months. Needed before it can act: a **map from each
strategy family to the library rows it relies on** (mean reversion: MR1, MR2), which does
not exist yet.

**Notebooks, when used.** A `notebooks/` folder outside the package, analysis-only
dependencies (jupyter, plotting, statsmodels, alphalens-reloaded, quantstats or
pyfolio-reloaded), pinned dataset versions, never the holdout, and every idea tried counted
toward the multiple-testing total. A check that proves useful graduates into tested code.

## 9. Lifecycles and the ceiling

### Three lifecycles, nested

| Level | What moves | States | Driven by | Cadence |
|---|---|---|---|---|
| Strategy (exists: the bench) | One config | Admitted → active → demoted → retired | The weekly review, T4 | Weekly |
| Family (new) | An ingredient and the strategies built on it | Candidate → generating → dormant → revived, or retired | T3's pass, fail or fading | Monthly |
| Portfolio (new) | The whole set | Room to grow → at the ceiling → open a new dimension | The novelty rate and marginal gain (below) | Quarterly |

**Dormant, never deleted.** A family whose ingredient fails stops generating, and T3 keeps
re-testing it monthly (cheap). If the ingredient returns on fresh data the family is
revived; it is retired only when its reason is gone (for example, a market-structure change
that ends the effect).

### Families come back; the best does not stay

Edges are often cyclical: momentum works in trends and crashes at turning points (2009),
then recovers; value had roughly a lost decade from about 2010 to 2020 and returned in
2022; mean reversion tends to do better in choppy markets. Edges also return when crowding
unwinds. And edges decay: crowding, publication, market structure (decimal prices, HFT,
zero commission), regime shifts. Intraday edges often last months; slower effects years,
weaker than when found. The champion-and-challenger loop never stops; today's challengers
replace today's champions when they fade.

### The ceiling

For N strategies of Sharpe s with average correlation ρ, combined Sharpe ≈
s × √(N / (1 + (N − 1)ρ)). As N grows it levels off at **s / √ρ**:

| Each strategy's Sharpe | Average ρ | Ceiling, however many are added |
|---|---|---|
| 0.3 | 0.5 | about 0.42 |
| 0.3 | 0.3 | about 0.55 |
| 0.3 | 0.1 | about 0.95 |

**Correlation is the ceiling.** Two measurable signs it is near: the **novelty rate falls**
(more candidates rejected at admission as not novel) and **marginal contribution goes to
zero** (new passing candidates barely improve the portfolio's Sharpe). Breaking through
means lowering ρ with something structurally different: a new horizon, a new style
(cross-sectional next to time-series), new inputs (when paid data becomes worth it), new
assets (section 10). Reaching the ceiling means this dimension is used up, not that
nothing new exists.

### Explore and exploit

At the ceiling the risk is stagnation: the portfolio fills with ageing edges that may decay
together. A fixed share of research effort (20 to 30%, *to set*) always goes to
**exploration** (new families, new dimensions) while the rest **exploits** (refines what
works), so a supply of challengers exists when champions fade (the multi-armed bandit
trade-off).

```
PORTFOLIO (quarterly): novelty rate falling, marginal gain near 0 ──► open a new dimension
     ▲
FAMILY (monthly, T3):   generating ⇄ dormant ──► revived (retired only if its reason is gone)
     ▲
STRATEGY (weekly, bench): admitted → active → demoted → retired
```

## 10. Other asset classes and time slices (discussed 2026-11-25, nothing decided)

Diversify across **return drivers**, not tickers: the equity premium, the term premium
(bonds), carry (currencies), the volatility premium (options), trend (all of them).
Broker support below is as understood on 2026-11-25; check current offerings.

| Kind | What it is | Hours | Leverage | Costs | Data | Alpaca | Work for this stack | Adds | Main risk |
|---|---|---|---|---|---|---|---|---|---|
| Overnight holding | A time slice of the same stocks: close to next open | Gap while closed | None needed | Same | Same bars | Yes | Low: the daily strategies of lever 1 hold overnight by nature | A documented effect (much of US index return has come close to open); a new horizon | Gaps that cannot be reacted to |
| Multi-asset ETFs | Bonds, gold, commodities, sectors, international markets through ETFs | Market hours | None | Low | Same feed | Yes (A already trades SPY, QQQ, IWM, DIA, TLT) | Lowest: same broker, data and code | Different drivers (term premium, commodities); cross-asset trend through ETFs | Lower ρ only while drivers stay apart |
| Options | Contracts on a stock's price; nonlinear payoff, expiry, time decay, Greeks | Market hours | Built in | Wide spreads, many contracts | Chains are large and often paid | Yes | High: pricing, Greeks, expiry, assignment, a 100-share multiplier, in the simulator and ledger | Hedging (buying puts on the portfolio); the volatility premium (selling options) | Selling options can lose many times the premium |
| Futures | Exchange contracts on indices, rates, commodities, currencies | Nearly 24 hours, 5 days | High (margin) | Very low | Continuous contracts need roll adjustment | No | High: new broker, rolls, margin, contract sizes (micro contracts help) | The classic home of trend following across asset classes | Margin calls, leverage |
| Forex | Currency pairs | 24 hours, 5 days | High | Low on majors, wide at retail | Free to cheap | No | Medium to high: new broker, pairs and carry accounting | Carry, macro trend; low correlation to stocks | Leverage, retail spreads |
| Crypto | Digital assets on many venues | 24 hours, 7 days | Optional | Higher fees | Fragmented, quality varies | Yes | High in a hidden place: the NYSE calendar, sessions and EOD chain assume a market that closes | Strong but regime-bound trends | Extreme volatility; correlation with stocks has risen |

**Suggested order (discussion, not decided):** stabilise the current system; then daily and
overnight equities (lever 1, same everything); then multi-asset ETFs (most of the
diversification of futures at retail, with no new broker); options for hedging only,
later; crypto only with a calendar rework; futures and forex only at a business scale
with a second broker.

## 11. Open decisions

- [ ] The thresholds marked *to set*: information-ratio floor, decay-ratio floor, M4's
      correlation level (after the first run with G9's numbers).
- [ ] How many runs before the G10 decision point (with G10).
- [ ] Whether the SIP switch waits on the horizon question (lever 5).
- [ ] How much of the stack assumes 5-minute bars (before Phase A step 3).
- [ ] T1's scope and output (a result-file section or a separate report file) before it is built.
- [ ] Whether each strategy family must carry a written hypothesis (section 7) before it
      enters the pipeline.
- [ ] The map from strategy families to T3's library rows (section 8), before T3 can act.
- [ ] How long T3 stays report-only before it sets family weights.
- [ ] The exploration share of research effort (section 9).
- [ ] Whether to load ten years of free daily bars early, in parallel (section 7).
- [ ] The order of new asset classes, if any (section 10).

## 12. Log

| Date | What happened |
|---|---|
| 2026-11-25 | Doc started with the user: definition of working, milestones, Phase A and B, the seven levers; later the same day, how to read edge (holdout, four questions, why an edge exists) and research tooling T1 to T4; then what counts as true, data correctness against quantity, and T3 and T4 worked through; then history and live time, T3 in detail (indicator against signal, the three shapes, timing, the baseline and shuffle test, the library of predictions, what T3 drives), lifecycles and the ceiling, and other asset classes (discussion only). The first run with G9's benchmark (research plan, Step 1b) was in progress on B |
