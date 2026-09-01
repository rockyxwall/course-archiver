"""
Course downloader

Usage:
  uv run python main.py          # automation mode
  uv run python main.py --train  # record navigation to build session.json
"""
import argparse
import os
import re
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

    selected = ui.select_workflow(courses, cookies, scraper, cfg.output_dir)
    if not selected:
        ui.console.print("Nothing selected. Exiting.")
        sys.exit(0)

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
    ui.console.print(f"\nProcessing [bold]{total_count}[/bold] videos with [cyan]{cfg.concurrent_downloads}[/cyan] parallel downloads (Target: [bold green]{cfg.video_quality}p[/bold green])...\n")

    progress = ui.make_progress()
    overall_task = progress.add_task(
        "[bold green]Total Progress",
        total=total_count,
        name="Overall",
        info=f"0/{total_count} completed",
    )

    def _worker(idx, title, kind, v_url, referer, v_out, pdfs, ck, frags, qual):
        errs = []
        display_title = title if len(title) <= 22 else f"{title[:19]}..."
        task_id = progress.add_task(
            f"[cyan]{display_title}",
            total=None,
            name=f"[{idx}/{total_count}] {display_title}",
            info="Starting...",
        )

        try:
            # 1. Video Download
            if v_url and v_out and not v_out.exists():
                temp_dir = v_out.parent / ".temp"
                downloaded_frags = set()
                if temp_dir.exists():
                    try:
                        for f in temp_dir.iterdir():
                            if ".part-Frag" in f.name and not f.name.endswith(".part"):
                                m = re.search(r"-Frag(\d+)", f.name)
                                if m:
                                    downloaded_frags.add(int(m.group(1)))
                    except Exception:
                        pass

                def ydl_hook(d):
                    if d["status"] == "downloading":
                        downloaded = d.get("downloaded_bytes", 0)
                        frag_idx = d.get("fragment_index")
                        frag_count = d.get("fragment_count")
                        speed_str = ui.fmt_speed(d.get("speed"))
                        eta_str = ui.fmt_eta(d.get("eta"))
                        bytes_str = ui.fmt_bytes(downloaded)

                        if frag_count and frag_count > 0:
                            if frag_idx is not None:
                                downloaded_frags.add(frag_idx)
                            completed_count = len(downloaded_frags)
                            info = f"{completed_count}/{frag_count} frags ({bytes_str}) {speed_str} {eta_str}"
                            progress.update(task_id, completed=completed_count, total=frag_count, info=info)
                        else:
                            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                            if total_bytes > 0:
                                total_str = ui.fmt_bytes(total_bytes)
                                info = f"{bytes_str}/{total_str} {speed_str} {eta_str}"
                                progress.update(task_id, completed=downloaded, total=total_bytes, info=info)
                            else:
                                info = f"{bytes_str} {speed_str} {eta_str}"
                                progress.update(task_id, completed=downloaded, info=info)
                    elif d["status"] == "finished":
                        progress.update(task_id, completed=100, total=100, info="Merging video (ffmpeg)...")

                def post_hook(d):
                    status = d.get("status")
                    if status == "started":
                        progress.update(task_id, completed=100, total=100, info="Merging video (ffmpeg)...")
                    elif status == "finished":
                        progress.update(task_id, completed=100, total=100, info="Merge complete.")

                try:
                    downloader.download_video(
                        v_url, v_out, referer, frags, qual,
                        progress_hook=ydl_hook,
                        postprocessor_hook=post_hook,
                    )
                except Exception as e:
                    errs.append(f"Video '{title}' failed: {e}")

            # 2. PDF Attachments (in the same progress bar)
            needed_pdfs = [(lbl, u, p) for lbl, u, p in pdfs if not p.exists() and not p.with_suffix(".url").exists()]
            total_needed = len(needed_pdfs)
            for p_idx, (label, url, p_out) in enumerate(needed_pdfs, 1):
                progress.update(
                    task_id,
                    completed=100,
                    total=100,
                    info=f"PDF ({p_idx}/{total_needed} {label}.pdf)",
                )
                try:
                    downloader.download_pdf(url, p_out, ck)
                except Exception as e:
                    errs.append(f"PDF '{title}' ({label}) failed: {e}")

        finally:
            if task_id is not None:
                progress.remove_task(task_id)

        progress.advance(overall_task)
        curr_overall = progress.tasks[overall_task].completed
        progress.update(overall_task, info=f"{int(curr_overall)}/{total_count} completed")
        return title, kind, errs

    futures = set()
    skipped = 0
    completed = 0
    failed = 0

    try:
        with progress:
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
                                progress.advance(overall_task)
                                curr_overall = progress.tasks[overall_task].completed
                                progress.update(overall_task, info=f"{int(curr_overall)}/{total_count} completed")
                                progress.console.print(f"[dim][{idx}/{total_count}] Skipped (already exists): {video.title}[/dim]")
                                continue

                            # Throttle interception to active worker capacity
                            while len(futures) >= cfg.concurrent_downloads:
                                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                                for fut in done:
                                    t, k, errs = fut.result()
                                    if errs:
                                        failed += 1
                                        for err in errs:
                                            progress.console.print(f"[red]  {err}[/red]")
                                    else:
                                        completed += 1
                                        progress.console.print(f"[green]✓ Completed {k} [{cfg.video_quality}p]: {t}[/green]")

                            # JIT URL interception if video download needed
                            video_url = None
                            referer = ""
                            kind = "YT" if video.video_type == "youtube" else "Bunny"
                            if video_needed:
                                progress.console.print(f"[{idx}/{total_count}] [cyan]Intercepting & queuing {kind} [{cfg.video_quality}p]:[/cyan] {video.title}")
                                stream_info = scraper.intercept_video_url(page, video)
                                if stream_info:
                                    video_url = stream_info.get("url")
                                    referer = stream_info.get("referer", "")
                                else:
                                    progress.console.print(f"[yellow]  Warning: No stream URL captured for {video.title}[/yellow]")

                            # Submit download task
                            fut = executor.submit(
                                _worker,
                                idx,
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
                            progress.console.print(f"[red]  {err}[/red]")
                    else:
                        completed += 1
                        progress.console.print(f"[green]✓ Completed {k} [{cfg.video_quality}p]: {t}[/green]")
    except KeyboardInterrupt:
        ui.console.print("\n[yellow]Operation cancelled by user. Exiting...[/yellow]")
        os._exit(0)

    ui.print_summary(total_count, completed, skipped, failed, cfg.output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Course downloader")
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
        os._exit(0)


if __name__ == "__main__":
    main()
