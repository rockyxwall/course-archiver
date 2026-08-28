"""Scraper: uses redwansmethod.com JSON API directly. No DOM scraping needed."""
import re
import threading
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


def intercept_video_url(page: Page, video: Video, timeout_ms: int = 12_000) -> Optional[str]:
    """
    Navigate to watch page and capture the video URL.
    - YouTube videos: capture youtu.be redirect URL -> return youtube.com/watch?v=ID
    - Bunny videos: capture playlist.m3u8 URL
    Returns None if nothing captured.
    """
    captured: Optional[str] = None
    lock = threading.Lock()

    if video.video_type == "youtube":
        def handle_response(response):
            nonlocal captured
            # youtu.be/ID redirect fires as a network request
            if "youtu.be/" in response.url and captured is None:
                m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", response.url)
                if m:
                    with lock:
                        captured = f"https://www.youtube.com/watch?v={m.group(1)}"
    else:
        def handle_response(response):
            nonlocal captured
            if "playlist.m3u8" in response.url and captured is None:
                with lock:
                    captured = response.url

    page.on("response", handle_response)
    try:
        page.goto(video.watch_url)
        # DOMContentLoaded is faster and won't hang on background analytic tracking
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
        page.wait_for_timeout(timeout_ms)
    except Exception:
        # Fallback: if load state fails, just sleep to allow capturing anyway
        page.wait_for_timeout(timeout_ms)
    finally:
        page.remove_listener("response", handle_response)

    return captured

