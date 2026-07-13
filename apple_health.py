"""
Import Paul's recovery data from Apple Health (his Apple Watch).

Garmin has none of Paul's sleep / resting-HR / HRV data - he only wears the
Garmin for rides. Apple Health has no live API, so recovery data comes in as an
export that gets dropped into the apple_health/ folder. This reads it and writes
data/apple_health.json in the shape analyze.py expects.

Two input formats are supported, newest file wins:

1. Official Apple Health export - Health app > profile > "Export All Health Data"
   produces export.zip containing export.xml. Drop the .zip (or the unzipped
   export.xml) into apple_health/. Universal but manual.

2. "Health Auto Export" app (recommended for the daily-at-noon automation) -
   set it to auto-write daily JSON to apple_health/. We parse its .json too.

Fields extracted per day: resting_hr (bpm), hrv (ms, SDNN), sleep_h (hours asleep).
"""

import glob
import json
import os
import zipfile
from collections import defaultdict
from datetime import datetime
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(HERE, "apple_health")
OUT = os.path.join(HERE, "data", "apple_health.json")

RHR_TYPE = "HKQuantityTypeIdentifierRestingHeartRate"
HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
}


def _parse_dt(s):
    # Apple format: "2026-07-12 06:30:00 -0400"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return None


def parse_export_xml(fileobj):
    """Stream-parse a (potentially huge) Apple Health export.xml."""
    rhr = defaultdict(list)
    hrv = defaultdict(list)
    sleep_secs = defaultdict(float)

    for _, elem in ET.iterparse(fileobj, events=("end",)):
        if elem.tag != "Record":
            continue
        rtype = elem.get("type")
        if rtype == RHR_TYPE:
            d = (elem.get("startDate") or "")[:10]
            try:
                rhr[d].append(float(elem.get("value")))
            except (TypeError, ValueError):
                pass
        elif rtype == HRV_TYPE:
            d = (elem.get("startDate") or "")[:10]
            try:
                hrv[d].append(float(elem.get("value")))
            except (TypeError, ValueError):
                pass
        elif rtype == SLEEP_TYPE and elem.get("value") in ASLEEP_VALUES:
            start = _parse_dt(elem.get("startDate"))
            end = _parse_dt(elem.get("endDate"))
            if start and end and end > start:
                # attribute the sleep block to the wake-up (end) date
                sleep_secs[end.date().isoformat()] += (end - start).total_seconds()
        elem.clear()

    return _assemble(rhr, hrv, sleep_secs)


def parse_auto_export_json(data):
    """
    Parse a JSON export into daily recovery records. Supports two shapes:

    A) 'Health Auto Export' app: {"data": {"metrics": [{"name","data":[{date,qty}]}]}}
    B) The simple iOS Shortcut format this project documents - a list (or {"days":[...]})
       of daily dicts: [{"date":"2026-07-13","resting_hr":48,"hrv":72,"sleep_h":7.5}, ...]
    """
    # shape B: a plain list of daily records, or {"days": [...]}
    records = None
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and isinstance(data.get("days"), list):
        records = data["days"]
    if records is not None:
        days = {}
        for r in records:
            d = (r.get("date") or "")[:10]
            if not d:
                continue
            rec = {}
            for k_out, keys in {"resting_hr": ("resting_hr", "restingHR", "rhr"),
                                "hrv": ("hrv", "hrv_sdnn", "HRV"),
                                "sleep_h": ("sleep_h", "sleep_hours", "sleep")}.items():
                for k in keys:
                    if r.get(k) is not None:
                        rec[k_out] = round(float(r[k]), 2)
                        break
            days[d] = rec
        return days

    # shape A: Health Auto Export metrics
    rhr = defaultdict(list)
    hrv = defaultdict(list)
    sleep_secs = defaultdict(float)

    metrics = []
    if isinstance(data, dict):
        metrics = (data.get("data") or {}).get("metrics") or data.get("metrics") or []
    for m in metrics:
        name = (m.get("name") or "").lower()
        for pt in m.get("data", []):
            d = (pt.get("date") or "")[:10]
            qty = pt.get("qty", pt.get("value"))
            if "resting" in name and "heart" in name and qty is not None:
                rhr[d].append(float(qty))
            elif "variability" in name or "hrv" in name:
                if qty is not None:
                    hrv[d].append(float(qty))
            elif "sleep" in name:
                # qty usually in hours for asleep metrics
                if qty is not None:
                    sleep_secs[d] += float(qty) * 3600
    return _assemble(rhr, hrv, sleep_secs)


def _assemble(rhr, hrv, sleep_secs):
    days = {}
    all_dates = set(rhr) | set(hrv) | set(sleep_secs)
    for d in all_dates:
        rec = {}
        if rhr.get(d):
            rec["resting_hr"] = round(sum(rhr[d]) / len(rhr[d]), 1)
        if hrv.get(d):
            rec["hrv"] = round(sum(hrv[d]) / len(hrv[d]), 1)
        if sleep_secs.get(d):
            rec["sleep_h"] = round(sleep_secs[d] / 3600, 2)
        days[d] = rec
    return days


def _config_icloud_dir():
    try:
        cfg = json.load(open(os.path.join(HERE, "config.json")))
        return cfg.get("apple_health_icloud_dir")
    except Exception:
        return None


def find_input():
    """
    Return (path, kind) of the newest usable export, searching the local inbox and
    (for the hands-off Shortcut) a configured iCloud Drive folder.
    """
    dirs = [INBOX]
    icloud = _config_icloud_dir()
    if icloud and os.path.isdir(os.path.expanduser(icloud)):
        dirs.append(os.path.expanduser(icloud))
    candidates = []
    for d in dirs:
        for pat, kind in [("*.zip", "zip"), ("export.xml", "xml"),
                          ("*.xml", "xml"), ("*.json", "json")]:
            for p in glob.glob(os.path.join(d, pat)):
                candidates.append((os.path.getmtime(p), p, kind))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, path, kind = candidates[0]
    return path, kind


def main():
    os.makedirs(INBOX, exist_ok=True)
    found = find_input()
    if not found:
        print(f"No Apple Health export found in {INBOX}/")
        print("Drop export.zip (Health app > Export All Health Data) there and re-run.")
        # leave any previous apple_health.json in place
        return

    path, kind = found
    print(f"Reading {os.path.basename(path)} ({kind})...")
    if kind == "zip":
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.endswith("export.xml")), None)
            if not name:
                print("  no export.xml inside zip")
                return
            with z.open(name) as f:
                days = parse_export_xml(f)
    elif kind == "xml":
        with open(path, "rb") as f:
            days = parse_export_xml(f)
    else:  # json
        days = parse_auto_export_json(json.load(open(path)))

    out = {"source": "apple_health", "imported_at": datetime.now().isoformat(timespec="seconds"),
           "source_file": os.path.basename(path), "days": days}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2)

    with_sleep = sum(1 for r in days.values() if r.get("sleep_h"))
    with_rhr = sum(1 for r in days.values() if r.get("resting_hr"))
    with_hrv = sum(1 for r in days.values() if r.get("hrv"))
    print(f"  {len(days)} days: {with_rhr} with RHR, {with_hrv} with HRV, {with_sleep} with sleep")


if __name__ == "__main__":
    main()
