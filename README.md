# course-archiver

Automated downloader for **redwansmethod.com** purchased courses.
Downloads videos (Bunny CDN HLS + YouTube) and PDF attachments.

## Quick Start

```powershell
# 1. Setup (once)
cp .env.example .env        # fill EMAIL, PASSWORD, OUTPUT_DIR
uv sync
uv run playwright install chromium

# 2. First run — saves session.json (browser opens briefly)
uv run python main.py

# 3. All subsequent runs — fully headless
uv run python main.py
```

## How It Works

```
main.py
│
├── Phase 1  Playwright (headless=False if no session)
│            └── Gets cookies → closes browser
│
├── Phase 2  Pure API (requests, no browser)
│            ├── GET /courses/fetchCoursesByUser/{userId}
│            ├── GET /subjects/fetchAllSubjects/{courseId}
│            ├── GET /chapters/fetchAllChapters/{subjectId}
│            └── GET /videos/fetchAllVideos/{chapterId}
│            └── Interactive selection (courses → chapters → reorder)
│
├── Phase 3  Playwright headless=True
│            └── /watch/{videoId} → intercepts playlist.m3u8 (Bunny)
│                                 or youtu.be redirect (YouTube)
│
└── Phase 4  Downloads (no browser)
             ├── yt-dlp  → video (.mp4, 720p preferred)
             └── requests → PDFs (lecture sheet, notes, practice, solve)
```

## File Structure

```
src/
  config.py      — loads .env + config/selectors.json, typed Config dataclass
  auth.py        — Playwright login, session save, cookie helpers
  scraper.py     — API client + Playwright m3u8/YT URL interceptor
  downloader.py  — yt-dlp video + requests PDF, both resume-safe
  ui.py          — Rich tree/table/progress + InquirerPy selection
  trainer.py     — --train mode: records navigation to .runtime/training_report.json

config/
  selectors.json — login form CSS selectors (user-editable, committed to git)

.runtime/        — auto-generated, gitignored
  session.json   — Playwright auth storage state
  training_report.json — recorded URLs from --train mode
```

## Config (.env)

| Variable | Default | Description |
|---|---|---|
| `EMAIL` | — | Login email |
| `PASSWORD` | — | Login password |
| `OUTPUT_DIR` | `D:/Vidoes/Course` | Where files are saved |
| `CONCURRENT_DOWNLOADS` | `3` | Parallel video downloads |
| `CONCURRENT_FRAGMENTS` | `10` | yt-dlp parallel chunk downloads per video |
| `VIDEO_QUALITY` | `1080` | Preferred height (e.g. 1080 $\to$ 720 $\to$ 480 $\to$ best) |

## Output Structure

```
OUTPUT_DIR/
  {Course}/
    {Subject}/
      {Chapter}/
        01_Lecture 1/
          01_Lecture 1.mp4
          Lecture.pdf
          Note.pdf
```

## Re-login / Session Expired

Delete `session.json` and run again — browser will open and log in fresh.

## Training Mode (advanced)

If the site changes its URL structure:

```powershell
uv run python main.py --train
```

Browser opens → navigate the site → Ctrl+C → review `training_report.json`.
