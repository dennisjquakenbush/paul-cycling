"""
Pull Paul's Garmin data and cache it locally as a season dataset.

Writes:
  data/profile.json      - weight, birthdate, VO2max, thresholds
  data/activities.json   - list of activities; cycling rides include time-series
                           streams (power / HR / cadence / speed / elevation)
  data/wellness.json      - Garmin daily wellness (usually empty; Paul's recovery
                           data lives in Apple Health, imported separately)

Streams are stored so analyze.py can own all the maths. Rides are downsampled to
~1 sample / 2s to keep the JSON small without hurting power-curve accuracy.
"""

import json
import os
import time
from datetime import date, datetime

import garmin

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# How much history to pull. The Indiana season runs May-Oct; 200 days covers it
# plus the winter base that precedes it.
DAYS_BACK = 200

CYCLING_TYPES = {
    "cycling", "mountain_biking", "road_biking", "gravel_cycling",
    "virtual_ride", "indoor_cycling", "cyclocross", "e_bike_fitness",
}

# metric key -> the short name we store it under
STREAM_KEYS = {
    "directPower": "power",
    "directHeartRate": "hr",
    "directBikeCadence": "cad",
    "directSpeed": "speed",
    "directElevation": "alt",
    "sumDistance": "dist",
    "sumElapsedDuration": "elapsed",
}


def _iso(ts_ms):
    return datetime.utcfromtimestamp(ts_ms / 1000).isoformat()


def extract_streams(detail_metrics):
    """Turn Garmin's metricDescriptors/activityDetailMetrics into named arrays."""
    descs = detail_metrics.get("metricDescriptors", [])
    idx = {}
    for d in descs:
        key = d.get("key")
        if key in STREAM_KEYS:
            idx[STREAM_KEYS[key]] = d.get("metricsIndex")
    rows = detail_metrics.get("activityDetailMetrics", [])

    # downsample to keep files small (~1 pt / 2s for long rides)
    step = 1 if len(rows) <= 2500 else max(1, len(rows) // 2500)

    out = {name: [] for name in idx}
    for i in range(0, len(rows), step):
        metrics = rows[i].get("metrics", [])
        for name, mi in idx.items():
            v = metrics[mi] if mi is not None and mi < len(metrics) else None
            out[name].append(v)
    return out


def pull_profile():
    prof = garmin.social_profile() or {}
    settings = garmin.user_settings() or {}
    ud = settings.get("userData", {})
    weight_g = ud.get("weight")
    profile = {
        "gid": prof.get("displayName"),
        "full_name": prof.get("fullName"),
        "birth_date": ud.get("birthDate"),
        "weight_kg": round(weight_g / 1000, 1) if weight_g else None,
        "height_cm": ud.get("height"),
        "vo2max_cycling": ud.get("vo2MaxCycling"),
        "gender": ud.get("gender"),
        "measurement_system": ud.get("measurementSystem"),
        "pulled_at": datetime.now().isoformat(timespec="seconds"),
    }
    if profile["birth_date"]:
        b = datetime.fromisoformat(profile["birth_date"]).date()
        today = date.today()
        profile["age"] = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return profile


def pull_activities():
    acts = garmin.activities_since(DAYS_BACK)
    print(f"  {len(acts)} activities in last {DAYS_BACK} days")
    out = []
    for a in acts:
        tkey = (a.get("activityType") or {}).get("typeKey", "")
        summ = {
            "id": a.get("activityId"),
            "name": a.get("activityName"),
            "type": tkey,
            "start": a.get("startTimeLocal"),
            "duration_s": a.get("duration"),
            "moving_s": a.get("movingDuration"),
            "distance_m": a.get("distance"),
            "elev_gain_m": a.get("elevationGain"),
            "avg_power": a.get("averagePower"),
            "max_power": a.get("maxPower"),
            "norm_power": a.get("normPower"),
            "avg_hr": a.get("averageHR"),
            "max_hr": a.get("maxHR"),
            "avg_cad": a.get("averageBikingCadenceInRevPerMinute"),
            "max_cad": a.get("maxBikingCadenceInRevPerMinute"),
            "avg_speed_mps": a.get("averageSpeed"),
            "calories": a.get("calories"),
            "tss_garmin": a.get("trainingStressScore"),
            "if_garmin": a.get("intensityFactor"),
            "np_garmin": a.get("normPower"),
            "location": a.get("locationName"),
            "is_cycling": tkey in CYCLING_TYPES,
            # MTB-specific + environment, straight from Garmin's summary
            "grit": a.get("grit"),
            "flow": a.get("avgFlow"),
            "elev_loss_m": a.get("elevationLoss"),
            "min_temp_c": a.get("minTemperature"),
            "max_temp_c": a.get("maxTemperature"),
            # Garmin's own best mean-maximal power at standard durations (seconds)
            "mmp": {str(s): a.get(f"maxAvgPower_{s}")
                    for s in (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200)
                    if a.get(f"maxAvgPower_{s}") is not None},
        }
        if summ["is_cycling"]:
            try:
                det = garmin.activity_streams(a.get("activityId"))
                if det:
                    summ["streams"] = extract_streams(det)
                time.sleep(0.4)
            except Exception as e:
                print(f"    stream pull failed for {a.get('activityId')}: {e}")
        out.append(summ)
    return out


def pull_wellness(gid):
    """Best-effort Garmin wellness. Usually empty for Paul (Apple Watch user)."""
    from datetime import timedelta
    days = {}
    have_any = False
    for i in range(60):
        day = (date.today() - timedelta(days=i)).isoformat()
        try:
            ds = garmin.daily_summary(gid, day) or {}
            rec = {
                "resting_hr": ds.get("restingHeartRate"),
                "steps": ds.get("totalSteps"),
                "sleep_h": None,
            }
            sl = garmin.sleep(gid, day) or {}
            secs = (sl.get("dailySleepDTO") or {}).get("sleepTimeSeconds")
            if secs:
                rec["sleep_h"] = round(secs / 3600, 2)
            if rec["resting_hr"] or rec["sleep_h"]:
                have_any = True
            days[day] = rec
        except Exception:
            pass
        time.sleep(0.1)
    return {"source": "garmin", "has_data": have_any, "days": days}


def main():
    os.makedirs(DATA, exist_ok=True)
    print("Pulling profile...")
    profile = pull_profile()
    json.dump(profile, open(os.path.join(DATA, "profile.json"), "w"), indent=2)
    print(f"  {profile['full_name']}, age {profile.get('age')}, {profile['weight_kg']} kg")

    print("Pulling activities + streams...")
    acts = pull_activities()
    json.dump(acts, open(os.path.join(DATA, "activities.json"), "w"))
    rides = [a for a in acts if a["is_cycling"]]
    print(f"  stored {len(acts)} activities ({len(rides)} rides)")

    print("Pulling Garmin wellness (recovery data usually lives in Apple Health)...")
    well = pull_wellness(profile["gid"])
    json.dump(well, open(os.path.join(DATA, "wellness.json"), "w"), indent=2)
    print(f"  Garmin wellness has_data={well['has_data']}")

    print("Done.")


if __name__ == "__main__":
    main()
