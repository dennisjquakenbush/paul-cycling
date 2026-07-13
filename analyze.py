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


def recovery_score(pmc_series, recovery):
    """
    A single 0-100 recovery estimate (the Tour-de-France-style dial), blending
    training form with body signals:
      - Form (TSB) is the backbone - how much training fatigue he's carrying.
      - HRV and resting HR nudge it up or down (trend vs baseline if we have enough
        history, otherwise their absolute quality).
      - Sleep trims it when short.
    Junior-safe: leans conservative when signals conflict.
    """
    if not pmc_series:
        return None
    tsb = pmc_series[-1]["tsb"]
    rec = recovery or {}

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
    recovery = recovery_block(apple, garmin_well)
    ready = readiness(pmc_series, recovery)
    rscore = recovery_score(pmc_series, recovery)
    brief = coaching_brief(pmc_series, ready, ftp, ftp_info, tiz, recovery, excluded)
    fuel = fueling(weight, pmc_series)

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
