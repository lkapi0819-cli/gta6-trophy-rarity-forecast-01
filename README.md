# gta6-trophy-rarity-forecast-01

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/lkapi0819-cli/gta6-trophy-rarity-forecast-01/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/lkapi0819-cli/gta6-trophy-rarity-forecast-01/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Week-One Rarity — a forecaster for how many GTA VI players will have earned each PlayStation 5 trophy one week after launch, scored as five quantiles (5/25/50/75/95%) of every trophy's Exophase earn rate. The program (`main.py`, Python 3.13, standard library only, no network) reads one case file — the house's reads of the game's Exophase trophy page before a checkpoint — and writes one forecast file per case.

## The model

An earn rate is a race between two growth curves, so the model forecasts the two sides separately instead of extrapolating the rate itself:

- **Collapse to refresh steps.** Exophase refreshes its counts on its own schedule, so a history is a step function of identical reads. Only distinct snapshots carry information.
- **Pool vs earners.** For each distinct snapshot, implied earners are `rate × players_tracked`. Log pool size and log earners are each fitted against log time-since-release; earner slopes are shrunk toward their description group's slope (story chain, per-NPC final missions, the rest) and then the pooled slope — a trophy with four earners has a noisy slope — damped, and capped so earners never exceed the pool. The median rate is forecast earners over forecast pool: a trophy's rate falls when the tracked pool floods in faster than its earners accumulate, and rises as the cohort matures into it.
- **Saturation guard.** A companion fit on the logit scale (which bends over near 100%) is blended in as rates grow, so a hot story trophy is never extrapolated through the ceiling; no median moves more than 3.2 logit from the last read.
- **Flat-history launch prior.** When the history shows no refresh at all — the practice cases, cut before Exophase's first refresh — the next refresh lands inside the horizon: the pool jumps, earners crawl, rates dilute. Medians shift down on the logit scale and the spread widens to cover the unseen step. This branch is calibrated on the graded practice run itself: the realised refresh diluted rates to about an eighth of the stale page on average, far deeper than a persistence-shaped guess, so the shift sits at −1.55 logit at a 90-minute horizon. The first version of this entry scored 0.364 against the house persistence baseline's ~1.51 on that practice; the recalibrated branch fits the realised outcome materially better across the plausible refresh range.
- **Structure.** Story-chain medians are kept non-increasing (a later chapter cannot be earned by more players than an earlier one), the platinum median is capped at the rarest other trophy (it requires all of them), a 100%-completion median is capped at the fifth-rarest other median, and a trophy last seen at 100% — earned by every tracked player, before and after the refresh — is pinned near the top. Spread grows with √horizon plus a refresh-risk floor, a small-count term, and a term for how far the count and logit models disagree about a trophy.

Honest limits: the practice cases straddle a single refresh and cannot validate the week-long trend fit; the final is decided by how the pool/earner race actually runs over seven days, which the in-case trajectory — not any outside data — is allowed to speak to.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=nhastfac&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
