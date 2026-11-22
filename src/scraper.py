"""Scraper: uses JSON API directly with connection pooling and token auto-refresh."""
from dataclasses import dataclass, field
from typing import Optional

import requests as req

API_BASE = "https://api.redwansmethod.com"

_session = req.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
})
_active_token: Optional[str] = None


@dataclass
class Video:
    id: str
    title: str
    number: int
    video_type: str          # "youtube" | "bunny" | other
    bunny_id: str
    watch_url: str
    youtube_url: Optional[str] = None
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
    global _active_token
    auth_token = _active_token or token
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    r = _session.get(f"{API_BASE}{path}", cookies=cookies, headers=headers, timeout=30)
    if r.status_code == 401 and cookies:
        try:
            _, new_token = get_session_info(cookies)
            _active_token = new_token
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
            r = _session.get(f"{API_BASE}{path}", cookies=cookies, headers=headers, timeout=30)
        except Exception:
            pass
    r.raise_for_status()
    return r.json()


def get_session_info(session_cookies: dict) -> tuple[str, str]:
    """Returns (user_id, access_token)."""
    global _active_token
    r = _session.get("https://www.redwansmethod.com/api/auth/session", cookies=session_cookies, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid or expired session: response is not a valid JSON object.")
    user = data.get("user")
    if not isinstance(user, dict):
        raise ValueError("Invalid or expired session: no user in session response.")
    user_id = user.get("mongoId")
    token = user.get("accessToken")
    if not user_id or not token:
        raise ValueError("Invalid or expired session: missing mongoId or accessToken.")
    _active_token = token
    return user_id, token


def is_session_valid(session_cookies: dict) -> bool:
    try:
        user_id, token = get_session_info(session_cookies)
        return bool(user_id and token)
    except Exception:
        return False


def get_all_courses(cookies: dict) -> list[Course]:
    user_id, token = get_session_info(cookies)
    data = _api(f"/courses/fetchCoursesByUser/{user_id}", cookies, token)
    items = data.get("courses", data) if isinstance(data, dict) else data
    return [Course(id=c["_id"], name=c.get("courseName") or c.get("title") or "Untitled") for c in items]


def get_course_tree(course: Course, cookies: dict) -> None:
    """Populate course.subjects -> chapters -> videos in-place."""
    course.subjects = []
    _, token = get_session_info(cookies)
    sdata = _api(f"/subjects/fetchAllSubjects/{course.id}", cookies, token)
    subjects_raw = sdata.get("subjects", sdata) if isinstance(sdata, dict) else sdata
    for s in subjects_raw:
        subject = Subject(id=s["_id"], name=s.get("subjectName") or "")
        cdata = _api(f"/chapters/fetchAllChapters/{subject.id}", cookies, token)
        chapters_raw = cdata.get("chapters", cdata) if isinstance(cdata, dict) else cdata
        for ch in sorted(chapters_raw, key=lambda x: int(x.get("position") or 0)):
            chapter = Chapter(
                id=ch["_id"],
                name=ch.get("chapterName") or "",
                position=int(ch.get("position") or 0),
            )
            vdata = _api(f"/videos/fetchAllVideos/{chapter.id}", cookies, token)
            videos_raw = vdata.get("videos", vdata) if isinstance(vdata, dict) else vdata
            for v in sorted(videos_raw, key=lambda x: int(x.get("videoNumber") or 0)):
                chapter.videos.append(Video(
                    id=v["_id"],
                    title=v.get("videoTitle") or "",
                    number=int(v.get("videoNumber") or 0),
                    video_type=(v.get("videoType") or "bunny").lower(),
                    bunny_id=v.get("bunnyVideoId") or "",
                    watch_url=f"https://www.redwansmethod.com/watch/{v['_id']}",
                    youtube_url=v.get("videoYoutubeURL") or None,
                    lecture_sheet_url=v.get("videoLectureSheetURL") or None,
                    note_url=v.get("videoNoteURL") or None,
                    practice_sheet_url=v.get("videoPracticeSheetURL") or None,
                    solve_sheet_url=v.get("videoSolveSheetURL") or None,
                ))
            subject.chapters.append(chapter)
        course.subjects.append(subject)


def resolve_stream(video: Video, cookies: dict, token: str) -> tuple[Optional[str], str]:
    """Resolve stream URL in ~50ms using JSON API without launching browser."""
    # 1. Direct YouTube video
    if video.video_type == "youtube" and video.youtube_url:
        return video.youtube_url, "https://www.youtube.com/"

    # 2. Bunny CDN via signed-playback endpoint
    if video.bunny_id or video.video_type in ("bunny", "server"):
        try:
            data = _api(f"/videos/signed-playback/{video.id}", cookies, token)
            cdn_url = data.get("url") if isinstance(data, dict) else None
            if cdn_url:
                try:
                    head_resp = _session.head(
                        cdn_url,
                        headers={"Referer": "https://www.redwansmethod.com/"},
                        timeout=5,
                    )
                    # Only reject if CDN explicitly confirms the stream does not exist
                    if head_resp.status_code in (404, 410):
                        cdn_url = None
                except Exception:
                    pass
                if cdn_url:
                    return cdn_url, "https://www.redwansmethod.com/"
        except Exception:
            pass

    # 3. Fallback to YouTube if known
    if video.youtube_url:
        return video.youtube_url, "https://www.youtube.com/"

    return None, ""
