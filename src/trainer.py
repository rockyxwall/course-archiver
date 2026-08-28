"""Trainer: --train mode. Opens browser, records navigation + m3u8 hits + links."""
import json
import signal
import sys
from pathlib import Path
from datetime import datetime

from playwright.sync_api import Page, BrowserContext


_report: dict = {
    "recorded_at": None,
    "pages_visited": [],
    "m3u8_urls": [],
}
_current_url: str = ""


def _on_response(response) -> None:
    if ".m3u8" in response.url:
        entry = {"m3u8_url": response.url, "triggered_from_page": _current_url}
        _report["m3u8_urls"].append(entry)
        print(f"  [m3u8] {response.url}")


def _on_framenavigated(frame) -> None:
    global _current_url
    if frame.parent_frame is not None:
        return  # only main frame
    url = frame.url
    if url == _current_url or url in ("about:blank", ""):
        return
    _current_url = url
    print(f"  [nav]  {url}")


def _snapshot_links(page: Page, url: str) -> list[str]:
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)"
        )
        return hrefs
    except Exception:
        return []


def run(
    context: BrowserContext,
    sel: dict,
    report_path: Path,
    email: str,
    password: str,
) -> None:
    page = context.new_page()
    page.on("response", _on_response)
    page.on("framenavigated", _on_framenavigated)

    # Auth: login if needed (skipped when session already valid)
    from src import auth
    session_exists = (Path(__file__).parent.parent / ".runtime" / "session.json").exists()
    if session_exists:
        print("\n[trainer] Session found — skipping login, navigating to profile...")
        page.goto(sel["login"]["url"])
        page.wait_for_load_state("networkidle")
    else:
        print("\n[trainer] Logging in...")
        auth.login(page, sel, email, password)

    print("[trainer] Ready. Navigate the site freely.")
    print("[trainer] Visit: courses list → a course → a module → a lesson")
    print("[trainer] Press Ctrl+C when done.\n")

    visited_urls: set[str] = set()

    def _capture_page_snapshot(frame=None) -> None:
        url = page.url
        if url in visited_urls or url in ("about:blank", ""):
            return
        visited_urls.add(url)
        try:
            title = page.title()
        except Exception:
            title = ""
        links = _snapshot_links(page, url)
        _report["pages_visited"].append({"url": url, "title": title, "links": links})

    page.on("load", lambda: _capture_page_snapshot())

    # Block until user closes browser or Ctrl+C
    def _save_and_exit(*_):
        _report["recorded_at"] = datetime.now().isoformat()
        report_path.write_text(
            json.dumps(_report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[trainer] Saved → {report_path}")
        print("[trainer] Next: review training_report.json and fill selectors.json")
        sys.exit(0)

    signal.signal(signal.SIGINT, _save_and_exit)
    signal.signal(signal.SIGTERM, _save_and_exit)

    try:
        # Keep alive until browser closes
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass
    finally:
        _save_and_exit()
