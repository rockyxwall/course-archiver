"""Downloader: video (yt-dlp) + PDF (requests), both resume-safe."""
import re
from pathlib import Path

import requests
import yt_dlp


def _safe_name(name: str) -> str:
    """Strip chars illegal in Windows filenames."""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def download_video(
    video_url: str,
    out_path: Path,
    referer: str,
    concurrent: int,
    quality: int,
    progress_hook=None,
) -> None:
    if out_path.exists():
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = out_path.parent / ".temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Highest quality up to preferred height (e.g. 1080 -> 720 -> 480 -> next best)
    fmt = (
        f"bestvideo[height<={quality}]+bestaudio"
        f"/best[height<={quality}]"
        f"/bestvideo+bestaudio"
        f"/best"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    if referer:
        headers["Referer"] = referer
    elif "b-cdn.net" in video_url or "mediadelivery.net" in video_url:
        headers["Referer"] = "https://iframe.mediadelivery.net/"

    ydl_opts = {
        "paths": {
            "home": str(out_path.parent),
            "temp": str(temp_dir),
        },
        "outtmpl": {"default": out_path.stem},  # yt-dlp appends .mp4
        "format": fmt,
        "concurrent_fragment_downloads": concurrent,
        "merge_output_format": "mp4",
        "http_headers": headers,
        "progress_hooks": [progress_hook] if progress_hook else [],
        "quiet": True,
        "no_warnings": True,
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    # Clean up empty .temp folder
    try:
        if temp_dir.exists() and not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        pass


def download_pdf(pdf_url: str, out_path: Path, cookies: dict[str, str]) -> None:
    if out_path.exists():
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # If it's a web/social link instead of a document, save as Windows Internet Shortcut (.url)
    if any(domain in pdf_url for domain in ["facebook.com", "fb.watch", "youtube.com", "youtu.be"]):
        shortcut_path = out_path.with_suffix(".url")
        shortcut_path.write_text(f"[InternetShortcut]\nURL={pdf_url}\n", encoding="utf-8")
        return

    temp_path = out_path.with_suffix(".pdf.part")

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    })

    # Handle Google Drive Links
    if "drive.google.com" in pdf_url or "docs.google.com" in pdf_url:
        file_id_match = re.search(r"(?:/file/d/|/d/|id=)([a-zA-Z0-9_-]{20,})", pdf_url)
        if file_id_match:
            file_id = file_id_match.group(1)
            direct_url = f"https://docs.google.com/uc?export=download&id={file_id}"
            res = session.get(direct_url, stream=True, timeout=60)

            # Check for large file warning token
            token = None
            for key, val in res.cookies.items():
                if key.startswith("download_warning"):
                    token = val
                    break
            if not token and "confirm=" in res.text:
                m = re.search(r"confirm=([0-9A-Za-z_]+)", res.text)
                if m:
                    token = m.group(1)
            if token:
                direct_url = f"https://docs.google.com/uc?export=download&confirm={token}&id={file_id}"
                res = session.get(direct_url, stream=True, timeout=60)

            res.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in res.iter_content(chunk_size=65536):
                    f.write(chunk)
            temp_path.replace(out_path)
            return

    # Fallback to standard download
    with session.get(pdf_url, cookies=cookies, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(temp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
    temp_path.replace(out_path)


def build_lecture_dir(
    output_dir: Path,
    course_name: str,
    subject_name: str,
    chapter_name: str,
    video_number: int,
    title: str,
) -> Path:
    """Creates a unified folder for a lecture holding both its video and PDFs."""
    return (
        output_dir
        / _safe_name(course_name)
        / _safe_name(subject_name)
        / _safe_name(chapter_name)
        / f"{video_number:02d}_{_safe_name(title)}"
    )


def build_out_path(
    output_dir: Path,
    course_name: str,
    subject_name: str,
    chapter_name: str,
    video_number: int,
    title: str,
    filename: str,
) -> Path:
    lecture_dir = build_lecture_dir(
        output_dir, course_name, subject_name, chapter_name, video_number, title
    )
    return lecture_dir / _safe_name(filename)
