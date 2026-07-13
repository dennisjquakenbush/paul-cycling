"""
Analytics engine. Reads the cached Garmin season + Apple Health recovery data
and computes everything the dashboard shows, from the raw streams up:

  - per-ride: avg/NP power, best-effort power at standard durations, HR drift,
    HR:power decoupling, our own TSS
  - power curve (mean-maximal power) across the season + FTP estimate + W/kg
  - power & HR zones and time-in-zone
  - daily TSS -> CTL (fitness) / ATL (fatigue) / TSB (form) with ramp-rate flags
  - recovery trends (HRV, resting HR, sleep) from Apple Health
  - a junior-athlete readiness verdict

Writes web/data.js as `window.PAUL_DATA = {...}` so the dashboard is a static
file that opens with a double-click (no server, no CORS).

Everything treats Paul as a 16-year-old junior: conservative ramp flags, heavy
weight on sleep/recovery, no restrictive fueling logic.
"""

import json
import math
import os
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# Standard durations (seconds) for the power curve / best efforts.
DURATIONS = [5, 15, 30, 60, 300, 480, 1200, 3600]
DURATION_LABELS = {5: "5s", 15: "15s", 30: "30s", 60: "1min", 300: "5min",
                   480: "8min", 1200: "20min", 3600: "60min"}

# CTL/ATL exponential time constants (days), standard Banister/TrainingPeaks.
CTL_TC = 42
ATL_TC = 7

# Junior-safe weekly CTL ramp ceiling.
RAMP_WARN = 5.0


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def clean(seq):
    return [x for x in seq if x is not None]


def rolling_best(power, window_s, dt=1.0):
    """Best average power sustained over `window_s` seconds (assumes ~1Hz)."""
    n = int(round(window_s / dt))
    if not power or len(power) < n or n <= 0:
        return None
    # treat gaps (None) as 0 so we don't overstate efforts across pauses
    p = [x if x is not None else 0.0 for x in power]
    csum = [0.0]
    for v in p:
        csum.append(csum[-1] + v)
    best = 0.0
    for i in range(0, len(p) - n + 1):
        avg = (csum[i + n] - csum[i]) / n
        if avg > best:
            best = avg
    return round(best, 1)


def normalized_power(power, dt=1.0):
    """Coggan Normalized Power: 4th-root of the mean of 30s-rolling-avg^4."""
    p = [x if x is not None else 0.0 for x in power]
    win = int(round(30 / dt))
    if len(p) < win or win <= 0:
        vals = clean(power)
        return round(sum(vals) / len(vals), 1) if vals else None
    csum = [0.0]
    for v in p:
        csum.append(csum[-1] + v)
    acc = 0.0
    cnt = 0
    for i in range(0, len(p) - win + 1):
        avg = (csum[i + win] - csum[i]) / win
        acc += avg ** 4
        cnt += 1
    if not cnt:
        return None
    return round((acc / cnt) ** 0.25, 1)


# --------------------------------------------------------------------------- #
# per-ride metrics
# --------------------------------------------------------------------------- #

def sample_dt(streams):
    """Estimate seconds-per-sample from the elapsed-duration stream."""
    el = streams.get("elapsed")
    if el and len(el) > 10:
        vals = clean(el)
        if len(vals) > 2 and vals[-1] and vals[-1] > 0:
            return max(0.5, vals[-1] / (len(el) - 1))
    return 1.0


def ride_metrics(a, ftp):
    """Compute our own metrics for one ride from its streams."""
    s = a.get("streams") or {}
    power = s.get("power") or []
    hr = s.get("hr") or []
    dt = sample_dt(s)

    pvals = clean(power)
    hvals = clean(hr)
    has_power = len(pvals) > 30 and any(v > 0 for v in pvals)
    has_hr = len(hvals) > 30

    m = {
        "id": a["id"], "name": a["name"], "type": a["type"], "start": a["start"],
        "date": (a["start"] or "")[:10],
        "duration_s": a.get("duration_s") or 0,
        "distance_km": round((a.get("distance_m") or 0) / 1000, 2),
        "elev_gain_m": round(a.get("elev_gain_m") or 0),
        "grit": a.get("grit"), "flow": a.get("flow"),
        "max_temp_c": a.get("max_temp_c"), "min_temp_c": a.get("min_temp_c"),
        "avg_cad": a.get("avg_cad"), "avg_speed_mps": a.get("avg_speed_mps"),
        "mmp_garmin": a.get("mmp") or {},
        "has_power": has_power, "has_hr": has_hr,
        "avg_power": round(sum(pvals) / len(pvals), 1) if has_power else None,
        "max_power": round(max(pvals), 1) if has_power else None,
        "avg_hr": round(sum(hvals) / len(hvals)) if has_hr else None,
        "max_hr": round(max(hvals)) if has_hr else None,
        "np": normalized_power(power, dt) if has_power else None,
    }

    # best efforts
    m["best"] = {}
    if has_power:
        for d in DURATIONS:
            m["best"][d] = rolling_best(power, d, dt)

    # TSS: prefer our power TSS, then Garmin's, then an HR estimate
    m["tss"], m["tss_src"] = compute_tss(m, a, ftp)

    # HR:power decoupling + power fade on rides >= 45 min
    m["decoupling"] = decoupling(power, hr, dt) if (has_power and has_hr) else None
    m["power_fade"] = power_fade(power, dt) if has_power else None
    return m


def compute_tss(m, a, ftp):
    dur_h = (m["duration_s"] or 0) / 3600
    if m["has_power"] and m["np"] and ftp:
        intensity = m["np"] / ftp
        return round(dur_h * intensity * intensity * 100, 1), "power"
    if a.get("tss_garmin"):
        return round(a["tss_garmin"], 1), "garmin"
    # crude HR-based fallback (hrTSS): needs avg HR vs threshold
    if m["has_hr"] and m["avg_hr"]:
        # threshold HR filled in later at season level; use a nominal 170 here
        thr = 170
        frac = m["avg_hr"] / thr
        return round(dur_h * frac * frac * 100, 1), "hr"
    return 0.0, "none"


def decoupling(power, hr, dt):
    """
    Pw:Hr decoupling: % change in power/HR efficiency between first and second
    half of the ride. >5% suggests fatigue / poor durability.
    """
    n = min(len(power), len(hr))
    if n < 200:
        return None
    half = n // 2

    def eff(ps, hs):
        pp = [x for x in ps if x is not None]
        hh = [x for x in hs if x is not None and x > 0]
        if not pp or not hh:
            return None
        return (sum(pp) / len(pp)) / (sum(hh) / len(hh))

    e1 = eff(power[:half], hr[:half])
    e2 = eff(power[half:n], hr[half:n])
    if not e1 or not e2:
        return None
    return round((e1 - e2) / e1 * 100, 1)


def power_fade(power, dt):
    """Average power in first vs last quarter of the ride (% drop)."""
    p = [x for x in power if x is not None]
    if len(p) < 400:
        return None
    q = len(p) // 4
    first = sum(p[:q]) / q
    last = sum(p[-q:]) / q
    if first <= 0:
        return None
    return round((first - last) / first * 100, 1)


# --------------------------------------------------------------------------- #
# season aggregates
# --------------------------------------------------------------------------- #

def power_curve(rides):
    curve = {}
    for d in DURATIONS:
        best = None
        best_ride = None
        for r in rides:
            v = (r.get("best") or {}).get(d)
            if v is not None and (best is None or v > best):
                best, best_ride = v, r["date"]
        curve[d] = {"watts": best, "date": best_ride}
    return curve


def estimate_ftp(curve):
    """
    Estimate FTP and, importantly, judge how trustworthy it is.

    Standard method is 95% of a maximal 20-min effort. But Paul races MTB and has
    almost certainly never done a clean 20-min test, so his best 20-min is just
    the hardest 20 minutes he happened to ride, not a true maximal. We cross-check
    against 5-min power: for a trained rider FTP is roughly 72-75% of 5-min power.
    A large gap between the two estimates means the 20-min number is unreliable and
    his real threshold is probably higher - a genuine "go do a test" flag, not a
    recording error.

    Returns (ftp, info-dict).
    """
    p5 = curve.get(300, {}).get("watts")
    p20 = curve.get(1200, {}).get("watts")

    est_20 = round(p20 * 0.95) if p20 else None
    est_5 = round(p5 * 0.73) if p5 else None

    info = {"from_20min": est_20, "from_5min": est_5,
            "p5_p20_ratio": round(p5 / p20, 2) if (p5 and p20) else None,
            "confidence": "low", "note": ""}

    if est_20 and est_5:
        lo, hi = sorted([est_20, est_5])
        info["range"] = [lo, hi]
        ratio = p5 / p20
        if ratio > 1.35:
            # 20-min effort looks sub-maximal; his engine says he's stronger
            info["confidence"] = "low"
            info["note"] = ("His 5-min power is very high relative to his best "
                            "20-min, which almost always means he has never done a "
                            "maximal sustained effort - not that his threshold is "
                            "low. Treat FTP as a range and confirm with a 20-min or "
                            "ramp test. Load/zones below use the conservative "
                            "20-min number.")
            ftp = est_20
        else:
            info["confidence"] = "moderate"
            info["note"] = "20-min and 5-min estimates broadly agree."
            ftp = est_20
        return ftp, info

    if est_20:
        info["confidence"] = "moderate"
        return est_20, info
    if est_5:
        info["confidence"] = "low"
        info["note"] = "No usable 20-min effort; FTP inferred from 5-min power only."
        return est_5, info
    return None, info


def classify_rider(curve, ftp):
    """Sprinter / punchy XC / diesel from curve shape relative to FTP."""
    p5 = curve.get(5, {}).get("watts")
    p60s = curve.get(60, {}).get("watts")
    p300 = curve.get(300, {}).get("watts")
    if not (p5 and p300 and ftp):
        return "unclassified", ""
    sprint_ratio = p5 / ftp            # neuromuscular vs threshold
    vo2_ratio = p300 / ftp             # 5-min vs threshold
    if sprint_ratio > 4.5 and vo2_ratio > 1.18:
        return "punchy sprinter", ("Explosive up top with a strong 5-min engine - "
                                    "suits XC starts and repeated short climbs.")
    if sprint_ratio > 4.5:
        return "sprinter", "Big peak power; work on sustaining 5-20min efforts."
    if vo2_ratio > 1.2:
        return "punchy XC", ("Strong 5-min power over threshold - classic XC "
                             "profile that rewards repeated hard efforts.")
    return "diesel", "Steady threshold engine; sharpen top-end for race starts."


def power_zones(ftp):
    if not ftp:
        return []
    edges = [(0.00, 0.55, "Z1 Active recovery"),
             (0.55, 0.75, "Z2 Endurance"),
             (0.75, 0.90, "Z3 Tempo"),
             (0.90, 1.05, "Z4 Threshold"),
             (1.05, 1.20, "Z5 VO2max"),
             (1.20, 1.50, "Z6 Anaerobic"),
             (1.50, 4.00, "Z7 Neuromuscular")]
    return [{"zone": name, "lo": round(lo * ftp), "hi": round(hi * ftp)}
            for lo, hi, name in edges]


def time_in_zone(rides, ftp, since_days=42):
    """Seconds in each power zone over the recent window (power rides only)."""
    if not ftp:
        return None
    cutoff = (date.today() - timedelta(days=since_days)).isoformat()
    bounds = [0.55, 0.75, 0.90, 1.05, 1.20, 1.50]
    labels = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7"]
    secs = defaultdict(float)
    for r in rides:
        if r["date"] < cutoff or not r.get("has_power"):
            continue
        # recompute per-sample zone from stored streams
        # (streams live on the raw activity, merged below)
        for w, dt in r.get("_power_samples", []):
            frac = w / ftp
            zi = 0
            for b in bounds:
                if frac >= b:
                    zi += 1
            secs[labels[zi]] += dt
    total = sum(secs.values())
    if total <= 0:
        return None
    return [{"zone": z, "seconds": round(secs.get(z, 0)),
             "pct": round(secs.get(z, 0) / total * 100, 1)} for z in labels]


def daily_tss_series(rides):
    daily = defaultdict(float)
    for r in rides:
        if r["date"]:
            daily[r["date"]] += r.get("tss") or 0
    if not daily:
        return []
    start = datetime.fromisoformat(min(daily)).date()
    end = date.today()
    series = []
    d = start
    while d <= end:
        series.append({"date": d.isoformat(), "tss": round(daily.get(d.isoformat(), 0), 1)})
        d += timedelta(days=1)
    return series


def pmc(series):
    """Compute CTL / ATL / TSB from the daily TSS series."""
    ctl = atl = 0.0
    ctl_k = 1 - math.exp(-1 / CTL_TC)
    atl_k = 1 - math.exp(-1 / ATL_TC)
    out = []
    prev_ctl = prev_atl = 0.0
    for pt in series:
        tss = pt["tss"]
        # TSB uses yesterday's values
        tsb = prev_ctl - prev_atl
        ctl = ctl + ctl_k * (tss - ctl)
        atl = atl + atl_k * (tss - atl)
        out.append({"date": pt["date"], "ctl": round(ctl, 1),
                    "atl": round(atl, 1), "tsb": round(tsb, 1)})
        prev_ctl, prev_atl = ctl, atl
    return out


def weekly_tss(series):
    weeks = defaultdict(float)
    for pt in series:
        d = datetime.fromisoformat(pt["date"]).date()
        monday = d - timedelta(days=d.weekday())
        weeks[monday.isoformat()] += pt["tss"]
    return [{"week": k, "tss": round(v)} for k, v in sorted(weeks.items())]


# --------------------------------------------------------------------------- #
# advanced models - the deeper math
# --------------------------------------------------------------------------- #

# durations (s) for the mean-maximal-power sweep used by the Critical Power fit
CP_DURATIONS = [120, 180, 240, 300, 420, 480, 600, 720, 900, 1200]


def season_mmp(rides_raw, durations):
    """Best mean-maximal power at each duration across every ride's power stream."""
    out = {}
    for d in durations:
        best = None
        for a in rides_raw:
            s = a.get("streams") or {}
            p = s.get("power")
            if not p:
                continue
            v = rolling_best(p, d, sample_dt(s))
            if v and (best is None or v > best):
                best = v
        out[d] = best
    return out


def fit_critical_power(mmp, weight):
    """
    Fit the 2-parameter Critical Power model to maximal efforts:
        work(t) = CP * t + W'         (linear in t, since work = power * time)
    CP (watts) is the asymptote of sustainable power; W' (joules) is the finite
    anaerobic work capacity above CP - his 'matchbook' for surges and climbs.
    Fit by ordinary least squares on the 2-20 min efforts; report R^2 as the
    goodness of fit (how cleanly his efforts obey the model).
    """
    pts = [(t, w) for t, w in sorted(mmp.items()) if w and 120 <= t <= 1200]
    if len(pts) < 3:
        return None
    xs = [t for t, _ in pts]
    ys = [w * t for t, w in pts]            # work = power * time
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    cp = (n * sxy - sx * sy) / denom        # slope
    wprime = (sy - cp * sx) / n             # intercept
    ybar = sy / n
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((y - (cp * x + wprime)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    if cp <= 0 or wprime <= 0:
        return None
    return {
        "cp": round(cp), "cp_wkg": round(cp / weight, 2) if weight else None,
        "w_prime_j": round(wprime), "w_prime_kj": round(wprime / 1000, 1),
        "r2": round(r2, 3) if r2 is not None else None,
        "points": [{"secs": t, "watts": round(w)} for t, w in pts],
        # CP-model prediction of sustainable power for a race of length T
        "predict": {
            "short_track_25min": round(cp + wprime / 1500),
            "20min": round(cp + wprime / 1200),
            "45min": round(cp + wprime / 2700),
            "75min": round(cp + wprime / 4500),
        },
    }


def efficiency_factor(rides):
    """
    Efficiency Factor (EF) = Normalized Power / average HR on aerobic rides.
    Rising EF over weeks = the aerobic engine is getting stronger (more watts per
    heartbeat). A clean, HR-based fitness signal independent of how hard he rode.
    """
    pts = []
    for r in sorted(rides, key=lambda x: x["date"]):
        if r.get("np") and r.get("avg_hr") and (r["duration_s"] or 0) >= 1500:
            pts.append({"date": r["date"], "ef": round(r["np"] / r["avg_hr"], 3)})
    if len(pts) < 4:
        return {"points": pts, "trend_pct": None}
    recent = [p["ef"] for p in pts[-4:]]
    prior = [p["ef"] for p in pts[-8:-4]] or [p["ef"] for p in pts[:-4]]
    tr = None
    if prior:
        tr = round((statistics.mean(recent) - statistics.mean(prior)) / statistics.mean(prior) * 100, 1)
    return {"points": pts, "trend_pct": tr, "latest": pts[-1]["ef"]}


def variability_index(rides):
    """VI = NP / average power. ~1.0 = steady (TT-like); high = punchy/stochastic
    (MTB racing). Tells us how spiky his riding is, which drives W' usage."""
    vis = []
    for r in rides:
        if r.get("np") and r.get("avg_power") and r["avg_power"] > 0:
            vis.append(r["np"] / r["avg_power"])
    if not vis:
        return None
    return {"season_median": round(statistics.median(vis), 2),
            "highest": round(max(vis), 2)}


def monotony_strain(daily_series):
    """
    Foster's training monotony & strain from daily load (last 7 days):
        monotony = mean(daily TSS) / stdev(daily TSS)
        strain   = weekly load * monotony
    High monotony (>2) means every day looks the same (no easy/hard contrast),
    which - especially with high strain - is a classic illness/overtraining flag.
    """
    if len(daily_series) < 7:
        return None
    last7 = [p["tss"] for p in daily_series[-7:]]
    mean = statistics.mean(last7)
    sd = statistics.pstdev(last7)
    if sd < 1e-6:
        monotony = None if mean == 0 else 3.0   # all-same nonzero days = very monotonous
    else:
        monotony = mean / sd
    weekly = sum(last7)
    strain = weekly * monotony if monotony else None
    flag = monotony is not None and monotony > 2.0
    return {"monotony": round(monotony, 2) if monotony else None,
            "weekly_load": round(weekly),
            "strain": round(strain) if strain else None,
            "high_risk": flag}


def durability(rides):
    """
    Fatigue resistance: on long rides (>=90 min), how much power fades from the
    first to the last quarter (Pw drop %). Lower fade = more durable = wins races
    in the back half. Reported as a 0-100 durability score (100 = no fade).
    """
    fades = [r["power_fade"] for r in rides
             if r.get("power_fade") is not None and (r["duration_s"] or 0) >= 5400]
    if not fades:
        return None
    avg_fade = statistics.mean(fades)
    return {"avg_fade_pct": round(avg_fade, 1),
            "score": int(max(0, min(100, round(100 - avg_fade)))),
            "n_rides": len(fades)}


def season_decoupling(rides):
    decs = [r["decoupling"] for r in rides
            if r.get("decoupling") is not None and (r["duration_s"] or 0) >= 2700]
    if not decs:
        return None
    return {"median": round(statistics.median(decs), 1), "n": len(decs)}


def mtb_skills(rides):
    """
    Garmin's Grit (how demanding the terrain is) and Flow (how smoothly he descends
    - lower is smoother). Trends tell us if he's riding harder trails and getting
    slicker on the descents, where MTB races are often won or lost.
    """
    def series(field):
        return [(r["date"], r[field]) for r in sorted(rides, key=lambda x: x["date"])
                if r.get(field) is not None and r["type"] == "mountain_biking"]
    grit = series("grit")
    flow = series("flow")
    if not grit:
        return None

    def recent_prior(pts):
        if len(pts) < 6:
            return (round(statistics.mean(v for _, v in pts), 1) if pts else None, None)
        r = statistics.mean(v for _, v in pts[-5:])
        p = statistics.mean(v for _, v in pts[-10:-5])
        return round(r, 1), round(r - p, 1)

    g_avg, g_tr = recent_prior(grit)
    f_avg, f_tr = recent_prior(flow)
    return {"grit_recent": g_avg, "grit_trend": g_tr,
            "flow_recent": f_avg, "flow_trend": f_tr,
            "grit_max": round(max(v for _, v in grit), 1)}


def descending(rides):
    """
    'Free speed' on descents. Flow rises naturally as Grit (trail difficulty) rises,
    so the raw Flow number is misleading. We fit his own Flow-vs-Grit line, then read
    each ride's residual: Flow BELOW his line = smoother than his norm for that
    difficulty (skill), ABOVE = choppy (braking too much = time lost). We combine that
    with descent speed to estimate how much free speed is on the table.
    """
    pts = [r for r in rides if r["type"] == "mountain_biking"
           and r.get("grit") and r.get("flow") is not None]
    if len(pts) < 6:
        return None
    grit = [r["grit"] for r in pts]
    flow = [r["flow"] for r in pts]
    n = len(pts)
    sx, sy = sum(grit), sum(flow)
    sxx = sum(g * g for g in grit)
    sxy = sum(g * f for g, f in zip(grit, flow))
    denom = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / denom if denom else 0.0     # flow gained per grit
    intercept = (sy - slope * sx) / n

    resid = []
    for r in pts:
        r["_fr"] = r["flow"] - (slope * r["grit"] + intercept)
        resid.append(r["_fr"])
    sd = statistics.pstdev(resid) or 1.0
    by_date = sorted(pts, key=lambda x: x["date"])
    recent = by_date[-6:]
    prior = by_date[:-6][-6:]
    recent_resid = statistics.mean(r["_fr"] for r in recent)
    # smoother-than-his-norm -> higher score
    score = int(max(1, min(99, round(50 - (recent_resid / sd) * 22))))
    trend = None
    if prior:
        trend = round(statistics.mean(r["_fr"] for r in prior) - recent_resid, 2)  # +ve = improving

    # descent speed context (mph) on the more descent-heavy rides
    spd = [r.get("avg_speed_mps") for r in pts if r.get("avg_speed_mps")]
    avg_mph = round(statistics.mean(spd) * 2.23694, 1) if spd else None

    flow_per10 = slope * 10
    if score < 45 or flow_per10 > 1.5:
        free, ftxt = "high", "There's real time to gain by braking less and carrying speed through the rough stuff."
    elif score < 60:
        free, ftxt = "moderate", "Some free speed available - smoothing the choppiest descents would help."
    else:
        free, ftxt = "low", "He's already smooth for the terrain he rides - little free speed left on descents."

    return {"skill_score": score, "trend": trend, "free_speed": free,
            "flow_per_10grit": round(flow_per10, 2), "avg_speed_mph": avg_mph,
            "note": ftxt}


def heat_exposure(rides):
    """
    Heat adaptation from ride temperatures (F). Racing/training in the heat builds
    tolerance; if his recent rides are hot he'll cope better on a hot race day, and
    his fueling/hydration plan should assume it.
    """
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    temps = [r["max_temp_c"] for r in rides
             if r.get("max_temp_c") is not None and r["date"] >= cutoff]
    if not temps:
        return None
    hot = sum(1 for t in temps if t >= 25)       # >=77F
    very_hot = sum(1 for t in temps if t >= 30)   # >=86F
    frac = hot / len(temps)
    adapted = frac >= 0.5
    return {"rides_30d": len(temps),
            "avg_max_f": round(statistics.mean(temps) * 9 / 5 + 32),
            "hottest_f": round(max(temps) * 9 / 5 + 32),
            "hot_ride_pct": round(frac * 100),
            "very_hot_rides": very_hot,
            "heat_adapted": adapted}


def climbing_cadence(rides):
    total_gain_m = sum(r.get("elev_gain_m") or 0 for r in rides)
    cads = [r["avg_cad"] for r in rides if r.get("avg_cad")]
    biggest = max((r.get("elev_gain_m") or 0 for r in rides), default=0)
    return {"season_climb_ft": round(total_gain_m * 3.28084),
            "biggest_ride_ft": round(biggest * 3.28084),
            "avg_cadence": round(statistics.mean(cads)) if cads else None}


def season_mmp_garmin(rides, durations):
    """Season-best mean-maximal power using Garmin's own per-ride numbers (more
    reliable than re-deriving from streams, and covers more durations)."""
    out = {}
    for d in durations:
        vals = [r["mmp_garmin"].get(str(d)) for r in rides if r.get("mmp_garmin", {}).get(str(d))]
        if vals:
            out[d] = max(vals)
    return out


def climbing_power(cp, ftp, weight_kg, bike_kg):
    """
    Turn power + weight into real climbing performance. Uphill speed is set by the
    WHOLE system (rider + bike), so this computes system power-to-weight and, from
    the physics of climbing, his sustainable vertical rate (VAM) at Critical Power,
    plus how much each pound off the bike is worth in seconds.
    """
    if not weight_kg:
        return None
    body = weight_kg
    system = body + (bike_kg or 0)
    out = {
        "body_lb": round(body / 0.453592, 1),
        "cp_wkg_body": round(cp / body, 2) if cp else None,
        "ftp_wkg_body": round(ftp / body, 2) if ftp else None,
    }
    if not bike_kg:
        return out

    out["bike_lb"] = round(bike_kg / 0.453592, 1)
    out["system_lb"] = round(system / 0.453592, 1)
    out["bike_pct"] = round(bike_kg / system * 100, 1)
    out["cp_wkg_system"] = round(cp / system, 2) if cp else None
    out["ftp_wkg_system"] = round(ftp / system, 2) if ftp else None

    if cp:
        g, crr, grade = 9.81, 0.018, 0.08          # 8% climb, XC dirt rolling resistance
        theta = math.atan(grade)
        s, c = math.sin(theta), math.cos(theta)
        # steady climb (aero negligible at climbing speed): P = m g v (sinθ + Crr cosθ)
        v = cp / (system * g * (s + crr * c))       # m/s along the slope
        vam_m = v * s * 3600                          # vertical metres / hour
        t100 = 30.48 / (v * s)                        # seconds per 100 ft of vertical
        out["vam_ft_per_h"] = round(vam_m * 3.28084)
        out["sec_per_100ft"] = round(t100)
        # climb time is proportional to mass -> seconds saved per lb, per 100 ft climbed
        out["sec_per_lb_per_100ft"] = round(t100 / system * 0.453592, 2)
        out["grade_assumed"] = "8%"
    return out


def build_models(rides, rides_raw, weight, daily_series, bike_kg=None, ftp=None):
    # prefer Garmin's own per-ride power curve for the CP fit; fall back to streams
    mmp = season_mmp_garmin(rides, CP_DURATIONS) or season_mmp(rides_raw, CP_DURATIONS)
    total_kj = 0.0
    for a in rides_raw:
        s = a.get("streams") or {}
        p = [x for x in (s.get("power") or []) if x]
        if p:
            total_kj += sum(p) * sample_dt(s) / 1000
    cp_fit = fit_critical_power(mmp, weight)
    cp_w = cp_fit["cp"] if cp_fit else None
    return {
        "critical_power": cp_fit,
        "climbing_power": climbing_power(cp_w, ftp, weight, bike_kg),
        "efficiency_factor": efficiency_factor(rides),
        "variability_index": variability_index(rides),
        "monotony_strain": monotony_strain(daily_series),
        "durability": durability(rides),
        "decoupling": season_decoupling(rides),
        "mtb_skills": mtb_skills(rides),
        "descending": descending(rides),
        "heat": heat_exposure(rides),
        "climbing": climbing_cadence(rides),
        "season_kj": round(total_kj),
    }


# --------------------------------------------------------------------------- #
# recovery (Apple Health)
# --------------------------------------------------------------------------- #

def recovery_block(apple, garmin_well):
    """
    Merge recovery data. Apple Health is the primary source (Paul's Apple Watch);
    Garmin wellness is used only if Apple data is missing and Garmin has any.
    """
    days = {}
    src = None
    if apple and apple.get("days"):
        days = apple["days"]
        src = "apple_health"
    elif garmin_well and garmin_well.get("has_data"):
        days = garmin_well["days"]
        src = "garmin"

    def series(field):
        out = []
        for d in sorted(days):
            v = days[d].get(field)
            if v is not None:
                out.append({"date": d, "v": v})
        return out

    def trend(points, n=7):
        if len(points) < n + 1:
            return None
        recent = sum(p["v"] for p in points[-n:]) / n
        prior = points[-(n + 1):-1]
        base = sum(p["v"] for p in points[-(2 * n):-n]) / max(1, len(points[-(2 * n):-n]))
        return round(recent - base, 1)

    hrv = series("hrv")
    rhr = series("resting_hr")
    sleep = series("sleep_h")
    return {
        "source": src,
        "hrv": hrv, "resting_hr": rhr, "sleep_h": sleep,
        "hrv_trend": trend(hrv), "rhr_trend": trend(rhr), "sleep_trend": trend(sleep),
        "latest": {
            "hrv": hrv[-1]["v"] if hrv else None,
            "resting_hr": rhr[-1]["v"] if rhr else None,
            "sleep_h": sleep[-1]["v"] if sleep else None,
        },
    }


# Indiana race calendar (kept in sync with the dashboard) for prep timing.
RACE_CALENDAR = [
    ("2026-05-16", "Winona Lake", "DINO"),
    ("2026-05-31", "Brown County SP", "DINO"),
    ("2026-06-21", "Potato Creek SP", "DINO"),
    ("2026-07-12", "Muscatatuck Park", "DINO"),
    ("2026-08-02", "Griffin Bike Park", "DINO"),
    ("2026-08-16", "Southwestway Park", "DINO"),
    ("2026-08-29", "Stoney Run, Hebron", "NICA"),
    ("2026-09-19", "Potato Creek SP", "NICA"),
    ("2026-10-03", "Muscatatuck Park", "NICA"),
    ("2026-10-17", "Southwestway Park", "NICA"),
]


def load_config():
    p = os.path.join(HERE, "config.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def load_riders():
    p = os.path.join(DATA, "riders.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


# venue coordinates for race-day weather
VENUE_COORDS = {
    "Winona Lake": (41.33, -85.83),
    "Brown County SP": (39.19, -86.23),
    "Potato Creek SP": (41.55, -86.35),
    "Muscatatuck Park": (39.00, -85.62),
    "Griffin Bike Park": (39.42, -87.31),
    "Southwestway Park": (39.664, -86.269),
    "Stoney Run, Hebron": (41.35, -87.16),
}

FORECAST_WINDOW_DAYS = 16


def race_weather(nr):
    """
    Forecast + auto-derived pacing/fueling adjustments for the next race, once it's
    inside the ~16-day forecast window. Returns a status so the dashboard can show
    a 'closer to race day' placeholder until then.
    """
    if not nr:
        return None
    if nr["days_out"] < 0 or nr["days_out"] > FORECAST_WINDOW_DAYS:
        return {"status": "too_far", "days_out": nr["days_out"], "name": nr["name"]}
    coords = VENUE_COORDS.get(nr["name"])
    if not coords:
        return {"status": "no_coords", "name": nr["name"]}
    import weather
    fc = weather.fetch_forecast(coords[0], coords[1], nr["date"])
    if not fc:
        return {"status": "unavailable", "name": nr["name"]}

    # derive adjustments
    adj = []
    hi = fc.get("high_f")
    if hi is not None:
        if hi >= 90:
            adj.append(("Hot race", f"~{hi} F. Expect some power loss; start the first climb a touch conservative and drink early. Push fluids to 24-30 oz/h with extra sodium; pre-cool (ice sock/cold drink) in the staging."))
        elif hi >= 80:
            adj.append(("Warm race", f"~{hi} F. Hydrate well ahead; 20-27 oz/h with electrolytes. Fine to race hard if paced."))
        elif hi <= 55:
            adj.append(("Cool race", f"~{hi} F. Warm up longer, arm/knee warmers to the line; carbs matter more than fluid."))
    if fc.get("mud_risk"):
        adj.append(("Likely mud", f"{fc.get('prior_precip_in',0)} in of rain in the two days prior. Drop tire pressure a few psi for grip, expect slower lap times and higher effort, and scrub/keep the drivetrain clean. Pre-ride the tricky lines."))
    elif (fc.get("precip_prob") or 0) >= 50:
        adj.append(("Rain possible", f"{fc.get('precip_prob')}% chance. Pack a rain layer; if it wets the course, ride the mud advice above."))
    if fc.get("wind_mph") and fc["wind_mph"] >= 15:
        adj.append(("Windy", f"{fc['wind_mph']} mph from the {fc.get('wind_dir','?')}. Shelter where you can on exposed/open sections and use the anaerobic battery out of the wind, not into it."))

    return {"status": "ok", "name": nr["name"], "date": nr["date"],
            "days_out": nr["days_out"], "forecast": fc, "adjustments": adj}


def next_race(excluded=None):
    excluded = set(excluded or [])
    t = date.today().isoformat()
    upcoming = [r for r in RACE_CALENDAR
                if r[0] >= t and f"{r[0]}|{r[1]}" not in excluded]
    if not upcoming:
        return None
    d, name, series = upcoming[0]
    days = (datetime.fromisoformat(d).date() - date.today()).days
    return {"date": d, "name": name, "series": series, "days_out": days}


def coaching_brief(pmc_series, ready, ftp, ftp_info, tiz, recovery, excluded=None):
    """
    A deterministic weekly coaching brief for a junior athlete: the single most
    important thing, then a day-by-day skeleton shaped by current form, ramp rate,
    and how close the next race is. Not a substitute for a coach - a smart default.
    """
    if not pmc_series:
        return None
    tsb = pmc_series[-1]["tsb"]
    nr = next_race(excluded)
    days_out = nr["days_out"] if nr else None

    # headline: the single most important change
    headline = ""
    if ftp_info.get("confidence") == "low" and ftp_info.get("p5_p20_ratio", 0) and ftp_info["p5_p20_ratio"] > 1.35:
        headline = ("Pin down his real FTP. His 5-min power says he is stronger than "
                    "his 20-min number - a 20-min or ramp test will fix every load and "
                    "zone figure here and unlock harder, better-targeted intervals.")
    elif tsb <= -20:
        headline = ("Recover first. Form is deep in the red after racing - two genuine "
                    "easy days will do more for the next race than any interval session.")
    elif days_out is not None and days_out <= 10:
        headline = f"Taper for {nr['name']} in {days_out} days: sharpen, then shed fatigue."
    else:
        # look at zone balance
        easy = sum(z["pct"] for z in (tiz or []) if z["zone"] in ("Z1", "Z2"))
        if tiz and easy < 70:
            headline = ("Make easy days easier. Too much riding sits in the middle; a "
                        "cleaner easy/hard split will lift his ceiling without more hours.")
        else:
            headline = "Consistent build. Keep the polarized split and add race-specific intensity."

    # weekly skeleton
    plan = []
    if tsb <= -20:
        plan = [
            ("Today", "Rest or 20-30 min spin, Z1 only. No intensity."),
            ("Day 2", "Easy Z2 45-60 min, keep HR down, focus on smooth pedaling."),
            ("Day 3", "Optional skills ride on the MTB - low intensity, work corners/descents."),
            ("Day 4", "First quality day back: 4 x 4 min at Z4 (threshold) if legs feel good."),
            ("Weekend", "Longer Z2 endurance ride, 2-3 h, some standing climbs."),
        ]
    elif days_out is not None and days_out <= 10:
        plan = [
            ("Early week", "2 x 8 min at threshold plus a few 30 s race-pace surges. Short overall."),
            ("Mid week", "Easy Z2 with 3 x 1 min openers the day before travel."),
            ("Day before", "20-30 min with 3 x 90 s build to race pace, then off feet."),
            ("Race day", "Warm up 15-20 min, one hard 1-min effort, then hold for the start."),
        ]
    else:
        plan = [
            ("Mon", "Rest or easy spin."),
            ("Tue", "VO2max: 5 x 3-4 min at Z5 (his strength) with equal recovery."),
            ("Wed", "Easy Z2 endurance, keep it genuinely easy."),
            ("Thu", "Threshold / over-unders: 3 x 8 min alternating Z3/Z4."),
            ("Fri", "Rest or skills ride on singletrack."),
            ("Sat", "Long Z2 with race-terrain climbs, 2-3 h."),
            ("Sun", "Short MTB skills + a few sprints, or rest."),
        ]

    notes = []
    if recovery and not recovery.get("source"):
        notes.append("Import Apple Health so sleep and HRV can gate the hard days - "
                     "at 16, recovery drives adaptation more than any workout.")
    if ready.get("ramp") is not None and ready["ramp"] > RAMP_WARN:
        notes.append("Hold weekly load flat this week; fitness is already climbing fast for a junior.")
    return {"headline": headline, "plan": plan, "notes": notes, "next_race": nr}


def fueling(weight_kg, pmc_series):
    """
    Junior-safe fueling and hydration targets, scaled to body weight. Generous by
    design - a 16-year-old should never be under-fueled. All ranges, never a diet.
    """
    w = weight_kg or 57
    ctl = pmc_series[-1]["ctl"] if pmc_series else 0

    # daily carbohydrate: ~5 g/kg easy days, up to ~8-10 g/kg on big training days
    def gkg(low, high):
        return [round(w * low), round(w * high)]
    daily = {
        "rest_easy": gkg(4, 5),
        "moderate": gkg(6, 7),
        "hard_or_long": gkg(8, 10),
        "note": ("Carbs fuel training and school. On hard or long days he should be at "
                 "the top of the range - low energy is the fastest way to stall a junior."),
    }

    # during-ride carbs + fluid by duration band (fluid in fl oz - imperial)
    during = [
        {"band": "Under 60 min", "carbs_g_per_h": "0-30", "fluid_oz_per_h": "16-24",
         "how": "Water is usually enough; a few sips of drink mix if it's hard."},
        {"band": "60-90 min", "carbs_g_per_h": "30-45", "fluid_oz_per_h": "16-24",
         "how": "One bottle of carb drink or a gel/chews midway."},
        {"band": "90-150 min", "carbs_g_per_h": "45-60", "fluid_oz_per_h": "20-27",
         "how": "Drink mix plus a gel or half a bar every 30-45 min. Start early."},
        {"band": "Over 150 min", "carbs_g_per_h": "60-90", "fluid_oz_per_h": "20-30",
         "how": "Mix drink-mix + gels + real food. Practice this on long training rides."},
    ]

    # race day (assumes a typical junior XC race ~45-75 min plus warm-up)
    race_day = {
        "night_before": ("Normal carb-rich dinner (rice/pasta/potatoes) plus a snack. "
                         "Hydrate through the evening; nothing new or unusual to eat."),
        "breakfast": (f"3 hours before start: {round(w*1.5)}-{round(w*2)} g carbs "
                      "(oatmeal, banana, toast, honey, juice). Familiar foods only."),
        "pre_start": ("15-45 min before: a gel or banana and ~8-12 oz fluid. "
                      "Top off, don't stuff."),
        "during": ("For a 45-75 min XC race, ~1 gel or a bottle of carb mix is plenty; "
                   "in heat prioritize drinking. Longer/marathon formats: 60 g carbs/h."),
        "after": (f"Within 30-60 min: {round(w*1)}-{round(w*1.2)} g carbs + ~20-25 g protein "
                  "(chocolate milk, sandwich, recovery shake) to refill and rebuild."),
    }

    hydration = {
        "baseline_oz_per_h": "16-25",
        "heat_note": ("Above ~77 F add ~8 oz/h and use electrolytes (sodium). Weigh "
                      "before/after long rides - replace ~16-24 oz for each pound lost."),
        "daily": "Sip through the school day; urine pale-straw, not clear, not dark.",
    }

    return {"weight_kg": w, "ctl": ctl,
            "daily_carbs_g": daily, "during_ride": during,
            "race_day": race_day, "hydration": hydration}


def recovery_score(pmc_series, recovery, models=None):
    """
    A single 0-100 recovery estimate (the Tour-de-France-style dial), blending
    training form with body signals and load distribution:
      - Form (TSB) is the backbone - how much training fatigue he's carrying.
      - HRV and resting HR nudge it up or down (trend vs baseline if we have enough
        history, otherwise their absolute quality).
      - Sleep trims it when short.
      - Training monotony/strain (Foster) trims it when load is dangerously flat.
    Junior-safe: leans conservative when signals conflict.
    """
    if not pmc_series:
        return None
    tsb = pmc_series[-1]["tsb"]
    rec = recovery or {}
    m = models or {}

    # base from form: TSB 0 -> ~65, +25 -> ~93, -30 -> ~31
    score = 65 + tsb * 1.15
    drivers = []

    if tsb <= -20:
        drivers.append({"text": "Deep fatigue from recent racing/hard training", "dir": "down"})
    elif tsb < -8:
        drivers.append({"text": "Carrying some training fatigue", "dir": "down"})
    elif tsb > 12:
        drivers.append({"text": "Well rested - form is fresh", "dir": "up"})
    else:
        drivers.append({"text": "Training load and freshness in balance", "dir": "flat"})

    # HRV
    hrv_pts = rec.get("hrv") or []
    if rec.get("hrv_trend") is not None:
        t = rec["hrv_trend"]
        if t <= -5:
            score -= 8; drivers.append({"text": f"HRV below baseline ({t})", "dir": "down"})
        elif t >= 5:
            score += 6; drivers.append({"text": f"HRV above baseline (+{t})", "dir": "up"})
    elif hrv_pts:
        v = hrv_pts[-1]["v"]
        if v >= 80:
            score += 4; drivers.append({"text": f"HRV is high ({round(v)} ms) - strong recovery signal", "dir": "up"})
        elif v < 40:
            score -= 4; drivers.append({"text": f"HRV is low ({round(v)} ms)", "dir": "down"})

    # resting HR
    rhr_pts = rec.get("resting_hr") or []
    if rec.get("rhr_trend") is not None:
        t = rec["rhr_trend"]
        if t >= 3:
            score -= 7; drivers.append({"text": f"Resting HR elevated (+{t} bpm)", "dir": "down"})
        elif t <= -2:
            score += 4; drivers.append({"text": "Resting HR low/settled", "dir": "up"})
    elif rhr_pts:
        v = rhr_pts[-1]["v"]
        if v <= 50:
            score += 3; drivers.append({"text": f"Resting HR is low ({round(v)} bpm) - well conditioned", "dir": "up"})
        elif v >= 70:
            score -= 3; drivers.append({"text": f"Resting HR is high ({round(v)} bpm)", "dir": "down"})

    # sleep
    sl = (rec.get("latest") or {}).get("sleep_h")
    if sl is not None:
        if sl < 7:
            score -= 6; drivers.append({"text": f"Short sleep ({sl} h)", "dir": "down"})
        elif sl >= 8:
            score += 4; drivers.append({"text": f"Good sleep ({sl} h)", "dir": "up"})

    # training monotony/strain - flat, relentless load blunts recovery
    ms = m.get("monotony_strain") or {}
    if ms.get("high_risk"):
        score -= 6; drivers.append({"text": f"Load is very monotonous (monotony {ms['monotony']})", "dir": "down"})

    score = int(max(3, min(99, round(score))))

    if score >= 80:
        band, color, est = "Fresh", "green", "Recovered and ready for quality work or a race."
    elif score >= 65:
        band, color, est = "Good", "green", "Nearly there - one steady day and he's set."
    elif score >= 50:
        band, color, est = "Moderate", "amber", "Recovering - keep the next day genuinely easy."
    elif score >= 35:
        band, color, est = "Low", "amber", "1-2 easy or rest days should refresh him."
    else:
        band, color, est = "Depleted", "red", "Needs 2-3 genuine rest/easy days to bounce back."

    return {"score": score, "band": band, "color": color, "estimate": est,
            "drivers": drivers[:4],
            "has_body_data": bool(hrv_pts or rhr_pts),
            "signals": {"tsb": tsb,
                        "hrv": hrv_pts[-1]["v"] if hrv_pts else None,
                        "resting_hr": rhr_pts[-1]["v"] if rhr_pts else None,
                        "sleep_h": sl}}


def synthesize(pmc_series, models, recovery, ftp, ftp_info, tiz, nr, weight, does_short_track=False):
    """
    The 'brain': connect signals across every model into a handful of plain-language
    observations. Each one deliberately combines 2-4 metrics so the relationships -
    not just the isolated numbers - drive the read on him.
    """
    out = []
    if not pmc_series:
        return out
    pmc = pmc_series[-1]
    m = models or {}
    cp = m.get("critical_power")
    ef = m.get("efficiency_factor") or {}
    dec = m.get("decoupling") or {}
    dur = m.get("durability") or {}
    ms = m.get("monotony_strain") or {}
    vi = m.get("variability_index") or {}
    rec = recovery or {}
    ramp = round(pmc["ctl"] - pmc_series[-8]["ctl"], 1) if len(pmc_series) > 7 else None

    # 1) Aerobic base trajectory: EF trend + decoupling + durability together
    good_base = ((ef.get("trend_pct") or 0) > 0) + (dec.get("median") is not None and dec["median"] < 5) + (dur.get("score", 0) >= 90)
    if ef.get("trend_pct") is not None or dec.get("median") is not None:
        bits = []
        if ef.get("trend_pct") is not None:
            bits.append(f"efficiency {'up' if ef['trend_pct'] >= 0 else 'down'} {abs(ef['trend_pct'])}% (watts per heartbeat)")
        if dec.get("median") is not None:
            bits.append(f"decoupling only {dec['median']}%")
        if dur.get("score") is not None:
            bits.append(f"durability {dur['score']}/100")
        tone = "good" if good_base >= 2 else "info"
        verb = "is building well" if good_base >= 2 else "is holding steady"
        out.append({"title": "Aerobic engine " + verb, "tone": tone,
                    "text": "Three independent signals agree: " + ", ".join(bits) +
                            ". A strong, fatigue-resistant aerobic base is his biggest weapon in a long XC race - it's what lets him keep punching late."})

    # 2) Threshold reconciliation: Critical Power vs the 20-min FTP + W'
    if cp and ftp:
        gap = ftp - cp["cp"]
        out.append({"title": "His real threshold sits around Critical Power", "tone": "info",
                    "text": f"The Critical Power model (fit at R² {cp['r2']}) puts his sustainable ceiling at {cp['cp']} W, "
                            f"{'just below' if 0 <= gap <= 15 else 'near'} the 20-min FTP estimate of {ftp} W. What really sets him apart is a large W' of "
                            f"{cp['w_prime_kj']} kJ - a deep anaerobic battery. So he's not a diesel with a high threshold; he's a threshold-plus-huge-punch rider."})

    # 3) Race archetype -> tactics: W' + Variability Index
    if cp and vi.get("season_median"):
        out.append({"title": "Built for punchy, stop-start racing", "tone": "info",
                    "text": f"His riding runs at a variability index of {vi['season_median']} (spiky, not steady) and he carries a {cp['w_prime_kj']} kJ anaerobic battery. "
                            "Tactically that means: spend the battery on the holeshot and the steep pitches, then settle to critical power and let it recharge on the flatter, faster sections."})

    # 4) Overtraining / illness risk: monotony + strain + ramp + HRV/RHR
    risk_bits, risk = [], 0
    if ms.get("monotony") is not None:
        risk_bits.append(f"monotony {ms['monotony']}")
        if ms.get("high_risk"): risk += 2
    if ramp is not None and ramp > RAMP_WARN:
        risk_bits.append(f"fitness ramping +{ramp}/wk"); risk += 1
    if rec.get("rhr_trend") and rec["rhr_trend"] >= 3:
        risk_bits.append(f"resting HR +{rec['rhr_trend']}"); risk += 1
    if rec.get("hrv_trend") and rec["hrv_trend"] <= -5:
        risk_bits.append(f"HRV {rec['hrv_trend']}"); risk += 1
    if risk_bits:
        if risk >= 2:
            out.append({"title": "Overtraining risk is worth watching", "tone": "watch",
                        "text": "Several load-and-recovery signals are leaning the wrong way at once (" + ", ".join(risk_bits) +
                                "). Individually each is minor; together they say add contrast - make the easy days truly easy - and don't stack hard days this week."})
        else:
            out.append({"title": "Load distribution looks healthy", "tone": "good",
                        "text": "Training monotony is low (" + ", ".join(risk_bits) +
                                "), meaning he's mixing genuinely hard and genuinely easy days rather than grinding the same medium every day. That's exactly how a junior should train."})

    # 5) Zone balance vs polarization target
    if tiz:
        easy = sum(z["pct"] for z in tiz if z["zone"] in ("Z1", "Z2"))
        if easy >= 72:
            out.append({"title": "Intensity distribution is well polarized", "tone": "good",
                        "text": f"About {round(easy)}% of his time is easy (Z1-Z2), with the rest genuinely hard. That polarized split is the most reliable way to lift the ceiling without piling on fatigue."})
        else:
            out.append({"title": "Push the easy days easier", "tone": "watch",
                        "text": f"Only {round(easy)}% of riding time is truly easy; too much sits in the tempo 'grey zone'. Slowing the easy rides down would let the hard days be harder and sharpen his top end."})

    # 6) Heat adaptation -> race-day plan
    heat = m.get("heat")
    if heat and heat.get("rides_30d", 0) >= 4:
        if heat["heat_adapted"]:
            out.append({"title": "He's heat-adapted right now", "tone": "good",
                        "text": f"{heat['hot_ride_pct']}% of his rides in the last month topped 77 F (avg high {heat['avg_max_f']} F, hottest {heat['hottest_f']} F). "
                                "That earned heat tolerance is a real edge on a hot race day - but keep hydration/sodium high to hold it, since the adaptation fades in about 2-3 weeks off the heat."})
        else:
            out.append({"title": "Not much recent heat exposure", "tone": "info",
                        "text": f"Only {heat['hot_ride_pct']}% of recent rides were hot. If a race day looks hot, expect some power loss and plan a couple of deliberately warm rides in the 10 days before to adapt."})

    # 7) Descending 'free speed': Grit + Flow (difficulty-normalized) + speed
    desc = m.get("descending")
    sk = m.get("mtb_skills")
    if desc:
        gritline = ""
        if sk and sk.get("grit_recent") is not None:
            gritline = f" (recently around Grit {sk['grit_recent']}, harder trails than earlier)" if (sk.get("grit_trend") or 0) > 5 else f" (Grit ~{sk['grit_recent']})"
        trend_txt = ""
        if desc.get("trend") is not None:
            if desc["trend"] > 0.2:
                trend_txt = " And it's trending the right way - he's getting smoother for the difficulty."
            elif desc["trend"] < -0.2:
                trend_txt = " It's slipped a little lately, likely because the trails got gnarlier."
        tone = "good" if desc["free_speed"] == "low" else ("watch" if desc["free_speed"] == "high" else "info")
        out.append({"title": f"Descending: {desc['free_speed']} free speed available", "tone": tone,
                    "text": f"Adjusting Flow for how hard the terrain is{gritline}, his descending skill scores {desc['skill_score']}/100 "
                            f"(50 is his own average){' at about ' + str(desc['avg_speed_mph']) + ' mph' if desc.get('avg_speed_mph') else ''}. "
                            f"{desc['note']}{trend_txt} For a rider this fit, descending is usually where the cheap seconds are."})

    # 7.5) Climbing: power + system weight physics
    clp = m.get("climbing_power")
    if clp and clp.get("system_lb") and clp.get("vam_ft_per_h"):
        per800 = round(clp["sec_per_lb_per_100ft"] * 8, 1)   # sec/lb over an 800 ft race climb
        heavy = clp["bike_lb"] >= 24
        out.append({"title": "Where power meets weight: climbing", "tone": "info",
                    "text": f"All-in he's {clp['system_lb']} lb (rider + {clp['bike_lb']} lb bike; the bike is {clp['bike_pct']}% of the system). "
                            f"At Critical Power that's {clp['cp_wkg_system']} W/kg of system weight, which climbs about {clp['vam_ft_per_h']:,} ft/hr - roughly {clp['sec_per_100ft']}s per 100 ft of climb. "
                            + (f"His {clp['bike_lb']} lb bike is on the heavier side for XC: every pound off it saves ~{clp['sec_per_lb_per_100ft']}s per 100 ft, so on a race with ~800 ft of climbing a 2 lb lighter bike is worth ~{round(per800*2)}s - real places in a tight field."
                               if heavy else
                               f"Every pound off the bike saves ~{clp['sec_per_lb_per_100ft']}s per 100 ft climbed.")})

    # 7.8) Short track: his best-fit discipline
    if does_short_track and cp and vi.get("season_median"):
        st_w = cp["predict"].get("short_track_25min")
        out.append({"title": "Short track is his event", "tone": "good",
                    "text": f"Short track (~20-30 min, all-out, constant surges) rewards exactly what he has: a {cp['w_prime_kj']} kJ anaerobic battery, strong 5-min power, and a punchy riding style (VI {vi['season_median']}). "
                            f"The CP model says he can hold about {st_w} W for a 25-min short track - higher than his longer-XC number, because the shorter the race the more of that battery he can spend. "
                            "Race it aggressively: fight for a front-row start, go hard off the line for clear air, then surge every rise and sprint each corner exit - repeatability is the weapon."})

    # 8) Race outlook: TSB + days to race + recovery + CP prediction
    if nr and cp:
        d = nr["days_out"]
        window = ("time to build then taper" if d > 14 else "into the taper now - hold intensity, cut volume")
        out.append({"title": f"Outlook for {nr['name']} ({d} days)", "tone": "info",
                    "text": f"He's carrying fitness of {pmc['ctl']} (CTL) with form at {pmc['tsb']}. With {d} days out there's {window}. "
                            f"The Critical Power model projects he can hold about {cp['predict']['45min']} W for a 45-minute race - build the plan around defending that number on the climbs."})

    return out[:10]


def readiness(pmc_series, recovery):
    """Junior-athlete readiness verdict: fresh / normal / fatigued / dig-a-hole."""
    if not pmc_series:
        return {"verdict": "unknown", "why": "No training data.", "actions": []}
    tsb = pmc_series[-1]["tsb"]
    ctl = pmc_series[-1]["ctl"]
    # ramp rate: CTL change over last 7 days
    ramp = None
    if len(pmc_series) > 7:
        ramp = round(ctl - pmc_series[-8]["ctl"], 1)

    why, actions, verdict = [], [], "On track"
    if tsb <= -25:
        verdict = "Needs recovery"
        why.append(f"He's carrying a lot of fatigue right now (form {tsb}) - normal right after a hard race, but it means he's not fresh.")
        actions.append("Take 1-2 genuine easy or rest days now.")
    elif tsb < -10:
        verdict = "Tired"
        why.append(f"Carrying some fatigue (form {tsb}) - a bit run down but not deep in the red.")
        actions.append("Keep the next hard day short; prioritise sleep.")
    elif tsb > 15:
        verdict = "Fresh"
        why.append(f"Well rested and fresh (form {tsb}).")
        actions.append("Good window for a hard session or a race.")
    else:
        why.append(f"Training load and freshness are in balance (form {tsb}).")

    if ramp is not None and ramp > RAMP_WARN:
        why.append(f"Fitness is ramping fast (+{ramp} CTL/wk) - above the junior-safe ~{RAMP_WARN}.")
        actions.append("Hold load steady this week rather than adding more.")

    # recovery overlay
    rec = recovery or {}
    if rec.get("rhr_trend") and rec["rhr_trend"] >= 3:
        why.append(f"Resting HR is trending up (+{rec['rhr_trend']} bpm) - a fatigue signal.")
        if verdict in ("On track", "Fresh"):
            verdict = "Watch recovery"
        actions.append("Back off if resting HR stays elevated.")
    if rec.get("hrv_trend") and rec["hrv_trend"] <= -5:
        why.append(f"HRV is down ({rec['hrv_trend']}) vs baseline.")
        actions.append("Favor easy riding until HRV recovers.")
    if rec.get("latest", {}).get("sleep_h") is not None and rec["latest"]["sleep_h"] < 8:
        why.append(f"Last night's sleep was {rec['latest']['sleep_h']}h - juniors need 8-10h.")
        actions.append("Protect sleep; it drives adaptation at his age.")

    if not actions:
        actions.append("Train as planned.")
    return {"verdict": verdict, "tsb": tsb, "ctl": ctl, "ramp": ramp,
            "why": why, "actions": actions}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def load_apple_health():
    p = os.path.join(DATA, "apple_health.json")
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    activities = json.load(open(os.path.join(DATA, "activities.json")))
    profile = json.load(open(os.path.join(DATA, "profile.json")))
    garmin_well = json.load(open(os.path.join(DATA, "wellness.json")))
    apple = load_apple_health()

    # filter out recording-error rides (zero/near-zero duration)
    rides_raw = [a for a in activities
                 if a.get("is_cycling") and (a.get("duration_s") or 0) >= 300]
    dropped = [a for a in activities
               if a.get("is_cycling") and (a.get("duration_s") or 0) < 300]

    weight = profile.get("weight_kg")

    # first pass: rough FTP from a provisional estimate, then refine
    prelim = []
    for a in rides_raw:
        prelim.append(ride_metrics(a, ftp=200))  # provisional FTP for TSS
    curve0 = power_curve(prelim)
    ftp, ftp_info = estimate_ftp(curve0)
    ftp = ftp or 200

    # second pass with real FTP so TSS/zones are correct
    rides = []
    for a in rides_raw:
        m = ride_metrics(a, ftp=ftp)
        # attach per-sample (watt, dt) for time-in-zone without re-reading streams
        s = a.get("streams") or {}
        power = s.get("power") or []
        dt = sample_dt(s)
        m["_power_samples"] = [(w, dt) for w in power if w is not None]
        rides.append(m)

    curve = power_curve(rides)
    rider_type, rider_note = classify_rider(curve, ftp)
    zones = power_zones(ftp)
    tiz = time_in_zone(rides, ftp)
    series = daily_tss_series(rides)
    pmc_series = pmc(series)
    weekly = weekly_tss(series)
    config = load_config()
    excluded = config.get("excluded_races", [])
    # prefer the user-set rider/bike weights (config) over Garmin's stored weight
    LB = 0.453592
    if config.get("rider_weight_lb"):
        weight = round(config["rider_weight_lb"] * LB, 2)
        profile["weight_kg"] = weight
    bike_kg = round(config["bike_weight_lb"] * LB, 2) if config.get("bike_weight_lb") else None
    recovery = recovery_block(apple, garmin_well)
    models = build_models(rides, rides_raw, weight, series, bike_kg=bike_kg, ftp=ftp)
    ready = readiness(pmc_series, recovery)
    rscore = recovery_score(pmc_series, recovery, models)
    brief = coaching_brief(pmc_series, ready, ftp, ftp_info, tiz, recovery, excluded)
    fuel = fueling(weight, pmc_series)
    nr = next_race(excluded)
    insights = synthesize(pmc_series, models, recovery, ftp, ftp_info, tiz, nr, weight,
                          does_short_track=config.get("does_short_track", False))
    weather_block = race_weather(nr)

    # max HR seen (for HR-zone context)
    max_hr = max((r["max_hr"] for r in rides if r["max_hr"]), default=None)

    # strip heavy fields before writing per-ride list to the dashboard
    ride_list = []
    for r in sorted(rides, key=lambda x: x["start"] or "", reverse=True):
        rr = {k: v for k, v in r.items() if k not in ("_power_samples",)}
        ride_list.append(rr)

    ftp_wkg = round(ftp / weight, 2) if (ftp and weight) else None

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "fitness": {
            "ftp": ftp, "ftp_wkg": ftp_wkg, "max_hr": max_hr,
            "ftp_info": ftp_info,
            "rider_type": rider_type, "rider_note": rider_note,
            "vo2max_cycling": profile.get("vo2max_cycling"),
        },
        "power_curve": [
            {"secs": d, "label": DURATION_LABELS[d],
             "watts": curve[d]["watts"],
             "wkg": round(curve[d]["watts"] / weight, 2) if (curve[d]["watts"] and weight) else None,
             "date": curve[d]["date"]}
            for d in DURATIONS
        ],
        "zones": zones,
        "time_in_zone": tiz,
        "pmc": pmc_series,
        "weekly_tss": weekly,
        "recovery": recovery,
        "readiness": ready,
        "recovery_score": rscore,
        "coaching": brief,
        "models": models,
        "insights": insights,
        "race_weather": weather_block,
        "fueling": fuel,
        "race_calendar": [{"date": d, "name": n, "series": s} for d, n, s in RACE_CALENDAR],
        "excluded_races": excluded,
        "race_intel": load_riders(),
        "rides": ride_list,
        "data_quality": {
            "total_rides": len(rides),
            "rides_with_power": sum(1 for r in rides if r["has_power"]),
            "rides_with_hr": sum(1 for r in rides if r["has_hr"]),
            "dropped_recording_errors": len(dropped),
            "date_range": [min((r["date"] for r in rides), default=None),
                           max((r["date"] for r in rides), default=None)],
            "recovery_source": recovery.get("source"),
        },
    }

    os.makedirs(os.path.join(HERE, "web"), exist_ok=True)
    with open(os.path.join(HERE, "web", "data.js"), "w") as f:
        f.write("window.PAUL_DATA = ")
        json.dump(out, f)
        f.write(";\n")
    # also a plain json for the coaching agent to read
    json.dump(out, open(os.path.join(DATA, "analysis.json"), "w"), indent=2)

    print(f"FTP {ftp} W ({ftp_wkg} W/kg) | type {rider_type} | "
          f"CTL {pmc_series[-1]['ctl']} TSB {pmc_series[-1]['tsb']} | "
          f"readiness: {ready['verdict']}")
    print(f"Recovery source: {recovery.get('source') or 'NONE (import Apple Health)'}")


if __name__ == "__main__":
    main()
