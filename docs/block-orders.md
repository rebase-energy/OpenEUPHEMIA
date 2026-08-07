# Block orders: letting the solver decide

A block order is all-or-nothing across several hours — accept the whole
thing or none of it. That makes the clearing problem non-convex, and
non-convexity is the hard part of EUPHEMIA: a linear program can't
represent it, and once you add integer variables the dual prices that
define the market outcome stop being well-defined.

This note records what happens when OpenEUPHEMIA decides the block
accept/reject itself, rather than replaying what GME published.

## Two modes

| | Blocks | Script |
|---|---|---|
| **Replay** | published accept/reject taken as given; accepted blocks folded into the bid curves as price-taking volume | `scripts/replicate_italy_prices.py` |
| **Blind** | every block handed to the MILP as a binary; the solver chooses | `scripts/replicate_italy_blocks.py` |

Replay keeps the clearing a pure LP and is what the headline Italy case
uses. Blind is the stronger claim — nothing about the outcome is fed in
— and is what this note is about.

Blind mode follows EUPHEMIA's own two-stage decomposition: a MILP with
one binary per block picks the accept/reject set, then the block
decisions are fixed and the problem is re-solved as an LP so that zonal
prices come from well-defined balance-constraint duals.

## Result — April 2025

Reproduce with `python scripts/replicate_italy_blocks.py` (~4 s/day):

| | Blind result |
|---|---|
| Zonal prices | **5,040 / 5,040 exact** — MAE 0.0000, max 0.0000 EUR/MWh |
| Block decisions | **1,089 / 1,116** (97.6%) |

Broken down by GME's own published status, the split is exact:

| Published status | Blocks | Reproduced |
|---|---|---|
| `ACC` — accepted | 503 | **503 (100%)** |
| `REJ` — rejected, out-of-the-money | 586 | **586 (100%)** |
| `PREJ` — *paradoxically* rejected | 27 | **0 (0%)** |

The set the solver gets wrong is *exactly* the paradoxically-rejected
set — not one ordinary decision wrong, not one paradoxical one right.

**Prices are exact even though 27 block decisions are not.** Those 27 are
all in-the-money supply blocks, and the extra volume they add leaves
Italy entirely over the price-taking border rather than displacing a
domestic marginal unit. Measured on 2025-04-17 (the largest case): of
9,469 MWh extra accepted volume, 9,469 MWh — 100.0% — left as border
export, and prices moved by 0.000000000 EUR/MWh.

## Why the paradoxical rejections are missed

A paradoxically rejected block is one that is *in the money at the
clearing price and still rejected*. EUPHEMIA has no rule that produces
these directly; they fall out of a constraint it does enforce:

> a regular or profile block order out-the-money cannot be accepted

That is the **no-PAB** rule (no paradoxically *accepted* blocks). The
reverse is permitted: rejecting an in-the-money block is allowed, and is
the unavoidable price of non-convexity. GME's published April 2025
solution obeys this exactly — zero PABs, and 27 PRBs that are all
in-the-money at the published prices.

A block becomes paradoxically rejected when accepting it would push the
price down below its own limit, turning it into a forbidden PAB. That
mechanism needs prices to *respond* to the block being accepted — and
under a price-taking boundary they cannot, because the marginal unit
never changes. So no block ever becomes a PAB, the constraint never
binds, and the solver accepts every in-the-money block on offer.

This was tested rather than assumed. Re-running with the border pinned
at published exchanges (so extra volume must be absorbed domestically
and prices must move) makes the constraint bite: on a five-day sample,
block accuracy rises from 171/183 to 179/183, and all four remaining
misses are flagged as out-of-the-money — exactly the blocks a
price-consistency cut would remove.

## Why it isn't fixed here

Enforcing no-PAB requires a cutting-plane loop: solve, check whether any
accepted block is out-of-the-money at the resulting prices, cut that
combination, re-solve. Implementing it needs two things at once —
**accurate prices** to judge in/out-of-the-money, and **price feedback**
so there is something to judge. The two available boundary conditions
each supply only one:

| Boundary | Prices | Price feedback |
|---|---|---|
| Price-taking (`prices`) | exact | none — the loop never fires |
| Fixed exchange (`exchanges`) | MAE ≈ 0.16 | present — but the error manufactures false violations |

On the same five-day sample the fixed-exchange run flags 12 blocks as
out-of-the-money. Four are genuine. The other eight are blocks GME
correctly accepted, flagged only because our own prices are off — a
naive loop would wrongly reject them. Adding the loop today trades four
correct rejections for up to eight incorrect ones.

The missing ingredient is not block logic but the border: a neighbour
that is neither infinitely elastic (price-taking) nor rigid (fixed
flow), but has a real downward-sloping curve. Then extra exports depress
the neighbour's price, the block genuinely falls out of the money, and
no-PAB bites on prices that are still correct. That is the full-SDAC
coupling step on the roadmap — Italy modelled in isolation structurally
cannot see it.

## Inputs

Blind mode uses two committed tables that replay mode does not (see
[`../data/italy/README.md`](../data/italy/README.md)):

- `simple-bid-curves.csv.gz` — aggregated curves with blocks **excluded**
- `block-orders.csv.gz` — one row per block leg, plus the published
  status used only for scoring, never as solver input
