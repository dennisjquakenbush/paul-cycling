"""
Thin Garmin Connect mobile-API client. Read-only.

All endpoints are the ones the Garmin Connect mobile app uses, reachable with the
DI bearer token from auth.py. On a 401 we force a token refresh once and retry.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import auth

BASE = "https://connectapi.garmin.com"
UA = "GCM-iOS-5.7.2.1"


def _get(path, token, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "User-Agent": UA,
        "Accept": "application/json",
        "NK": "NT",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if not raw:
            return None
        return json.loads(raw)


def get(path, params=None, _retried=False):
    """GET a Garmin endpoint, refreshing the token once on 401."""
    token = auth.get_token()
    try:
        return _get(path, token, params)
    except urllib.error.HTTPError as e:
        if e.code == 401 and not _retried:
            auth.get_token(force_refresh=True)
            return get(path, params, _retried=True)
        if e.code in (204, 404):
            return None
        raise
    except json.JSONDecodeError:
        return None


# ---- profile ---------------------------------------------------------------

def social_profile():
    return get("/userprofile-service/socialProfile")


def user_settings():
    return get("/userprofile-service/userprofile/user-settings")


# ---- activities ------------------------------------------------------------

def activities(start=0, limit=50):
    return get("/activitylist-service/activities/search/activities",
               {"start": start, "limit": limit}) or []


def activities_since(days_back, hard_cap=400):
    """Return every activity whose start is within the last `days_back` days."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    out, start, page = [], 0, 100
    while start < hard_cap:
        batch = activities(start, page)
        if not batch:
            break
        for a in batch:
            if (a.get("startTimeLocal") or "")[:10] >= cutoff:
                out.append(a)
        # activities come newest-first; stop once we pass the cutoff
        if (batch[-1].get("startTimeLocal") or "")[:10] < cutoff:
            break
        start += page
        time.sleep(0.3)
    return out


def activity_detail(activity_id):
    return get(f"/activity-service/activity/{activity_id}")


def activity_streams(activity_id):
    """
    Per-second-ish samples for an activity. Returns the 'metricDescriptors' +
    'activityDetailMetrics' structure Garmin uses; we normalise it in pull.py.
    """
    return get(f"/activity-service/activity/{activity_id}/details",
               {"maxChartSize": 4000, "maxPolylineSize": 0})


# ---- wellness (present only if Paul wears a Garmin watch; usually empty) ----

def daily_summary(gid, day):
    return get(f"/usersummary-service/usersummary/daily/{gid}",
               {"calendarDate": day})


def sleep(gid, day):
    return get(f"/wellness-service/wellness/dailySleepData/{gid}",
               {"date": day, "nonSleepBufferMinutes": 60})


def hrv(day):
    return get(f"/hrv-service/hrv/{day}")
