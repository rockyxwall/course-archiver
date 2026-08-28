"""Downloader: video (yt-dlp) + PDF (requests), both resume-safe."""
import re
from pathlib import Path

import requests
import yt_dlp


def _safe_name(name: str) -> str:
    """Strip chars illegal in Windows filenames."""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def download_video(
    m3u8_url: str,
    out_path: Path,
    cookie_str: str,
    concurrent: int,
    quality: int,
) -> None:
    if out_path.exists():
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 720p preferred, escalate to best if unavailable, never downgrade
    fmt = (
        f"bestvideo[height<={quality}]+bestaudio"
        f"/bestvideo[height>={quality}]+bestaudio"
        f"/bestvideo+bestaudio/best"
    )

    ydl_opts = {
        "outtmpl": str(out_path.with_suffix("")),  # yt-dlp appends ext
        "format": fmt,
        "concurrent_fragment_downloads": concurrent,
        "merge_output_format": "mp4",
        "http_headers": {"Cookie": cookie_str},
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([m3u8_url])


def download_pdf(pdf_url: str, out_path: Path, cookies: dict[str, str]) -> None:
    if out_path.exists():
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Handle Google Drive Links
    if "drive.google.com" in pdf_url:
        # Extract file ID
        file_id_match = re.search(r"/d/([a-zA-Z0-9_-]+)", pdf_url)
        if file_id_match:
            file_id = file_id_match.group(1)
            # Use Google Drive direct download URL
            pdf_url = f"https://docs.google.com/uc?export=download&id={file_id}"

            # Google Drive redirects to a warning page for large files.
            # We must parse the confirmation token and send a second request.
            session = requests.Session()
            response = session.get(pdf_url, stream=True, timeout=60)
            token = None
            for key, val in response.cookies.items():
                if key.startswith("download_warning"):
                    token = val
                    break
            if token:
                pdf_url = f"https://docs.google.com/uc?export=download&confirm={token}&id={file_id}"
                response = session.get(pdf_url, stream=True, timeout=60)

            response.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    f.write(chunk)
            return

    # Fallback to standard download
    with requests.get(pdf_url, cookies=cookies, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)


def build_out_path(
    output_dir: Path,
    course_name: str,
    subject_name: str,
    chapter_name: str,
    video_number: int,
    title: str,
    ext: str,
) -> Path:
    return (
        output_dir
        / _safe_name(course_name)
        / _safe_name(subject_name)
        / _safe_name(chapter_name)
        / f"{video_number:02d}_{_safe_name(title)}{ext}"
    )
