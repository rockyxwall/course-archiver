"""Auth: login, save/load session, cookie conversion."""
import json
from pathlib import Path

from playwright.sync_api import BrowserContext, Page


def login(page: Page, sel: dict, email: str, password: str) -> None:
    s = sel["login"]
    page.goto(s["url"])
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    # Already logged in — form won't exist, skip
    if not page.is_visible(s["email_input"]):
        return
    page.fill(s["email_input"], email)
    page.fill(s["password_input"], password)
    page.click(s["submit_button"])
    # Next.js SPA: URL stays /profile before and after login.
    # Wait for the login form to disappear as the success signal.
    page.wait_for_selector(s["email_input"], state="hidden", timeout=30_000)


def save_session(context: BrowserContext, path: Path) -> None:
    state = context.storage_state()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_cookies(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {c["name"]: c["value"] for c in data.get("cookies", [])}
    except Exception:
        return {}


def get_requests_cookies(context: BrowserContext) -> dict[str, str]:
    return {c["name"]: c["value"] for c in context.cookies()}


def build_cookie_str(context: BrowserContext) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in context.cookies())
