"""
Dashboard server. Serves the static web/ dashboard AND a tiny config API so the
dashboard can persist settings (which races Paul is skipping) to config.json - read
by both the dashboard and the daily noon job.

  GET  /api/config          -> current config.json
  POST /api/config          -> merge JSON body into config.json, return the result

Runs always-on under launchd at http://localhost:8777.
"""

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
CONFIG = os.path.join(HERE, "config.json")
PORT = 8777

DEFAULT_CONFIG = {"excluded_races": [], "apple_health_icloud_dir": None}


def load_config():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    return {**DEFAULT_CONFIG, **cfg}


def save_config(cfg):
    merged = {**load_config(), **cfg}
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, indent=2)
    os.replace(tmp, CONFIG)
    return merged


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/config":
            return self._json(load_config())
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] == "/api/config":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("body must be an object")
                # only allow known keys
                allowed = {k: data[k] for k in DEFAULT_CONFIG if k in data}
                return self._json(save_config(allowed))
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        self.send_error(404)

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    os.chdir(WEB)
    print(f"Serving dashboard + config API at http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
