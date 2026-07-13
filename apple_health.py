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


import re

# lenient extractors for a single daily record written by the iOS Shortcut, even
# when its JSON is slightly malformed (empty sleep field, stray trailing text).
_FIELD_RE = {
    "resting_hr": re.compile(r'"?resting_hr"?\s*[:=]\s*(\d+(?:\.\d+)?)'),
    "hrv": re.compile(r'"?hrv"?\s*[:=]\s*(\d+(?:\.\d+)?)'),
    "sleep_h": re.compile(r'"?sleep_h"?\s*[:=]\s*(\d+(?:\.\d+)?)'),
}
_DATE_RE = re.compile(r'"?date"?\s*[:=]\s*"?(\d{4}-\d{2}-\d{2})')


def parse_loose_text(text):
    """Best-effort parse of a Shortcut's daily file: try JSON, else regex."""
    text = text.strip()
    # first try clean JSON (single object or list)
    try:
        return parse_auto_export_json(json.loads(text))
    except Exception:
        pass
    # fall back to regex extraction of one day's fields
    dm = _DATE_RE.search(text)
    if not dm:
        return {}
    rec = {}
    for field, rx in _FIELD_RE.items():
        m = rx.search(text)
        if m:
            rec[field] = round(float(m.group(1)), 2)
    return {dm.group(1): rec}


def gather_files():
    """All candidate files across the inbox and the configured iCloud folder."""
    dirs = [INBOX]
    icloud = _config_icloud_dir()
    if icloud and os.path.isdir(os.path.expanduser(icloud)):
        dirs.append(os.path.expanduser(icloud))
    exports, dailies = [], []   # (mtime, path)
    for d in dirs:
        for p in glob.glob(os.path.join(d, "*")):
            low = p.lower()
            if low.endswith(".zip") or low.endswith(".xml"):
                exports.append((os.path.getmtime(p), p))
            elif low.endswith(".json") or low.endswith(".txt"):
                dailies.append((os.path.getmtime(p), p))
    exports.sort(reverse=True)
    dailies.sort()
    return exports, dailies


def merge_day(days, date, rec):
    """Field-wise merge so a daily file never wipes a value from a full export."""
    if not rec:
        return
    cur = days.setdefault(date, {})
    for k, v in rec.items():
        if v is not None:
            cur[k] = v


def main():
    os.makedirs(INBOX, exist_ok=True)
    exports, dailies = gather_files()
    if not exports and not dailies:
        icloud = _config_icloud_dir()
        print(f"No Apple Health data found in {INBOX}/" + (f" or {icloud}/" if icloud else ""))
        print("Set up the iOS Shortcut / drop export.zip, then re-run.")
        return  # leave any previous apple_health.json in place

    # start cumulative: keep whatever we imported before, so history survives even
    # if old daily files get cleaned out of the folder
    days = {}
    if os.path.exists(OUT):
        try:
            days = dict(json.load(open(OUT)).get("days", {}))
        except Exception:
            days = {}

    # newest full export contributes bulk history (sleep included)
    if exports:
        _, path = exports[0]
        print(f"Reading full export {os.path.basename(path)}...")
        try:
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as z:
                    name = next((n for n in z.namelist() if n.endswith("export.xml")), None)
                    parsed = parse_export_xml(z.open(name)) if name else {}
            else:
                parsed = parse_export_xml(open(path, "rb"))
            for d, rec in parsed.items():
                merge_day(days, d, rec)
        except Exception as e:
            print(f"  export parse failed: {e}")

    # every daily Shortcut file (oldest first, so newest wins on conflicts)
    n_daily = 0
    for _, path in dailies:
        try:
            parsed = parse_loose_text(open(path, encoding="utf-8", errors="ignore").read())
            for d, rec in parsed.items():
                merge_day(days, d, rec)
            n_daily += 1
        except Exception as e:
            print(f"  skip {os.path.basename(path)}: {e}")

    out = {"source": "apple_health", "imported_at": datetime.now().isoformat(timespec="seconds"),
           "days": days}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2)

    with_sleep = sum(1 for r in days.values() if r.get("sleep_h"))
    with_rhr = sum(1 for r in days.values() if r.get("resting_hr"))
    with_hrv = sum(1 for r in days.values() if r.get("hrv"))
    print(f"  {len(days)} days total ({n_daily} daily files): "
          f"{with_rhr} with RHR, {with_hrv} with HRV, {with_sleep} with sleep")


if __name__ == "__main__":
    main()
