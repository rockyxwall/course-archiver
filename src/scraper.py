"""Scraper: uses redwansmethod.com JSON API directly. No DOM scraping needed."""
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import requests as req
from playwright.sync_api import Page

API_BASE = "https://api.redwansmethod.com"


@dataclass
class Video:
    id: str
    title: str
    number: int
    video_type: str          # "youtube" | "bunny" | other
    bunny_id: str
    watch_url: str
    lecture_sheet_url: Optional[str] = None
    note_url: Optional[str] = None
    practice_sheet_url: Optional[str] = None
    solve_sheet_url: Optional[str] = None


@dataclass
class Chapter:
    id: str
    name: str
    position: int
    videos: list[Video] = field(default_factory=list)


@dataclass
class Subject:
    id: str
    name: str
    chapters: list[Chapter] = field(default_factory=list)


@dataclass
class Course:
    id: str
    name: str
    subjects: list[Subject] = field(default_factory=list)


def _api(path: str, cookies: dict, token: str = "") -> dict | list:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = req.get(f"{API_BASE}{path}", cookies=cookies, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def get_session_info(session_cookies: dict) -> tuple[str, str]:
    """Returns (user_id, access_token)."""
    r = req.get("https://www.redwansmethod.com/api/auth/session", cookies=session_cookies, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data["user"]["mongoId"], data["user"]["accessToken"]


def get_all_courses(cookies: dict) -> list[Course]:
    user_id, token = get_session_info(cookies)
    data = _api(f"/courses/fetchCoursesByUser/{user_id}", cookies, token)
    items = data.get("courses", data) if isinstance(data, dict) else data
    return [Course(id=c["_id"], name=c["courseName"]) for c in items]


def get_course_tree(course: Course, cookies: dict) -> None:
    """Populate course.subjects → chapters → videos in-place."""
    _, token = get_session_info(cookies)
    sdata = _api(f"/subjects/fetchAllSubjects/{course.id}", cookies, token)
    subjects_raw = sdata.get("subjects", sdata) if isinstance(sdata, dict) else sdata
    for s in subjects_raw:
        subject = Subject(id=s["_id"], name=s["subjectName"])
        cdata = _api(f"/chapters/fetchAllChapters/{subject.id}", cookies, token)
        chapters_raw = cdata.get("chapters", cdata) if isinstance(cdata, dict) else cdata
        for ch in sorted(chapters_raw, key=lambda x: x.get("position", 0)):
            chapter = Chapter(id=ch["_id"], name=ch["chapterName"], position=ch.get("position", 0))
            vdata = _api(f"/videos/fetchAllVideos/{chapter.id}", cookies, token)
            videos_raw = vdata.get("videos", vdata) if isinstance(vdata, dict) else vdata
            for v in sorted(videos_raw, key=lambda x: x.get("videoNumber", 0)):
                chapter.videos.append(Video(
                    id=v["_id"],
                    title=v.get("videoTitle", ""),
                    number=v.get("videoNumber", 0),
                    video_type=v.get("videoType", "bunny").lower(),
                    bunny_id=v.get("bunnyVideoId", ""),
                    watch_url=f"https://www.redwansmethod.com/watch/{v['_id']}",
                    lecture_sheet_url=v.get("videoLectureSheetURL") or None,
                    note_url=v.get("videoNoteURL") or None,
                    practice_sheet_url=v.get("videoPracticeSheetURL") or None,
                    solve_sheet_url=v.get("videoSolveSheetURL") or None,
                ))
            subject.chapters.append(chapter)
        course.subjects.append(subject)


def intercept_video_url(page: Page, video: Video, timeout_ms: int = 12_000) -> Optional[dict]:
    """
    Navigate to watch page and capture the video URL and Referer header.
    Handles:
    - YouTube embeds: DOM iframe src, youtube-nocookie, embed, youtu.be
    - Bunny CDN: playlist.m3u8 + Request Referer
    """
    captured: Optional[dict] = None
    lock = threading.Lock()

    def handle_response(response):
        nonlocal captured
        url = response.url
        if captured is not None:
            return

        # 1. Bunny CDN m3u8
        if "playlist.m3u8" in url:
            with lock:
                referer = (
                    response.request.headers.get("referer")
                    or response.request.headers.get("Referer")
                    or "https://iframe.mediadelivery.net/"
                )
                captured = {"url": url, "referer": referer}
            return

        # 2. YouTube network request
        m = re.search(r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=))([A-Za-z0-9_-]{11})", url)
        if m:
            with lock:
                captured = {
                    "url": f"https://www.youtube.com/watch?v={m.group(1)}",
                    "referer": "https://www.youtube.com/",
                }

    page.on("response", handle_response)
    try:
        page.goto(video.watch_url)
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass

    # Poll up to timeout_ms: check network response OR DOM iframe/metadata
    start = time.time()
    while captured is None and (time.time() - start) * 1000 < timeout_ms:
        try:
            yt_id = page.evaluate(r"""() => {
                // 1. Scan iframes
                for (const iframe of document.querySelectorAll('iframe')) {
                    const src = iframe.src || iframe.getAttribute('src') || '';
                    const m = src.match(/(?:embed\/|v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
                    if (m) return m[1];
                    for (const attr of ['data-video-id', 'data-videoid', 'data-youtube-id', 'data-yt-id']) {
                        const val = iframe.getAttribute(attr);
                        if (val && /^[A-Za-z0-9_-]{11}$/.test(val)) return val;
                    }
                }

                // 2. Scan video elements & parents
                for (const video of document.querySelectorAll('video')) {
                    let el = video;
                    let depth = 0;
                    while (el && depth < 6) {
                        for (const attr of ['data-video-id', 'data-videoid', 'data-youtube-id', 'data-yt-id']) {
                            const val = el.getAttribute && el.getAttribute(attr);
                            if (val && /^[A-Za-z0-9_-]{11}$/.test(val)) return val;
                        }
                        el = el.parentElement;
                        depth++;
                    }
                }

                // 3. Scan meta tags
                for (const sel of ['meta[property="og:video"]', 'meta[property="og:video:url"]', 'meta[name="twitter:player"]']) {
                    const meta = document.querySelector(sel);
                    if (meta) {
                        const content = meta.getAttribute('content') || '';
                        const m = content.match(/(?:embed\/|v=)([A-Za-z0-9_-]{11})/);
                        if (m) return m[1];
                    }
                }

                // 4. Scan script tags
                for (const script of document.querySelectorAll('script')) {
                    const text = script.textContent || '';
                    const m = text.match(/["']([A-Za-z0-9_-]{11})["'].*youtube/i) || text.match(/youtube.*["']([A-Za-z0-9_-]{11})["']/i);
                    if (m) return m[1];
                }

                return null;
            }""")

            if yt_id:
                captured = {
                    "url": f"https://www.youtube.com/watch?v={yt_id}",
                    "referer": "https://www.youtube.com/",
                }
                break
        except Exception:
            pass

        if captured:
            break
        page.wait_for_timeout(100)

    page.remove_listener("response", handle_response)
    return captured

