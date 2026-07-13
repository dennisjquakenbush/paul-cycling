"""
Daily pipeline - run by launchd every day at noon (and any time by ./run.sh).

Steps, each isolated so one failure doesn't sink the rest:
  1. refresh the Garmin token (keeps the automation alive indefinitely)
  2. pull Garmin activities + streams + profile
  3. import any new Apple Health export dropped in apple_health/
  4. re-run the analysis and regenerate the dashboard (web/data.js)

Everything is logged to data/daily.log with timestamps.
"""

import subprocess
import sys
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "daily.log")


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def step(name, fn):
    try:
        log(f"START {name}")
        fn()
        log(f"OK    {name}")
        return True
    except Exception as e:
        log(f"FAIL  {name}: {e!r}")
        return False


def run_module(mod):
    """Run a sibling module as a subprocess so its logging/stdout is captured."""
    res = subprocess.run([sys.executable, os.path.join(HERE, mod)],
                         capture_output=True, text=True, cwd=HERE, timeout=1200)
    for line in (res.stdout or "").splitlines():
        log(f"  {mod}: {line}")
    if res.returncode != 0:
        for line in (res.stderr or "").splitlines()[-8:]:
            log(f"  {mod}[err]: {line}")
        raise RuntimeError(f"{mod} exited {res.returncode}")


def publish():
    """Commit the refreshed dashboard data and push, so the hosted site updates.
    Enabled by config.json -> "publish_to_github": true. Best-effort."""
    import json
    try:
        cfg = json.load(open(os.path.join(HERE, "config.json")))
    except Exception:
        cfg = {}
    if not cfg.get("publish_to_github"):
        log("  publish: disabled (set config.publish_to_github=true to enable)")
        return
    # only commit if data.js actually changed
    subprocess.run(["git", "add", "web/data.js", "config.json"], cwd=HERE, check=False)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE)
    if diff.returncode == 0:
        log("  publish: no changes to push")
        return
    day = datetime.now().strftime("%Y-%m-%d")
    subprocess.run(["git", "commit", "-q", "-m", f"data: daily refresh {day}"], cwd=HERE, check=False)
    r = subprocess.run(["git", "push", "-q"], cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "git push failed").strip()[:200])
    log("  publish: pushed; GitHub Pages will redeploy")


def main():
    log("===== daily run start =====")
    import auth
    ok = True
    ok &= step("refresh Garmin token", lambda: auth.get_token(force_refresh=True))
    ok &= step("pull Garmin", lambda: run_module("pull.py"))
    # Apple Health is best-effort: no export present is not a failure
    step("import Apple Health", lambda: run_module("apple_health.py"))
    ok &= step("analyze", lambda: run_module("analyze.py"))
    # publishing is best-effort; a push failure should not fail the whole run
    step("publish to GitHub", publish)
    log(f"===== daily run {'complete' if ok else 'completed WITH ERRORS'} =====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
