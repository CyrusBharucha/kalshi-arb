"""
scripts/keep_alive.py
=====================
Drive a real headless Chromium to keep the Kalshi Arb Streamlit Cloud app
from hibernating.

Plain HTTP pings don't count as traffic: Streamlit only registers a session
once a browser opens the WebSocket at /_stcore/stream. This script opens a
real headless browser session, wakes the app if hibernated, and holds the
session open long enough to count.

Run via scheduled task every 20 minutes (KalshiKeepAlive task).
"""
from __future__ import annotations

import os
import re
import sys
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

APP_URL = os.environ.get(
    "KALSHI_APP_URL",
    "https://kalshi-arb-tva5pkwy8zavn4qjcwrg9c.streamlit.app/",
)

WAKE_BUTTON        = re.compile(r"get this app back up", re.IGNORECASE)
APP_ROOT           = '[data-testid="stApp"]'
PAGE_LOAD_TIMEOUT  = 90_000
INITIAL_SECONDS    = 20
BOOT_SECONDS       = 240
RELOAD_SECONDS     = 120
SESSION_HOLD_MS    = 30_000   # hold WS open so Streamlit counts the session


def _find_in_frames(page, selector):
    for frame in page.frames:
        try:
            if frame.locator(selector).count() > 0:
                return frame
        except PlaywrightError:
            continue
    return None


def _poll_frames(page, selector, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        frame = _find_in_frames(page, selector)
        if frame:
            return frame
        page.wait_for_timeout(2_000)
    return None


def _try_wake(page):
    for frame in page.frames:
        try:
            btn = frame.get_by_role("button", name=WAKE_BUTTON)
            if btn.count() > 0:
                btn.first.click(timeout=5_000)
                print("Clicked wake button.")
                return
        except PlaywrightError:
            continue
    for frame in page.frames:
        try:
            btns = frame.locator("button")
            if btns.count() == 1:
                label = (btns.first.inner_text(timeout=2_000) or "").strip()
                btns.first.click(timeout=5_000)
                print(f"Clicked only button: {label!r}")
                return
        except PlaywrightError:
            continue
    print("No wake button found — app presumably still booting.")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page    = browser.new_page()

        try:
            page.goto(APP_URL, wait_until="domcontentloaded",
                      timeout=PAGE_LOAD_TIMEOUT)
        except PlaywrightTimeout:
            print(f"FAIL: {APP_URL} did not load within "
                  f"{PAGE_LOAD_TIMEOUT // 1000}s", file=sys.stderr)
            browser.close()
            return 1

        frame = _poll_frames(page, APP_ROOT, INITIAL_SECONDS)

        if frame is None:
            _try_wake(page)
            frame = _poll_frames(page, APP_ROOT, BOOT_SECONDS)

        if frame is None:
            print("Reloading once...", file=sys.stderr)
            try:
                page.reload(wait_until="domcontentloaded",
                            timeout=PAGE_LOAD_TIMEOUT)
                frame = _poll_frames(page, APP_ROOT, RELOAD_SECONDS)
            except PlaywrightTimeout:
                pass

        if frame is None:
            print(f"FAIL: {APP_ROOT} never appeared.", file=sys.stderr)
            browser.close()
            return 1

        print(f"App is up — holding session for {SESSION_HOLD_MS // 1000}s...")
        page.wait_for_timeout(SESSION_HOLD_MS)
        print("OK: session held.")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
