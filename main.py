"""gta6-trophy-rarity-forecast-01 entry (Python 3.13, standard library only).

Forecasts, for every GTA VI PS5 trophy, five quantiles of its Exophase earn
rate (percent of tracked players who earned it) at the case's target.

Method — an earners/players decomposition on the logit scale:

  rate_i(t) = E_i(t) / N(t),  E = implied earners, N = players tracked.

A case's history is a step function: Exophase refreshes its counts on its own
schedule, so consecutive reads are often identical. The model first collapses
the history to its distinct snapshots, then

  * fits log N against log time-since-release and extrapolates the tracked
    pool to the target (the pool only grows; the slope is clamped at 0);
  * fits log (E_i + 0.75) against the same clock for each trophy, shrinks each
    slope toward its description group's slope and then the pooled slope,
    and extrapolates earners from the last snapshot (small counts are noisy,
    hence the shrinkage and the +0.75 continuity offset);
  * takes the median rate as E_i(target) / N(target), so a trophy's rate
    falls when the pool grows faster than its earners and rises when its
    earners outgrow the pool — the dilution/maturation race that decides
    launch-week rarity.

When the history holds no trend (one distinct snapshot — the practice cases,
whose every read is the stale pre-refresh page), the model falls back to a
launch prior: a fresh tracked pool floods in at a refresh, earners grow far
slower than the pool over a short horizon, so medians shift down on the logit
scale and the spread widens to cover the unseen refresh.

Quantiles are median_logit + z_tau * sigma on the logit scale. sigma grows
with the square root of the horizon (random-walk part), plus a floor for
refresh risk, plus a term for trophies with only a handful of earners.
A trophy last seen at 100% is pinned near the top: every tracked player has
it, before and after the refresh the rules describe. Story-chain medians are
kept non-increasing along the chain (a later chapter cannot be earned by
more players than an earlier one), and the platinum median is capped at the
rarest other trophy's median (it requires all of them).

The grader runs `python3 -P -s /bot/main.py`; the sys.path line below puts
this file's directory back first, as the starter prescribes. No network, no
randomness: two runs on the same case write the same bytes.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Spread of a rate's move, in logit units per square-root day (random walk).
# Calibrated after the graded practice run: its 90% band covered 98% of the
# realised rates, so the base spreads came down slightly.
SIGMA_DAY = 0.57
# Extra logit spread that is always present: an unseen Exophase refresh can
# step every rate between the last read and the target.
REFRESH_SD = 0.27
# How much extra spread a disagreement between the count model and the
# logit model buys, per logit of disagreement (capped).
DISAGREE_SD = 0.35
# Flat-history (no trend visible) launch prior: median logit shift at a
# 90-minute horizon, and the spread used there. The graded practice refresh
# diluted rates to about an eighth of the stale page on average — far deeper
# than the first version's -1.0 shift assumed — so the shift deepened toward
# the realised value (shrunk, as it rests on a single observed refresh).
FLAT_SHIFT_90M = -1.55
FLAT_SD = 0.82


def clamp_rate(x: float) -> float:
    return min(99.99, max(0.01, x))


def logit(pct: float) -> float:
    p = clamp_rate(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(lg: float) -> float:
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x: float):
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if float(value).is_integer() else value


def parse_time(text: str) -> float:
    """ISO 8601 UTC -> epoch seconds."""
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def ols_slope(xs, ys, anchor_x=None, anchor_y=None):
    """Least-squares slope of ys on xs; if an anchor is given, the line is
    forced through it and the slope is the weighted best fit around it."""
    n = len(xs)
    if n < 2:
        return 0.0
    if anchor_x is not None:
        num = 0.0
        den = 0.0
        for x, y in zip(xs, ys):
            dx = x - anchor_x
            num += dx * (y - anchor_y)
            den += dx * dx
        return num / den if den > 1e-12 else 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den > 1e-12 else 0.0


def distinct_snapshots(case):
    """Collapse history to reads whose (players, rates) differ from the
    previous kept read: Exophase refresh steps, oldest first."""
    snaps = []
    for read in case["history"]:
        key = (read.get("players_tracked"), tuple(
            -1.0 if r is None else r for r in read["rates"]))
        if snaps and snaps[-1][0] == key:
            continue
        when = read.get("slot") or read.get("at")
        snaps.append((key, parse_time(when), read))
    return [s[2] for s in snaps], [s[1] for s in snaps]


def forecast_case(case, name):
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError(f"{name} has no history")
    trophies = case["trophies"]
    horizon_days = case["horizon_minutes"] / 1440.0

    release = parse_time(case["game"]["release_date"] + "T00:00:00Z")

    def x_of(epoch):
        hours = max(0.0, (epoch - release) / 3600.0)
        return math.log1p(hours)

    snaps, times = distinct_snapshots(case)
    xs = [x_of(t) for t in times]
    x_last = xs[-1]
    x_target = x_of(parse_time(case["target"]))
    if x_target <= x_last:
        x_target = x_last + math.log1p(case["horizon_minutes"] / 60.0)

    last = snaps[-1]
    n_last = float(last["players_tracked"])
    last_rates = []
    for i in range(len(trophies)):
        r = last["rates"][i]
        if r is None:  # fall back to the most recent read that listed it
            for snap in reversed(snaps):
                if snap["rates"][i] is not None:
                    r = snap["rates"][i]
                    break
        last_rates.append(r)

    medians = [None] * len(trophies)
    sigmas = [0.9] * len(trophies)

    if len(snaps) >= 3:
        # --- trend regime: fit pool growth and per-trophy earner growth ---
        # Drop the tiny pre-launch reviewer pool once the real pool is large:
        # its rates describe a different population.
        n_max = max(float(s["players_tracked"]) for s in snaps)
        keep = [k for k, s in enumerate(snaps)
                if n_max < 10000 or float(s["players_tracked"]) >= 0.02 * n_max]
        if len(keep) < 2:
            keep = list(range(len(snaps)))
        fx = [xs[k] for k in keep]
        log_n = [math.log(max(1.0, float(snaps[k]["players_tracked"]))) for k in keep]
        slope_n = max(0.0, ols_slope(fx, log_n, fx[-1], log_n[-1]))
        log_n_target = log_n[-1] + slope_n * (x_target - fx[-1])
        log_n_target = min(log_n_target, log_n[-1] + 2.6)  # pool at most ~13x
        n_target = math.exp(log_n_target)

        # Pooled earner growth from the summed implied earners.
        totals = []
        for k in keep:
            s = snaps[k]
            tot = 0.0
            for r in s["rates"]:
                if r is not None:
                    tot += r / 100.0 * float(s["players_tracked"])
            totals.append(math.log(tot + 0.75))
        slope_pool = ols_slope(fx, totals, fx[-1], totals[-1])
        slope_pool = min(4.0, max(-0.5, slope_pool))

        # Pooled logit-rate growth, for the saturating companion model.
        logit_slopes = []
        for i in range(len(trophies)):
            ys = [logit(snaps[k]["rates"][i]) for k in keep
                  if snaps[k]["rates"][i] is not None and snaps[k]["rates"][i] < 99.99]
            xs_i = [fx[j] for j, k in enumerate(keep)
                    if snaps[k]["rates"][i] is not None and snaps[k]["rates"][i] < 99.99]
            if len(ys) >= 2:
                logit_slopes.append(ols_slope(xs_i, ys, xs_i[-1], ys[-1]))
        logit_slopes.sort()
        slope_logit_pool = logit_slopes[len(logit_slopes) // 2] if logit_slopes else 0.0
        slope_logit_pool = min(2.5, max(-1.5, slope_logit_pool))

        # Hierarchical shrinkage targets for the earner slopes: trophies
        # whose descriptions share a shape (the story chain, the per-NPC
        # final missions, everything else) share a trajectory, so each
        # group's summed-earner slope is fitted as well as the pooled one.
        def group_of(t):
            d = t.get("description", "")
            if d.startswith("Complete Chapter") or d == "Complete the final mission.":
                return "story"
            if d.startswith("Complete the final mission for"):
                return "sidestory"
            return "grind"

        groups = [group_of(t) for t in trophies]
        group_slope = {}
        for gname in set(groups):
            gtot = []
            for k in keep:
                s = snaps[k]
                tot = 0.0
                for i in range(len(trophies)):
                    if groups[i] != gname:
                        continue
                    r = s["rates"][i]
                    if r is not None:
                        tot += r / 100.0 * float(s["players_tracked"])
                gtot.append(math.log(tot + 0.75))
            gs = ols_slope(fx, gtot, fx[-1], gtot[-1])
            group_slope[gname] = min(4.0, max(-0.5, gs))
        shrink_g = len(keep) / (len(keep) + 2.0)

        # Raw per-trophy earner slopes, before shrinkage.
        raw_b = []
        for i in range(len(trophies)):
            ys = []
            for k in keep:
                r = snaps[k]["rates"][i]
                e = (r / 100.0 * float(snaps[k]["players_tracked"])) if r is not None else 0.0
                ys.append(math.log(e + 0.75))
            bi = ols_slope(fx, ys, fx[-1], ys[-1])
            raw_b.append(min(4.0, max(-0.5, bi)))

        dx = x_target - x_last
        for i in range(len(trophies)):
            r_last = last_rates[i]
            if r_last is None:
                continue
            e_last = r_last / 100.0 * n_last
            shrink = len(keep) / (len(keep) + 3.0)
            b_group = shrink_g * group_slope[groups[i]] + (1.0 - shrink_g) * slope_pool
            b = shrink * raw_b[i] + (1.0 - shrink) * b_group
            # Damped count model: earner growth decelerates, and earners can
            # never exceed the pool that could have earned the trophy.
            log_e = math.log(e_last + 0.75) + 0.85 * b * dx
            e_pred = max(0.0, math.exp(log_e) - 0.75)
            e_pred = min(e_pred, 0.97 * n_target)
            m_count = clamp_rate(100.0 * e_pred / n_target)
            # Saturating logit model: the same trend read on the logit
            # scale, which bends over as a rate nears 100%.
            ys_l = [logit(snaps[k]["rates"][i]) for k in keep
                    if snaps[k]["rates"][i] is not None and snaps[k]["rates"][i] < 99.99]
            xs_l = [fx[j] for j, k in enumerate(keep)
                    if snaps[k]["rates"][i] is not None and snaps[k]["rates"][i] < 99.99]
            if len(ys_l) >= 2:
                g_i = ols_slope(xs_l, ys_l, xs_l[-1], ys_l[-1])
                g_i = min(2.5, max(-1.5, g_i))
                g = shrink * g_i + (1.0 - shrink) * slope_logit_pool
            else:
                g = slope_logit_pool
            m_logit = inv_logit(logit(r_last) + 0.75 * g * dx)
            # Blend on the logit scale: the count model reads rare trophies
            # best; the logit model keeps common trophies honest.
            lg_count = logit(m_count)
            lg_logit = logit(m_logit)
            w_count = 1.0 / (1.0 + math.exp((r_last - 8.0) / 6.0))
            lg = w_count * lg_count + (1.0 - w_count) * lg_logit
            # No median may move more than 3.2 logit from the last read:
            # beyond that the trend is an artefact, not a forecast.
            lg = min(logit(r_last) + 3.2, max(logit(r_last) - 3.2, lg))
            medians[i] = clamp_rate(inv_logit(lg))
            extra = 0.25 / math.sqrt(max(1.0, e_last))
            # Where the two models disagree, the honest spread is wider.
            dis = min(1.5, abs(lg_count - lg_logit))
            sigmas[i] = min(2.2, math.sqrt(
                (SIGMA_DAY * math.sqrt(horizon_days)) ** 2 + REFRESH_SD ** 2
                + extra ** 2 + (DISAGREE_SD * dis) ** 2))
    else:
        # --- flat regime: no trend visible; apply the launch prior ---
        # The page has not refreshed inside the history, so the next refresh
        # lands between the checkpoint and the target: the pool jumps, earners
        # crawl, rates dilute. Shift grows with horizon, saturating.
        h_hours = case["horizon_minutes"] / 60.0
        shift = FLAT_SHIFT_90M * (1.0 - math.exp(-h_hours / 1.5)) / (1.0 - math.exp(-1.0))
        sd = min(1.6, FLAT_SD + 0.35 * math.sqrt(horizon_days))
        for i in range(len(trophies)):
            r_last = last_rates[i]
            if r_last is None:
                continue
            medians[i] = inv_logit(logit(r_last) + shift)
            sigmas[i] = sd

    # Tier priors for any trophy never seen in the history.
    tier_prior = {"platinum": 0.2, "gold": 1.0, "silver": 1.5, "bronze": 3.0}
    for i, t in enumerate(trophies):
        if medians[i] is None:
            medians[i] = tier_prior.get(t.get("tier"), 1.0)
            sigmas[i] = 1.4

    # Story chain (chapters in id order among the "Complete Chapter / final
    # mission" line as listed in the case) must be non-increasing.
    chain = [i for i, t in enumerate(trophies)
             if t["description"].startswith("Complete Chapter")
             or t["description"] == "Complete the final mission."]
    run_min = None
    for i in sorted(chain, key=lambda j: trophies[j]["id"]):
        run_min = medians[i] if run_min is None else min(run_min, medians[i])
        medians[i] = run_min
    # Platinum requires every other trophy: cap it at the rarest of them.
    for i, t in enumerate(trophies):
        if t.get("tier") == "platinum":
            others = [medians[j] for j in range(len(trophies)) if j != i]
            if others:
                medians[i] = min(medians[i], min(others))
    # A 100%-completion trophy requires nearly everything else: cap it at
    # the fifth-rarest other median (one unrelated freak rarity must not
    # bind it, but it cannot outrun the pack of hard trophies either).
    for i, t in enumerate(trophies):
        if "100% Completion" in t.get("description", ""):
            others = sorted(medians[j] for j in range(len(trophies)) if j != i)
            if len(others) >= 5:
                medians[i] = min(medians[i], others[4])

    rows = []
    for i, t in enumerate(trophies):
        r_last = last_rates[i]
        if r_last is not None and r_last >= 99.99:
            q = [99.5, 99.85, 99.95, 99.98, 99.99]
        else:
            lg = logit(medians[i])
            q = [round4(inv_logit(lg + z * sigmas[i])) for z in Z]
            q = [min(99.99, max(0.01, float(v))) for v in q]
            q.sort()
        rows.append({"id": t["id"], "q": q})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
