# Belief, payoff and one-step action selection

The decision layer turns a daily-maximum distribution into an explicit portfolio
payoff table, compares BUY/SELL/HOLD alternatives, and prepares separate decisions
for possible next METAR observations. This exposes the step between a probability
model and an implementable trading hypothesis.

Run the connected example:

```bash
python -B examples/decision_walkthrough.py
python -B -m unittest discover -s tests -p test_decisions.py -v
python -B -m unittest discover -s tests -p test_rules.py -v
```

The example feeds synthetic observations through the actual public
`KalmanWeatherModel`, obtains `P_now`, `Q_next` and `D_after`, then scores synthetic
books and positions. The chosen BUY is passed into the existing fake ARM/FIRE
example. A fake acknowledgement remains an acknowledgement, with no inferred fill.
The Kalman sampler uses 10,000 paths and seed 0 per sampling call; the book prices
are deliberately favorable hand-authored inputs, not observations of available
mispricing. Only the model example needs NumPy; the decision and rule modules use
the standard library.

## State and payoff

Let $Y$ be the settlement day's maximum **integer observation in the contract's
temperature unit**. A bucket $b$ is an inclusive integer interval, possibly open
on one side. Its YES and NO payoffs are

$$
h_b^{YES}(Y)=\mathbf{1}\{Y\in b\},\qquad
h_b^{NO}(Y)=1-h_b^{YES}(Y).
$$

For position $i$ with quantity $q_i$ and paid cost per share $c_i$,

$$
\Pi(Y)=C_{realized}+\sum_i q_i[h_{b_i}^{s_i}(Y)-c_i].
$$

`multi_position_utility` enumerates this common settlement variable:

$$
\mu_\Pi=\sum_y P_Y(y)\Pi(y),\qquad
v_\Pi=\sum_yP_Y(y)[\Pi(y)-\mu_\Pi]^2,\qquad
U=\mu_\Pi-\lambda v_\Pi.
$$

This includes covariance between positions without estimating a separate
covariance matrix. Summing individual position variances would be wrong: equal
YES and NO quantities on the same bucket have constant combined terminal payoff.
The regression test demonstrates this cancellation directly.

Prices and PnL are in dollars; quantity is shares; variance is dollars squared;
$\lambda$ therefore has inverse-dollar units. The source used this mean-variance
utility as a modeling choice. It is not an identified utility function or a
claim that its default penalty is empirically optimal.

## Candidate actions and accounting

The public state contains $P_Y$, confirmed maximum floor, current book snapshots,
open positions, a fee assumption and an optional cash bound. Candidate generation
preserves the source's best-level BUY/SELL/HOLD structure:

- BUY at the displayed ask, bounded by default lot size, remaining per-contract
  exposure allowance, displayed ask quantity and available cash.
- SELL held shares at the displayed bid, bounded by the displayed bid quantity.
- HOLD the existing portfolio.

These are hypothetical fills at an observed level. Neither queue position nor
price movement during submission is modeled. Displayed depth is not a promise
that those shares remain available.

With the illustrative proportional-notional fee rate $f$, BUY basis is
$a(1+f)$ and SELL proceeds are $b(1-f)$. FIFO lot reduction records realized
PnL and retains the original bucket bounds on the unsold shares. This simplified
fee convention comes from the source evaluator; it is not a current venue fee
schedule. Historical datasets and execution studies must supply their own
matching fee model.

For each action the comparator recomputes terminal utility after the hypothetical
transaction:

$$
\Delta U(a)=U(s\text{ after }a)-U(s\text{ after HOLD}),\qquad
a^*=\arg\max_{a\in\mathcal{A}(s)} U(s\text{ after }a).
$$

The default action is HOLD and exact utility ties retain it. A SELL can be useful
even when its realized PnL since entry is negative: entry cost is already incurred,
and selling can dominate continued exposure. The public comparator makes that
distinction explicit.

## Liquidity screen and reaction hypothesis

The source screens BUY alternatives using minimum bid/ask prices, maximum spread,
positive depth and a blend of modeled terminal value with an immediate exit:

$$
e_T=\alpha[p_b-a(1+f)]
 +(1-\alpha)[b(1-f)-a(1+f)],
$$

where $p_b=\mathbb E[h_b^s(Y)]$. This is a conservative scoring heuristic, not a
probability-weighted scenario tree unless a separate model establishes what
$\alpha$ represents. The source default was 0.6; the example declares its choice.

The optional reaction prior is preserved as a standalone function:

$$
\widehat p_s(\Delta)=p_s(0)e^{-\gamma\Delta}
                   +p_s^\star(1-e^{-\gamma\Delta}).
$$

Both prices refer to the same YES or NO contract; there is no second complement
inside the function. The source reaction module could fit a decay rate from
recorded event trajectories, but this package does not supply a validated fitted
reaction model. The public function has no invented empirical confidence band.

An event-price estimate can enter the BUY screen after selling fees; the larger
screened edge supplies a `gate_horizon` label. **The action ranking still uses
terminal portfolio utility**, as in the inspected historical engine. It does not
optimize a future sequence of exits. Moreover, this source-derived reaction
branch uses the same terminal fair as its target: when fair exceeds entry ask,
the exponential path stays between ask and fair, so its fee-adjusted event edge
cannot exceed the terminal edge. When fair is below ask, both edges are
nonpositive. It therefore cannot create a distinct positive event-horizon BUY
in this specification. A regression test makes that limitation explicit.
`exit_values` separately compares now,
resolution and an explicitly supplied event-contract price, including an optional
stop-loss override and the requirement for a usable current bid.

`candidate`, `review` and `hold` are offline assessment labels. The confidence
number on a BUY is the modeled probability of its payoff, not a statistical
confidence interval. No label causes an order to be sent.

## Conditional METAR playbook

The probability inputs have different meanings:

$$
P_Y(y)=P(Y=y\mid\mathcal F_t),\quad
Q(m)=P(M_{next}=m\mid\mathcal F_t),\quad
D_m(y)=P(Y=y\mid M_{next}=m,\mathcal F_t).
$$

`conditional_playbook` selects top-$Q$ branches, retains their original weights,
updates the confirmed floor to $\max(M_{floor},m)$ and scores each supplied $D_m$.
It preserves the current books across branches, matching the source's
counterfactual construction. Those books need to be rechecked at observation time;
the playbook does not predict their post-event availability. The source's doubled
next-event horizon is retained as a placeholder scenario convention, not a
reconstructed next-observation schedule.

No missing conditional distribution is silently replaced by truncating the
unconditional distribution. Truncation would encode an additional approximation,
not the requested conditional law. The example also reports

$$
R_{L1}=\sum_y\left|P_Y(y)-\sum_m Q(m)D_m(y)\right|.
$$

For a coherent joint law this is zero; the public model's separately constructed
approximations and Monte Carlo integration need not make it zero. The demo reports
the observed residual and selected-branch coverage. A nonzero residual is a model
diagnostic, not an exploitable trading edge. No strategy-performance conclusion
is made from these synthetic branches.

## Source mapping and publication changes

This is a source-derived extraction with compact public interfaces. It preserves
the inspected one-step calculation and selected strategy rules; it does not
reproduce an entire private application or establish that these modules were
autonomously deployed together.

| Source ID | Exact historical symbols | Public implementation |
|---|---|---|
| `W-DECISION-MDP` | `Position.payoff`, `Position.pnl`, `multi_position_utility`, `MDPDecisionEngine._enumerate_buy_candidates`, `_enumerate_sell_candidates`, `_apply_buy`, `_apply_sell`, `_action_ev_single`, `choose` | `Bucket`, `Position`, `multi_position_utility`, `apply_action`, `OneStepDecisionEngine` |
| `W-DECISION-REACTION` | `ReactionPrediction.mean_at`, `ReactionModel._exp_mean`, `predict_spike`, `record_event` | Exponential mean extracted; fitting and deployment integrations documented, not represented as validated public results. |
| `W-DECISION-EXIT` | `ExitEvaluator.evaluate` | `exit_values` with same-contract event prices and explicit costs. |
| `W-DECISION-PLAYBOOK` | `PlaybookComposer.compose` | `conditional_playbook`; explicit supplied conditional distributions and retained branch probabilities. |
| `W-DECISION-BRAIN` | `PreMetarQWeighted.evaluate`, `CoolingLock.evaluate`, `TailDeadArb.evaluate`, `Escalation.evaluate`, `PostMetarFlip.evaluate`, `CoolingOnset.evaluate` | Context and alternative scenario hypotheses in [strategies](strategies.md); not all narrator heuristics are ported. |
| `W-STRATEGY-RULES` | `BucketState.kill_th`, `condition_value`, `_apply_r1`, `_apply_r2`, `_apply_r3` | `rules.py`; see the separate rule explanation. |

Specific fixes are documented rather than silently attributed to historical code:

| Historical behavior found in source | Public correction and test |
|---|---|
| `position_ev_to_resolution` computed a general payoff, then returned an exact-bucket probability; some BUY/partial SELL constructors dropped `bucket_type`. | One inclusive-bound payoff function serves all probability, portfolio and lot operations. Range/tail and partial FIFO tests cover it. |
| NO event code could complement already side-specific fair/price inputs again. | Reaction and exit functions accept prices for the chosen contract explicitly; the NO regression test checks endpoints and event PnL. |
| A utility-improving SELL could receive final HOLD status when its PnL since entry failed the positive-edge gate. `ExitEvaluator` also required positive exit PnL outside stop-loss. | SELL and exit choices compare remaining economic alternatives, allowing a smaller realized loss when holding is worse. |
| Conditional fallback truncated $P_Y$; `M_floor or m` treated a zero floor as missing. | Require supplied $D_m$ and distinguish `None` from zero; test missing/impossible branches and a zero floor. |
| Crossed books, malformed probability mass and missing cash constraints were not consistently rejected. | Validate distributions, bounds and book inputs, and add the explicit cash cap to candidate sizing. |

Tests verify these calculations and boundaries. They do not validate profitability,
the reaction model, calibration quality or execution speed. The source class name
contains “MDP”; the implemented algorithm is greedy one-step lookahead. There is
no Bellman recursion, learned policy, dynamic-programming solution or optimality
proof in this extraction.
