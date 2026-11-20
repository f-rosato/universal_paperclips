#!/usr/bin/env python3
"""Playwright harness that lets an AI play Universal Paperclips over HTTP.

Opens the game (served by dev.py) in a visible Chromium window and exposes a
small JSON API on 127.0.0.1. Requests are handled one at a time.

Usage:
    python dev.py --no-open                    # in one terminal
    .venv/Scripts/python harness.py            # in another
    .venv/Scripts/python harness.py --url http://localhost:8100/ --port 8765

Endpoints (actions return only a short result; call /observe to see the game):
    GET  /observe[?text=1]      -> state (text=1 adds the full visible page text)
    POST /click {"id": "btnMakePaperclip", "times": 10}
                                -> {clicked, requested, stopped_reason}
    POST /wait  {"seconds": 5}  -> {waited}
    POST /set   {"id": "slider" | "investStrat" | "stratPicker", "value": ...}
                                -> {ok, id, value}

State shape:
    buttons:  [{id, label, enabled, kind: "button"|"project"}]  (visible only)
    values:   {spanId: text}                                     (visible only)
    controls: {slider: {value,min,max}, investStrat: {value, options}, ...}
    messages: [newest, ..., oldest]                              (console log)

Examples:
    curl localhost:8765/observe
    curl -X POST localhost:8765/click -d '{"id":"btnMakePaperclip","times":50}'
    curl -X POST localhost:8765/wait  -d '{"seconds":5}'
    curl -X POST localhost:8765/set   -d '{"id":"investStrat","value":"hi"}'
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

MAX_CLICKS = 1000
MAX_WAIT_S = 600
SETTABLE = ("slider", "investStrat", "stratPicker")

OBSERVE_JS = r"""
(withText) => {
  const vis = (el) => !!el && (el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null);
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const buttons = [];
  for (const b of document.querySelectorAll("button[id]")) {
    if (!vis(b)) continue;
    buttons.push({
      id: b.id,
      label: clean(b.innerText),
      enabled: !b.disabled,
      kind: b.classList.contains("projectButton") ? "project" : "button",
    });
  }
  const values = {};
  for (const s of document.querySelectorAll("span[id]")) {
    if (!vis(s) || s.id === "cursor" || /^readout\d$/.test(s.id)) continue;
    const t = clean(s.innerText);
    if (t !== "") values[s.id] = t;
  }
  const controls = {};
  const slider = document.getElementById("slider");
  if (vis(slider)) controls.slider = { value: +slider.value, min: +slider.min, max: +slider.max };
  for (const id of ["investStrat", "stratPicker"]) {
    const sel = document.getElementById(id);
    if (!vis(sel)) continue;
    controls[id] = {
      value: sel.value,
      options: [...sel.options].map((o) => ({ value: o.value, label: clean(o.text) })),
    };
  }
  const messages = [1, 2, 3, 4, 5]
    .map((i) => clean((document.getElementById("readout" + i) || {}).innerText))
    .filter((m) => m);
  const state = { buttons, values, controls, messages };
  if (withText) state.text = document.body.innerText;
  return state;
}
"""

BUTTON_STATUS_JS = r"""
(id) => {
  const el = document.getElementById(id);
  if (!el || el.tagName !== "BUTTON") return "not_found";
  if (!(el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null)) return "not_visible";
  if (el.disabled) return "disabled";
  return "ok";
}
"""

SET_SLIDER_JS = r"""
([id, v]) => {
  const el = document.getElementById(id);
  el.value = v;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return +el.value;
}
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Game:
    def __init__(self, page, click_delay_ms):
        self.page = page
        self.click_delay_ms = click_delay_ms

    def observe(self, with_text=False):
        return self.page.evaluate(OBSERVE_JS, with_text)

    def click(self, body):
        bid = body.get("id")
        if not isinstance(bid, str) or not bid:
            raise ApiError(400, "'id' (string) is required")
        times = body.get("times", 1)
        if not isinstance(times, int) or times < 1:
            raise ApiError(400, "'times' must be a positive integer")
        times = min(times, MAX_CLICKS)

        status = self.page.evaluate(BUTTON_STATUS_JS, bid)
        if status in ("not_found", "not_visible"):
            raise ApiError(404, f"button '{bid}' is {status.replace('_', ' ')}")

        loc = self.page.locator(f"[id='{bid}']")
        clicked, stopped = 0, None
        for i in range(times):
            if i > 0:
                status = self.page.evaluate(BUTTON_STATUS_JS, bid)
            if status != "ok":
                stopped = status
                break
            # force=True: we already checked visibility/enabled ourselves, and
            # thump.js's squish animation would otherwise fail the "stable" check.
            loc.click(force=True, no_wait_after=True, timeout=2000)
            clicked += 1
            if self.click_delay_ms and i < times - 1:
                self.page.wait_for_timeout(self.click_delay_ms)
        log(f"click {bid} x{clicked}/{times}" + (f" (stopped: {stopped})" if stopped else ""))
        return {"clicked": clicked, "requested": times, "stopped_reason": stopped}

    def wait(self, body):
        seconds = body.get("seconds")
        if not isinstance(seconds, (int, float)) or seconds < 0:
            raise ApiError(400, "'seconds' must be a non-negative number")
        seconds = min(float(seconds), MAX_WAIT_S)
        log(f"wait {seconds}s")
        self.page.wait_for_timeout(seconds * 1000)
        return {"waited": seconds}

    def set(self, body):
        cid, value = body.get("id"), body.get("value")
        if cid not in SETTABLE:
            raise ApiError(400, f"'id' must be one of {list(SETTABLE)}")
        ctrl = self.observe()["controls"].get(cid)
        if ctrl is None:
            raise ApiError(404, f"control '{cid}' is not visible yet")
        if cid == "slider":
            if not isinstance(value, (int, float)) or not ctrl["min"] <= value <= ctrl["max"]:
                raise ApiError(400, f"slider value must be a number in [{ctrl['min']}, {ctrl['max']}]")
            self.page.evaluate(SET_SLIDER_JS, [cid, value])
        else:
            valid = [o["value"] for o in ctrl["options"]]
            if value not in valid:
                raise ApiError(400, f"value for '{cid}' must be one of {valid}")
            self.page.select_option(f"#{cid}", value)
        log(f"set {cid} = {value!r}")
        return {"ok": True, "id": cid, "value": value}


def log(msg):
    print(f"[harness {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_handler(game):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n == 0:
                return {}
            try:
                data = json.loads(self.rfile.read(n))
            except json.JSONDecodeError as e:
                raise ApiError(400, f"invalid JSON: {e}")
            if not isinstance(data, dict):
                raise ApiError(400, "body must be a JSON object")
            return data

        def _dispatch(self, method):
            url = urlparse(self.path)
            try:
                if url.path == "/observe" and method in ("GET", "POST"):
                    q = parse_qs(url.query)
                    with_text = q.get("text", ["0"])[0] in ("1", "true")
                    if method == "POST":
                        with_text = bool(self._body().get("text", with_text))
                    self._send(200, game.observe(with_text))
                elif method == "POST" and url.path == "/click":
                    self._send(200, game.click(self._body()))
                elif method == "POST" and url.path == "/wait":
                    self._send(200, game.wait(self._body()))
                elif method == "POST" and url.path == "/set":
                    self._send(200, game.set(self._body()))
                else:
                    self._send(404, {"error": f"unknown endpoint {method} {url.path}"})
            except ApiError as e:
                log(f"error {e.status}: {e}")
                self._send(e.status, {"error": str(e)})
            except PlaywrightError as e:
                log(f"playwright error: {e}")
                self._send(500, {"error": f"browser error: {e}"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def log_message(self, fmt, *args):
            pass  # actions are logged by Game

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://localhost:8100/")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--click-delay-ms", type=int, default=20)
    ap.add_argument("--slow-mo", type=int, default=0, help="Playwright slow_mo in ms")
    args = ap.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=args.slow_mo)
        page = browser.new_page()
        try:
            page.goto(args.url)
        except PlaywrightError as e:
            raise SystemExit(f"[harness] cannot open {args.url} (is dev.py running?): {e}")
        page.wait_for_selector("#btnMakePaperclip")

        server = HTTPServer((args.host, args.port), make_handler(Game(page, args.click_delay_ms)))
        log(f"game at {args.url}; API on http://{args.host}:{args.port} (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log("stopped")
        finally:
            server.server_close()
            browser.close()


if __name__ == "__main__":
    main()
