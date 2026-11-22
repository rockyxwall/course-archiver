"""Downloader: video (yt-dlp) + PDF (requests), both resume-safe."""
import re
import time
from pathlib import Path

import imageio_ffmpeg
import requests
import yt_dlp

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def is_downloaded(path: Path) -> bool:
    """Return True if target exists either as direct file or as .url shortcut."""
    return path.exists() or path.with_suffix(".url").exists()


def _safe_name(name: str, max_len: int = 80) -> str:
    """Strip illegal chars, sanitize Windows reserved names, preserve extension, cap length."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().rstrip(". ")
    if not cleaned:
        return "untitled"

    parts = cleaned.rsplit(".", 1)
    if len(parts) == 2 and 1 <= len(parts[1]) <= 5 and not parts[1].isdigit():
        stem, ext = parts[0], f".{parts[1]}"
    else:
        stem, ext = cleaned, ""

    stem = stem.rstrip(". ")
    base = stem.split(".")[0].upper()
    if base in WINDOWS_RESERVED:
        stem = f"_{stem}"

    available = max(1, max_len - len(ext))
    stem = stem[:available].rstrip(". ")
    res_stem = stem if stem else "untitled"
    return f"{res_stem}{ext}"


class _YDLLogger:
    """Suppress raw yt-dlp stderr logs during retries; fatal errors raise exceptions."""
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def download_video(
    video_url: str,
    out_path: Path,
    referer: str,
    concurrent: int,
    quality: int,
    progress_hook=None,
    postprocessor_hook=None,
    max_retries: int = 3,
) -> None:
    if out_path.exists():
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = out_path.parent / ".temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

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
        "logger": _YDLLogger(),
        "paths": {
            "home": str(out_path.parent),
            "temp": str(temp_dir),
        },
        "outtmpl": {"default": f"{out_path.stem}.%(ext)s"},
        "format": fmt,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "concurrent_fragment_downloads": concurrent,
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        "http_headers": headers,
        "progress_hooks": [progress_hook] if progress_hook else [],
        "postprocessor_hooks": [postprocessor_hook] if postprocessor_hook else [],
        "quiet": True,
        "no_warnings": True,
        "continuedl": True,
        "socket_timeout": 30,
        "retries": 30,
        "fragment_retries": 50,
        "extractor_retries": 10,
        "file_access_retries": 10,
        "skip_unavailable_fragments": True,
        "abort_on_unavailable_fragment": False,
        "noprogress": True,
    }

    for attempt in range(1, max_retries + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            break
        except Exception:
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            if attempt == max_retries:
                raise
            time.sleep(2 * attempt)

    try:
        if temp_dir.exists() and not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        pass


def download_pdf(pdf_url: str, out_path: Path, cookies: dict[str, str], max_retries: int = 3) -> None:
    if is_downloaded(out_path):
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Check for web links or drive folders -> save as Windows Internet Shortcut (.url)
    if any(d in pdf_url for d in ["facebook.com", "fb.watch", "youtube.com", "youtu.be", "/drive/folders/"]):
        out_path.with_suffix(".url").write_text(f"[InternetShortcut]\nURL={pdf_url}\n", encoding="utf-8")
        return

    temp_path = out_path.with_suffix(".pdf.part")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    with requests.Session() as session:
        session.headers.update(headers)
        for attempt in range(1, max_retries + 1):
            try:
                target_url = pdf_url
                file_id = None

                # 2. Google Docs/Sheets/Slides PDF export
                doc_m = re.search(r"docs\.google\.com/(document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]+)", pdf_url)
                if doc_m:
                    doc_type, doc_id = doc_m.group(1), doc_m.group(2)
                    target_url = f"https://docs.google.com/{doc_type}/d/{doc_id}/export?format=pdf"
                # 3. Google Drive file direct download
                elif "drive.google.com" in pdf_url:
                    f_m = re.search(r"(?:/file/d/|/d/|id=)([a-zA-Z0-9_-]{20,})", pdf_url)
                    if f_m:
                        file_id = f_m.group(1)
                        target_url = f"https://docs.google.com/uc?export=download&id={file_id}"

                res = session.get(target_url, cookies=cookies, stream=True, timeout=60)

                # Check if Google Drive returned a large file confirmation page
                content_type = res.headers.get("Content-Type", "")
                if "text/html" in content_type and file_id:
                    token = None
                    for key, val in res.cookies.items():
                        if key.startswith("download_warning"):
                            token = val
                            break
                    if not token and "confirm=" in res.text:
                        m = re.search(r"confirm=([0-9A-Za-z_-]+)", res.text)
                        if m:
                            token = m.group(1)
                    if token:
                        target_url = f"https://docs.google.com/uc?export=download&confirm={token}&id={file_id}"
                        res = session.get(target_url, cookies=cookies, stream=True, timeout=60)
                        content_type = res.headers.get("Content-Type", "")

                # Fall back to .url shortcut on login wall / access-restricted / HTML error responses
                if res.status_code in (401, 403) or ("text/html" in content_type and res.status_code >= 400):
                    out_path.with_suffix(".url").write_text(f"[InternetShortcut]\nURL={pdf_url}\n", encoding="utf-8")
                    return

                res.raise_for_status()

                # 4. Stream to .pdf.part and inspect magic bytes (%PDF)
                is_valid_pdf = None
                with open(temp_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        if is_valid_pdf is None:
                            is_valid_pdf = chunk.startswith(b"%PDF") or b"%PDF" in chunk[:1024]
                        f.write(chunk)

                # If not a valid PDF (HTML error, login wall, restricted), save as .url shortcut
                if not is_valid_pdf:
                    if temp_path.exists():
                        temp_path.unlink()
                    out_path.with_suffix(".url").write_text(f"[InternetShortcut]\nURL={pdf_url}\n", encoding="utf-8")
                    return

                temp_path.replace(out_path)
                return

            except Exception:
                if attempt == max_retries:
                    raise
                time.sleep(2 * attempt)
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass


def _format_chapter_dir(chapter_name: str, subject_name: str) -> str:
    sub_upper = subject_name.upper()
    tag = ""
    if "LIVE" in sub_upper:
        tag = "LIVE"
    elif "ARCHIVE" in sub_upper:
        tag = "ARCHIVE"
    elif ":" in subject_name:
        tag = subject_name.split(":")[-1].strip()

    if not tag:
        return chapter_name

    pattern = rf'(?<![A-Za-z0-9]){re.escape(tag)}(?![A-Za-z0-9])'
    if not re.search(pattern, chapter_name, re.IGNORECASE):
        return f"{chapter_name}_{tag}"
    return chapter_name


def build_lecture_dir(
    output_dir: Path,
    course_name: str,
    subject_name: str,
    chapter_name: str,
    video_number: int,
    title: str,
) -> Path:
    ch_dir = _format_chapter_dir(chapter_name, subject_name)
    return (
        output_dir
        / _safe_name(course_name)
        / _safe_name(ch_dir)
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
