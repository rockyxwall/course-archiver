"""Config: loads .env, exposes typed config object."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
CONFIG_DIR = ROOT / "config"
RUNTIME_DIR = ROOT / ".runtime"


@dataclass
class Config:
    email: str
    password: str
    output_dir: Path
    concurrent_fragments: int
    concurrent_downloads: int
    video_quality: int
    session_path: Path
    selectors: dict


def load() -> Config:
    email = os.environ["EMAIL"]
    password = os.environ["PASSWORD"]
    output_dir = Path(os.environ.get("OUTPUT_DIR", "./downloads"))
    concurrent_fragments = int(os.environ.get("CONCURRENT_FRAGMENTS", "10"))
    concurrent_downloads = int(os.environ.get("CONCURRENT_DOWNLOADS", "3"))
    video_quality = int(os.environ.get("VIDEO_QUALITY", "720"))

    RUNTIME_DIR.mkdir(exist_ok=True)
    session_path = RUNTIME_DIR / "session.json"

    selectors_path = CONFIG_DIR / "selectors.json"
    if selectors_path.exists():
        with open(selectors_path, encoding="utf-8") as f:
            selectors = json.load(f)
    else:
        # Minimal default — works when session already exists
        selectors = {
            "login": {
                "url": "https://www.redwansmethod.com/profile",
                "email_input": "input[type='email']",
                "password_input": "input[type='password']",
                "submit_button": "button[type='submit']",
            }
        }

    return Config(
        email=email,
        password=password,
        output_dir=output_dir,
        concurrent_fragments=concurrent_fragments,
        concurrent_downloads=concurrent_downloads,
        video_quality=video_quality,
        session_path=session_path,
        selectors=selectors,
    )
