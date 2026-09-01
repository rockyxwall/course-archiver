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

    # ── Phase 3 & 4: Just-In-Time Intercept & Concurrent Download ───────────
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    # Flatten all video items
    all_items = [
        (course, subject, chapter, video)
        for course in selected
        for subject in course.subjects
        for chapter in subject.chapters
        for video in chapter.videos
    ]

    total_count = len(all_items)
    ui.console.print(f"\nProcessing [bold]{total_count}[/bold] videos with [cyan]{cfg.concurrent_downloads}[/cyan] parallel downloads...\n")

    def _worker(title, kind, v_url, referer, v_out, pdfs, ck, frags, qual):
        errs = []
        if v_url and v_out and not v_out.exists():
            try:
                downloader.download_video(v_url, v_out, referer, frags, qual)
            except Exception as e:
                errs.append(f"Video '{title}' failed: {e}")

        for label, url, p_out in pdfs:
            if not p_out.exists():
                try:
                    downloader.download_pdf(url, p_out, ck)
                except Exception as e:
                    errs.append(f"PDF '{title}' ({label}) failed: {e}")

        return title, kind, errs

    futures = set()
    skipped = 0
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=cfg.concurrent_downloads) as executor:
        with sync_playwright() as p:
            browser, context = _make_context(p, cfg, headless=True)
            page = context.new_page()

            try:
                for idx, (course, subject, chapter, video) in enumerate(all_items, 1):
                    # Check disk first
                    video_filename = f"{video.number:02d}_{video.title}.mp4"
                    video_out = downloader.build_out_path(
                        cfg.output_dir, course.name, subject.name,
                        chapter.name, video.number, video.title, video_filename
                    )
                    pdf_tasks = [
                        (label, url, downloader.build_out_path(
                            cfg.output_dir, course.name, subject.name,
                            chapter.name, video.number, video.title, f"{label}.pdf"
                        ))
                        for label, url in [
                            ("Lecture", video.lecture_sheet_url),
                            ("Note", video.note_url),
                            ("Practice", video.practice_sheet_url),
                            ("Solve", video.solve_sheet_url),
                        ] if url
                    ]

                    video_needed = not video_out.exists()
                    pdfs_needed = [(l, u, o) for l, u, o in pdf_tasks if not o.exists()]

                    if not video_needed and not pdfs_needed:
                        skipped += 1
                        ui.console.print(f"[dim][{idx}/{total_count}] Skipped (already exists): {video.title}[/dim]")
                        continue

                    # Throttle interception to active worker capacity
                    while len(futures) >= cfg.concurrent_downloads:
                        done, futures = wait(futures, return_when=FIRST_COMPLETED)
                        for fut in done:
                            t, k, errs = fut.result()
                            if errs:
                                failed += 1
                                for err in errs:
                                    ui.console.print(f"[red]  {err}[/red]")
                            else:
                                completed += 1
                                ui.console.print(f"[green]✓ Completed {k}: {t}[/green]")

                    # JIT URL interception if video download needed
                    video_url = None
                    referer = ""
                    kind = "YT" if video.video_type == "youtube" else "Bunny"
                    if video_needed:
                        ui.console.print(f"[{idx}/{total_count}] [cyan]Intercepting & queuing {kind}:[/cyan] {video.title}")
                        stream_info = scraper.intercept_video_url(page, video)
                        if stream_info:
                            video_url = stream_info.get("url")
                            referer = stream_info.get("referer", "")
                        else:
                            ui.console.print(f"[yellow]  Warning: No stream URL captured for {video.title}[/yellow]")

                    # Submit download task
                    fut = executor.submit(
                        _worker,
                        video.title,
                        kind,
                        video_url,
                        referer,
                        video_out if video_needed else None,
                        pdfs_needed,
                        cookies,
                        cfg.concurrent_fragments,
                        cfg.video_quality,
                    )
                    futures.add(fut)

            finally:
                browser.close()

        # Drain remaining downloads
        for fut in wait(futures).done:
            t, k, errs = fut.result()
            if errs:
                failed += 1
                for err in errs:
                    ui.console.print(f"[red]  {err}[/red]")
            else:
                completed += 1
                ui.console.print(f"[green]✓ Completed {k}: {t}[/green]")

    ui.print_summary(total_count, completed, skipped, failed, cfg.output_dir)


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
