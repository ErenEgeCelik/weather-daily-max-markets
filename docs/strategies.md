# Weather strategy research: from information to a decision

The weather program investigated two connected questions: how new observations
change the distribution of the day's maximum, and how those changes interact
with prices, liquidity and existing positions. Its implementations include
deterministic maximum transitions, model-dependent event scenarios and a
portfolio-aware one-step decision layer.

The public [decision walkthrough](../examples/decision_walkthrough.py) connects
the actual Kalman implementation to action scores and fake execution preparation.
It complements the observation collection and ARM/FIRE engineering examples;
the probability model and the execution path solve different parts of the problem.

## 1. Observation-confirmed maximum transitions

The confirmed daily maximum is monotone. For a bounded bucket $[L,U]$, an
eligible settlement-source observation above $U$ makes its YES payoff impossible.
For the open upper bucket $[L,\infty)$, an observation at or above $L$ locks its
YES payoff under that settlement definition. Reaching an exact bucket does
**not** lock it: the maximum can rise again later.

The inspected sidecar implements these as persistent event conditions:

| Rule | Prepared condition | Contract direction | Information used |
|---|---|---|---|
| R1 | A new integer observation reaches bounded bucket upper edge + 1. | NO on the exceeded middle bucket. | Monotonic daily maximum. |
| R2 | A new integer observation reaches the lower edge of the open upper bucket. | YES on that upper bucket. | Monotonic daily maximum. |
| R3 | A new observation enters a candidate middle bucket, or all specified lower NO prices meet a cascade threshold. | YES on the candidate middle bucket. | Current book shape plus an observation or market-price condition. |

`rules.known_yes_payoff` exposes the logical transition without requiring a
trading rule. Its premise is a confirmed observation eligible for that contract's
settlement convention, date and units. Source selection, rounding and publication
timing must be handled before invoking it.

R1 and R2 preparation is deliberately sticky in the inspected source: temporary
absence of an attractive book does not remove the observation condition. That
does not guarantee an executable price when it triggers. The public R1 mirrors
the source's closed-middle-bucket scope; the logical impossibility helper also
handles the lower tail.

## 2. R3: nearby bucket transition with book-shape filters

R3 adds a model of which middle bucket might receive repricing flow as the
confirmed maximum rises. The source filters on:

- Candidate YES ask within an entry band, spread and available depth.
- Candidate lower edge above the current maximum and at most two integer units
  above it in the inspected default configuration.
- Sufficient sum of lower-bucket YES asks and a low YES ask in the next bucket.
- A separate trigger-time price cap.

The lower-bucket ask sum is a **book heuristic**, not normalized probability mass.
Likewise, a large NO bid can be a market signal without proving the corresponding
bucket impossible. Logical certainty comes from the eligible observation, not
from a price threshold.

The source evolved from watching a single lower NO bucket to an AND condition
over intervening lower buckets. This addresses the case where a distant bucket's
NO price was already high before any relevant maximum transition. The public
`cascade_matches` exposes the all-buckets condition, with a regression test that
one high NO quote cannot satisfy the entire cascade.

The inspected defaults include an R3 entry band of $[0.005,0.65)$, lower-ask sum
at least 0.30, next-bucket ask at most 0.15, spread at most 0.25, cascade threshold
0.85 and trigger cap 0.85. These reproduce a historical source configuration;
they are not recommendations or independently validated optimal values.

The public interface uses best-ask notional for its small depth fixture; the
source summed the first five ask levels. It uses actual inclusive interval bounds
for range observations instead of the historical midpoint/tolerance shortcut,
and disables the spike branch when an intermediate contract is missing. It does
not implement the separate deployed spike worker's freshness, delta, re-arm or
ladder mechanics. Static threshold matching alone is not a complete strategy.

## 3. Model-driven decisions and conditional playbooks

The probability research supplies three objects: current daily-maximum law
$P_Y$, next-observation law $Q$, and conditional laws $D_m$. They support both
current inventory decisions and precomputed “if the next METAR is $m$” scenarios.

The [decision implementation](decision-implementation.md) shows the exact path:

1. Convert bucket definitions into state-contingent YES/NO payoffs.
2. Enumerate BUY/SELL/HOLD within liquidity, exposure and cash constraints.
3. Compute expected terminal PnL and variance across the **combined** portfolio.
4. Screen buys using modeled value and an immediate bid exit; compare resulting
   portfolio utility with HOLD.
5. Repeat under selected conditional METAR distributions and record the original
   branch probabilities.

The inspected reaction and exit modules add an exponential approach-to-target
price hypothesis and a comparison of selling now, holding to settlement, or
selling after an event. The public implementation exposes that equation and its
role without presenting a fitted, validated reaction model. The one-step engine
does not solve a multi-period policy. Its same-target exponential BUY screen
cannot generate a positive event-horizon edge larger than the terminal screen;
this limitation is preserved and tested, rather than described as an effective
independent horizon optimizer.

## 4. Other scenario hypotheses in the research application

The research application's `brain` layer organized model and book readings into
human-readable scenario cards. These are useful evidence of how the research
questions were organized, but their existence is not evidence of a profitable
autonomous strategy.

| Historical scenario symbols | Question examined |
|---|---|
| `CoolingLock`, `CoolingOnset` | Does the cooling regime lower the probability of higher maximum buckets enough to change a NO valuation? |
| `TailDeadArb` | Does modeled tail probability disagree with the price enough to motivate a hold-to-resolution candidate? |
| `Escalation`, `PostMetarFlip` | How do an upward maximum transition and continuing warming alter the next bucket's value? |
| `PreMetarQWeighted` | Which likely next observation would materially increase a bucket's conditional probability? |
| `ForecastRevision`, `MidCycleSensor`, `SweepCorroborate` | How should forecast changes, intermediate sensors and book moves enter the research narrative? |

“Cooling lock” and “dead tail” were names in source code. Cooling is a model
condition; a high model probability does not make a higher maximum physically
impossible. The public logical-transition helper does not treat it as certainty.

The source `PreMetarQWeighted` card used a two-branch heuristic:

$$
e_{proxy}=q_\star[D_{m_\star}(b)-a]
 +(1-q_\star)[P_Y(b)-a].
$$

The second branch reuses the unconditional law rather than a separately derived
law conditional on the complementary event. This is an inspection heuristic,
not the law of total probability or a verified edge. The public playbook instead
exposes the supplied conditional branches individually and reports the
$P_Y$ versus $\sum_m Q(m)D_m$ residual. This makes inconsistencies inspectable.

## Version and evidence boundaries

The R1/R2/R3 rules came from the inspected sidecar application. The Kalman,
one-step evaluator, reaction, exit and playbook modules came from a separate
research application. They are linked here as components of the author's work;
this does not establish that every module was deployed as one integrated live
strategy. Historical operational comments and thresholds retain that status.

The public decision code fixes identified accounting, side-convention and
conditional-input problems, listed with tests in
[decision-implementation.md](decision-implementation.md). The rule extraction
has its own narrow publication fixes described above. The original private
systems have not been changed by this extraction.

This section demonstrates research design and engineering implementation. It
does not publish a new backtest or infer realized PnL from synthetic prices.
Strategy economics need a separately matched observation/book history, causally
available inputs, realistic fills and costs, and an evaluation protocol. The
repository's evidence records distinguish those empirical studies from these
executable examples.
