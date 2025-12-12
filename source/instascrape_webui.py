"""Minimal web UI for instagram_scraper-0.9.12.py.

- Runs locally and opens in a browser tab (Chrome/Safari/etc.).
- Uses the existing scraper as the backend via dynamic import.
- Streams logs and supports stop.

This is intentionally small and dependency-light (Flask only).
"""

import contextlib
import importlib.util
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Optional

from flask import Flask, Response, redirect, render_template_string, request, url_for

# NOTE: The scraper backend is loaded via a dynamic import, which PyInstaller
# cannot automatically analyze for dependencies. Import these here so they are
# bundled into the .app/.exe.
try:
    import requests  # noqa: F401
    import selenium  # noqa: F401
    import webdriver_manager  # noqa: F401
except Exception:
    # In source runs, these should exist once requirements are installed.
    # In frozen runs, missing modules will surface as a job error.
    pass


def _bundle_base_dir() -> str:
    # PyInstaller sets `sys.frozen` and provides extracted resources in `sys._MEIPASS`.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return str(getattr(sys, "_MEIPASS"))
    return os.path.dirname(os.path.abspath(__file__))


HERE = _bundle_base_dir()
SCRAPER_PATH = os.path.join(HERE, "instagram_scraper-0.9.12.py")


def load_scraper_module(path: str = SCRAPER_PATH):
    spec = importlib.util.spec_from_file_location("insta_scraper_module", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load scraper module from: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@dataclass
class JobState:
    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = threading.Event()
    log_queue: "queue.Queue[str]" = queue.Queue()
    running: bool = False
    last_error: Optional[str] = None
    last_collection_url: str = ""
    last_save_dir: str = ""
    last_start_post: str = "1"
    last_end_post: str = ""


STATE = JobState()
app = Flask(__name__)


INDEX_HTML = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <title>InstaScrape Web UI</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; }
      label { display:block; margin-top: 10px; }
      input { width: 100%; padding: 8px; box-sizing: border-box; }
      .row { display:flex; gap:12px; }
      .row > div { flex:1; }
      pre { height: 45vh; overflow:auto; background: #111; color:#eee; padding:12px; border-radius: 8px; }
      button { padding: 10px 14px; margin-top: 12px; }
      .muted { color:#666; }
    </style>
  </head>
  <body>
    <h2>Instagram Collection Scraper</h2>
    <div class=\"muted\">Runs locally. Keep the browser open while scraping.</div>
        <div class=\"muted\">Status: <b id=\"status\">{{ 'running' if running else 'idle' }}</b></div>
        {% if last_error %}
            <div style=\"margin-top:10px; color:#b00020;\">Last error: {{ last_error }}</div>
        {% endif %}

        <form id=\"startForm\" method=\"post\" action=\"{{ url_for('start') }}\">
      <label>Sessionid cookie</label>
      <input name=\"sessionid\" placeholder=\"sessionid\" required />

      <label>Saved Collection URL</label>
    <input name=\"collection_url\" value=\"{{ last_collection_url }}\" placeholder=\"https://www.instagram.com/.../saved/...\" required />

      <label>Save directory</label>
    <input name=\"save_dir\" value=\"{{ last_save_dir or default_save_dir }}\" required />

      <div class=\"row\">
        <div>
          <label>Start post #</label>
                    <input name=\"start_post\" value=\"{{ last_start_post or '1' }}\" />
        </div>
        <div>
          <label>End post #</label>
                    <input name=\"end_post\" value=\"{{ last_end_post }}\" placeholder=\"leave blank for all\" />
        </div>
      </div>

            <button id=\"startBtn\" type=\"submit\" {{ 'disabled' if running else '' }}>Start</button>
            <button id=\"stopBtn\" type=\"button\" {{ '' if running else 'disabled' }}>Stop</button>
    </form>

    <h3>Logs</h3>
    <pre id=\"log\"></pre>

    <script>
      const log = document.getElementById('log');
            const statusEl = document.getElementById('status');
            const startBtn = document.getElementById('startBtn');
            const stopBtn = document.getElementById('stopBtn');
            const startForm = document.getElementById('startForm');

            function appendLine(line) {
                log.textContent += line + "\\n";
                log.scrollTop = log.scrollHeight;
            }

            function setRunning(running) {
                statusEl.textContent = running ? 'running' : 'idle';
                startBtn.disabled = !!running;
                stopBtn.disabled = !running;
            }

            function startPolling() {
                appendLine('[log stream fallback: polling]');
                setInterval(async () => {
                    try {
                        const res = await fetch('{{ url_for('logs_poll') }}');
                        if (!res.ok) return;
                        const payload = await res.json();
                        (payload.lines || []).forEach(appendLine);
                        setRunning(!!payload.running);
                    } catch (e) {
                        // ignore
                    }
                }, 1000);
            }

            // Lightweight status-only poll (does not consume log lines).
            setInterval(async () => {
                try {
                    const res = await fetch('{{ url_for('logs_poll') }}?drain=0');
                    if (!res.ok) return;
                    const payload = await res.json();
                    setRunning(!!payload.running);
                } catch (e) {
                    // ignore
                }
            }, 1000);

            if (window.EventSource) {
                const es = new EventSource('{{ url_for('logs') }}');
                es.onmessage = (ev) => {
                    appendLine(ev.data);
                    if (ev.data === '[done]') {
                        setRunning(false);
                    }
                };
                es.onerror = () => {
                    try { es.close(); } catch (e) {}
                    appendLine('[log stream error: EventSource disconnected]');
                    startPolling();
                };
            } else {
                startPolling();
            }

            stopBtn.addEventListener('click', async () => {
                try {
                    await fetch('{{ url_for('stop') }}', {method:'POST'});
                } catch (e) {
                    // ignore
                }
            });

            // Submit without page reload so the user can re-run jobs
            // without re-entering sessionid/URL.
            startForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                try {
                    const fd = new FormData(startForm);
                    const res = await fetch('{{ url_for('start') }}', {method:'POST', body: fd});
                    if (!res.ok) {
                        appendLine(`[start failed] HTTP ${res.status}`);
                        return;
                    }
                    const payload = await res.json();
                    if (payload && payload.ok) {
                        setRunning(true);
                    } else {
                        appendLine('[start failed]');
                    }
                } catch (err) {
                    appendLine(`[start error] ${err}`);
                }
            });
    </script>
  </body>
</html>
"""


class QueueWriter:
    def __init__(self, q: "queue.Queue[str]", stop_event: threading.Event):
        self.q = q
        self.stop_event = stop_event

    def write(self, s: str):
        if self.stop_event.is_set():
            return
        for line in s.splitlines():
            if line.strip() == "":
                continue
            self.q.put(line)

    def flush(self):
        return


def run_scrape_job(sessionid: str, collection_url: str, save_dir: str, start_post: int, end_post: float):
    STATE.running = True
    STATE.last_error = None

    done_event = threading.Event()

    try:
        mod = load_scraper_module()

        # Expand and ensure folder exists
        save_dir = os.path.expanduser(save_dir)
        os.makedirs(save_dir, exist_ok=True)

        # Redirect scraper prints into queue
        qw = QueueWriter(STATE.log_queue, STATE.stop_event)
        with contextlib.redirect_stdout(qw), contextlib.redirect_stderr(qw):
            print("[job started]")
            scraper = mod.InstagramScraper(sessionid, collection_url, save_dir, start_post, end_post)

            # Allow stopping from UI: set the scraper's internal stop flag too.
            # (This mirrors ESC behavior.)
            def sync_stop():
                while not STATE.stop_event.is_set() and not done_event.is_set():
                    time.sleep(0.2)
                if done_event.is_set():
                    return
                try:
                    scraper._escape_stopper.stop_event.set()  # noqa: SLF001
                except Exception:
                    pass

            stopper_thread = threading.Thread(target=sync_stop, daemon=True)
            stopper_thread.start()

            scraper.run()

    except Exception as e:
        STATE.last_error = str(e)
        STATE.log_queue.put(f"ERROR: {e}")
    finally:
        done_event.set()
        STATE.running = False
        STATE.log_queue.put("[done]")


@app.get("/")
def index():
    default_save_dir = os.path.expanduser("~/Downloads/InstaScraped")
    return render_template_string(
        INDEX_HTML,
        running=STATE.running,
        last_error=STATE.last_error,
        default_save_dir=default_save_dir,
        last_collection_url=STATE.last_collection_url,
        last_save_dir=STATE.last_save_dir,
        last_start_post=STATE.last_start_post,
        last_end_post=STATE.last_end_post,
    )


@app.post("/start")
def start():
    if STATE.running:
        return {"ok": False, "error": "already running"}, 409

    sessionid = (request.form.get("sessionid") or "").strip()
    collection_url = (request.form.get("collection_url") or "").strip()
    save_dir = (request.form.get("save_dir") or "").strip()

    STATE.last_collection_url = collection_url
    STATE.last_save_dir = save_dir

    try:
        start_post = int((request.form.get("start_post") or "1").strip())
    except ValueError:
        start_post = 1

    STATE.last_start_post = str(start_post)

    end_post_raw = (request.form.get("end_post") or "").strip()
    STATE.last_end_post = end_post_raw
    if end_post_raw == "":
        end_post = float("inf")
    else:
        try:
            end_post = int(end_post_raw)
        except ValueError:
            end_post = float("inf")

    if not sessionid or not collection_url or not save_dir:
        return {"ok": False, "error": "missing fields"}, 400

    STATE.stop_event.clear()
    STATE.log_queue.put("[queued]")
    STATE.thread = threading.Thread(
        target=run_scrape_job,
        args=(sessionid, collection_url, save_dir, start_post, end_post),
        daemon=True,
    )
    STATE.thread.start()

    return {"ok": True}


@app.post("/stop")
def stop():
    if STATE.running:
        STATE.log_queue.put("[stop requested]")
        STATE.stop_event.set()
    return ("", 204)


@app.get("/logs")
def logs():
    def event_stream():
        # Send a keepalive so the connection stays open.
        while True:
            try:
                line = STATE.log_queue.get(timeout=1.0)
                yield f"data: {line.replace(chr(13), '')}\n\n"
            except queue.Empty:
                # Comment events are ignored by EventSource but keep the connection alive.
                yield ": keepalive\n\n"

    resp = Response(event_stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.get("/logs_poll")
def logs_poll():
    drain = (request.args.get("drain") or "1").strip() not in ("0", "false", "False")

    lines = []
    if drain:
        for _ in range(200):
            try:
                lines.append(STATE.log_queue.get_nowait())
            except queue.Empty:
                break

    return {"lines": lines, "running": STATE.running, "last_error": STATE.last_error}


def _open_in_chrome(url: str) -> bool:
    if sys.platform != "darwin":
        return False

    for app_name in ("Google Chrome", "Google Chrome Canary", "Chromium"):
        try:
            r = subprocess.run(
                ["open", "-a", app_name, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _pick_listen_port(preferred: int = 5055, max_tries: int = 25) -> int:
    env_port = os.environ.get("INSTASCRAPE_PORT")
    if env_port:
        try:
            preferred = int(env_port)
        except ValueError:
            pass

    for port in range(preferred, preferred + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port

    raise RuntimeError("Could not find a free localhost port")


def main():
    port = _pick_listen_port(5055)
    url = f"http://127.0.0.1:{port}/"
    print(f"Starting web UI at {url}")
    try:
        if not _open_in_chrome(url):
            webbrowser.open(url)
    except Exception:
        pass
    try:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
