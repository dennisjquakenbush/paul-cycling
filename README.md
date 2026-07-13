# Paul's Cycling Dashboard

A web app that analyzes Paul Quakenbush's mountain-bike training and race prep for
the DINO and Indiana NICA series. It pulls his Garmin rides live, computes real
analytics from the raw power/HR streams, folds in Apple Watch recovery data, and
refreshes itself every day at noon. Pink/white theme inspired by his race jersey.

**Live (hosted):** https://dennisjquakenbush.github.io/paul-cycling/
**Local (always-on):** http://localhost:8777

> Note: the hosted site is a **public** GitHub Pages URL - his name and training
> data are visible to anyone with the link. Chosen deliberately; to make it private,
> switch the repo to private and install the app from the local server instead.

### Install it as an app (on his phone)
Open the live URL in Safari (iPhone) or Chrome (Android) -> Share / menu ->
**Add to Home Screen**. It installs as a standalone app with its own icon and works
offline (last-synced data) thanks to the service worker.

### Hosting / auto-publish
GitHub Pages deploys from `web/` via `.github/workflows/pages.yml` on every push.
The noon job commits the fresh `web/data.js` and pushes, so the hosted app updates
daily on its own (controlled by `config.json` -> `publish_to_github`).

---

## What it shows

- **Readiness** - fitness (CTL), fatigue (ATL), form (TSB) and a junior-safe verdict.
- **This week** - a day-by-day plan shaped by his form, ramp rate and the next race.
- **Fitness profile** - FTP (with an honest confidence range), W/kg, VO2max, rider type.
- **Power curve** - season best efforts from 5s to 60min, in watts and W/kg.
- **Training load** - CTL / ATL / TSB across the whole season.
- **Time in zone** and **weekly TSS**.
- **Recovery** - HRV, resting HR and sleep from Apple Health (once connected).
- **Fueling & hydration** - daily carb goals, during-ride carbs/fluid by duration, a
  full race-day plan and heat guidance, all scaled to his size and always generous.
- **Season calendar** - DINO into NICA, with countdowns. Each upcoming race has a
  **toggle**: switch off any race he is skipping and the next-race + taper advice
  updates to match (persists to `config.json`). Griffin Bike Park is off by default.
- **Recent rides** - normalized power, TSS, and HR:power decoupling per ride.
- **Tooltips** - hover the `?` next to any metric for a plain-English explanation.

## The key data-source facts

- **Garmin** has all his rides and power - pulled live. He has 100+ rides on file.
- **Garmin has none of his recovery data.** Paul records rides on a Garmin but wears
  an **Apple Watch** for sleep and heart rate, so resting HR / HRV / sleep live in
  Apple Health, which has no live API. That data comes in via an export (below).
- **FTP is data-limited.** His 5-min power is strong (5.0 W/kg) but he has never done
  a maximal 20-min effort, so the FTP number is a conservative range, not gospel.
  A 20-min or ramp test would sharpen every load and zone figure. This is flagged
  in the dashboard.

## Connecting Apple Health (recovery data) - hands-off

The goal is no daily uploading. Pick one:

**A. Free iOS Shortcut (recommended, no app, fully automatic)**
1. iPhone **Shortcuts** app -> Automation -> new **Personal Automation** -> **Time of Day, 8:00am, daily**.
2. Actions: **Find Health Samples** for Resting Heart Rate, HRV (SDNN) and Sleep;
   build a dictionary `{"date","resting_hr","hrv","sleep_h"}`; **Save File** to an
   iCloud Drive folder (e.g. `iCloud Drive/PaulHealth/health.json`).
3. Put that folder's Mac path in `config.json` -> `apple_health_icloud_dir`
   (usually `~/Library/Mobile Documents/com~apple~CloudDocs/PaulHealth`).
4. The noon job reads it automatically from then on. The importer accepts a single
   daily record or a list of daily records.

**B. Health Auto Export app** - point its daily JSON export at that same iCloud folder.

**C. One-off manual** - Health app -> profile -> **Export All Health Data** -> drop
`export.zip` into `apple_health/`.

Ask Claude to "set up the Apple Health shortcut for Paul" for the exact tap-by-tap recipe.

## The daily-at-noon automation

Two macOS `launchd` agents (in `~/Library/LaunchAgents/`), running independently of
Claude or any open app:

- `com.paul.cycling.daily` - at 12:00 every day: refresh the Garmin token, pull new
  rides, import any new Apple Health export, recompute everything, rebuild the dashboard.
- `com.paul.cycling.web` - keeps the dashboard served at http://localhost:8777.

Check / control them:

```bash
launchctl list | grep paul.cycling
launchctl kickstart -k gui/$(id -u)/com.paul.cycling.daily   # run the pipeline now
tail -f data/daily.log                                        # watch a run
```

To run everything manually any time: `./run.sh`

## Files

| File | Purpose |
|------|---------|
| `auth.py` | Garmin token load / auto-refresh (keeps the automation alive). |
| `garmin.py` | Read-only Garmin Connect mobile API client. |
| `pull.py` | Pull activities + power/HR streams + profile. |
| `apple_health.py` | Parse Apple Health export (zip/xml/json) into recovery data. |
| `analyze.py` | All the analytics -> `web/data.js`. |
| `daily.py` | The noon orchestrator (refresh -> pull -> import -> analyze). |
| `server.py` | Serves the dashboard + a small config API (race toggles) at :8777. |
| `config.json` | Settings: skipped races, Apple Health iCloud folder. |
| `web/` | The dashboard (static HTML/CSS/JS, no dependencies). |
| `data/` | Cached Garmin + computed data + logs. |

## Deep AI coaching analysis

The dashboard's analysis is deterministic (fast, always works, no AI cost). For the
full narrative coaching review - pacing plans, Strava comparisons, race-day fueling
and weather - paste the coaching prompt (the brief this project was built from) into
a Claude session; it reads the same fresh data in `data/analysis.json`.

## Notes / limits

- The Garmin token auto-refreshes. If it ever fully expires (long gap), Paul re-auths
  in whatever app created `~/.garminconnect/garmin_tokens.json`.
- All analysis is informational - not a substitute for a coach or medical advice.
- Treats Paul as a 16-year-old junior throughout: conservative ramp flags, heavy
  weight on sleep/recovery, no restrictive fueling.
