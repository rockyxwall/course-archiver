"""
Course downloader for redwansmethod.com

Usage:
  uv run python main.py          # automation mode
  uv run python main.py --train  # record navigation to build session.json
"""
import argparse
import sys

# Force output streams to UTF-8 to handle Bengali/Unicode characters
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src import auth, config, downloader, scraper, ui


def _make_context(playwright, cfg, headless: bool):
    browser = playwright.chromium.launch(headless=headless)
    if cfg.session_path.exists():
        context = browser.new_context(storage_state=str(cfg.session_path))
        ui.console.print("[green]Session restored from session.json[/green]")
    else:
        context = browser.new_context()
        ui.console.print("[yellow]No session found — will log in[/yellow]")
    return browser, context


def run_train(cfg) -> None:
    from src import trainer
    ui.console.rule("[bold cyan]Training Mode[/bold cyan]")
    ui.console.print("Navigate the site. Close browser or press Ctrl+C when done.\n")

    with sync_playwright() as p:
        browser, context = _make_context(p, cfg, headless=False)
        try:
            trainer.run(
                context=context,
                sel=cfg.selectors,
                report_path=cfg.session_path.parent / "training_report.json",
                email=cfg.email,
                password=cfg.password,
            )
        finally:
            auth.save_session(context, cfg.session_path)
            browser.close()


def run_auto(cfg) -> None:
    ui.console.rule("[bold cyan]Automation Mode[/bold cyan]")

    # ── Phase 1: get cookies (playwright open briefly, then closed) ──────────
    with sync_playwright() as p:
        headless = cfg.session_path.exists()
        browser, context = _make_context(p, cfg, headless=headless)
        try:
            if not cfg.session_path.exists():
                page = context.new_page()
                ui.console.print("Logging in...")
                auth.login(page, cfg.selectors, cfg.email, cfg.password)
                page.close()
            auth.save_session(context, cfg.session_path)
            cookies = auth.get_requests_cookies(context)
            cookie_str = auth.build_cookie_str(context)
        finally:
            browser.close()

    # ── Phase 2: API fetch + interactive selection (no playwright running) ───
    ui.console.print("Fetching purchased courses via API...")
    courses = scraper.get_all_courses(cookies)
    if not courses:
        ui.console.print("[red]No courses found.[/red]")
        sys.exit(1)

    selected = ui.select_courses(courses)
    if not selected:
        ui.console.print("Nothing selected. Exiting.")
        sys.exit(0)

    ui.console.print("Fetching course contents via API...")
    for course in selected:
        ui.console.print(f"  [cyan]{course.name}[/cyan]")
        scraper.get_course_tree(course, cookies)

    # Chapter selection (all pre-selected, user can deselect)
    selected = ui.select_chapters(selected)
    if not selected:
        ui.console.print("No chapters selected. Exiting.")
        sys.exit(0)

    ui.print_course_tree(selected)

    total_videos = sum(
        len(ch.videos)
        for c in selected
        for s in c.subjects
        for ch in s.chapters
    )

    # ── Phase 3: playwright for m3u8 interception only ───────────────────────
    ui.console.print(f"\nCapturing m3u8 for {total_videos} videos (headless browser)...")
    manifest: list[dict] = []

    with sync_playwright() as p:
        browser, context = _make_context(p, cfg, headless=True)
        page = context.new_page()
        try:
            for course in selected:
                for subject in course.subjects:
                    for chapter in subject.chapters:
                        for video in chapter.videos:
                            ui.console.print(f"[dim]Intercepting:[/dim] {video.title}")
                            url = scraper.intercept_video_url(page, video)
                            if not url:
                                kind = "YT" if video.video_type == "youtube" else "m3u8"
                                ui.console.print(f"[yellow]  No {kind}: {video.title}[/yellow]")
                            manifest.append({
                                "course": course,
                                "subject": subject,
                                "chapter": chapter,
                                "video": video,
                                "video_url": url,
                            })
        finally:
            browser.close()

    # ── Phase 4: download (no playwright) ────────────────────────────────────
    ui.print_manifest(manifest)

    total_files = sum(
        (1 if item["video_url"] else 0) + sum(
            1 for u in [
                item["video"].lecture_sheet_url, item["video"].note_url,
                item["video"].practice_sheet_url, item["video"].solve_sheet_url,
            ] if u
        )
        for item in manifest
    )
    ui.console.print(f"\nDownloading {total_files} files to [cyan]{cfg.output_dir}[/cyan]...\n")

    for i, item in enumerate(manifest, 1):
        course = item["course"]
        subject = item["subject"]
        chapter = item["chapter"]
        video = item["video"]

        if item["video_url"]:
            out = downloader.build_out_path(
                cfg.output_dir, course.name, subject.name,
                chapter.name, video.number, video.title, ".mp4"
            )
            kind = "YT" if video.video_type == "youtube" else "Bunny"
            ui.console.print(f"[{i}/{len(manifest)}] [cyan]Downloading {kind} Video:[/cyan] {video.title}")
            try:
                downloader.download_video(
                    item["video_url"], out, cookie_str,
                    cfg.concurrent_fragments, cfg.video_quality
                )
            except Exception as e:
                ui.console.print(f"[red]  Video failed: {video.title} — {e}[/red]")

        for label, url in [
            ("Lecture", video.lecture_sheet_url),
            ("Note", video.note_url),
            ("Practice", video.practice_sheet_url),
            ("Solve", video.solve_sheet_url),
        ]:
            if url:
                out = downloader.build_out_path(
                    cfg.output_dir, course.name, subject.name,
                    chapter.name, video.number, f"{video.title}_{label}", ".pdf"
                )
                ui.console.print(f"      [yellow]Downloading PDF ({label}):[/yellow] {video.title}")
                try:
                    downloader.download_pdf(url, out, cookies)
                except Exception as e:
                    ui.console.print(f"[red]  PDF failed ({label}): {video.title} — {e}[/red]")

    ui.console.print(f"\n[bold green]Done! Files saved to: {cfg.output_dir}[/bold green]")


def main() -> None:
    parser = argparse.ArgumentParser(description="redwansmethod.com course downloader")
    parser.add_argument("--train", action="store_true", help="Training mode: navigate site to save session")
    args = parser.parse_args()

    try:
        cfg = config.load()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except KeyError as e:
        print(f"Missing .env variable: {e}. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    try:
        if args.train:
            run_train(cfg)
        else:
            run_auto(cfg)
    except KeyboardInterrupt:
        ui.console.print("\n[yellow]Operation cancelled by user. Exiting...[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
