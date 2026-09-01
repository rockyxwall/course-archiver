import json
from pathlib import Path

from InquirerPy import inquirer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.tree import Tree

from src.scraper import Course

console = Console()
STATE_PATH = Path(__file__).parent.parent / ".runtime" / "last_selection.json"


def make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.fields[name]}", justify="left"),
        BarColumn(bar_width=24),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


from rich.panel import Panel

def print_course_tree(courses: list[Course]) -> None:
    tree = Tree("[bold cyan]Selected Courses[/bold cyan]")
    for course in courses:
        c_branch = tree.add(f"[green]{course.name}[/green]")
        for subject in course.subjects:
            s_branch = c_branch.add(f"[yellow]{subject.name}[/yellow]")
            for chapter in subject.chapters:
                ch_branch = s_branch.add(f"[blue]{chapter.name}[/blue]")
                for video in chapter.videos:
                    ch_branch.add(f"{video.number:02d}. {video.title}")
    console.print(tree)


def print_session_status(courses: list[Course], output_dir: Path) -> tuple[int, int]:
    """Prints a detailed downloader-grade status view of previous selection."""
    from src import downloader

    tree = Tree("[bold cyan]Previous Session & Download Status[/bold cyan]")
    total_videos = 0
    done_videos = 0

    for course in courses:
        c_branch = tree.add(f"[bold green]{course.name}[/bold green]")
        for subject in course.subjects:
            for chapter in subject.chapters:
                ch_tag = "LIVE" if "LIVE" in subject.name.upper() else ("ARCHIVE" if "ARCHIVE" in subject.name.upper() else subject.name)
                ch_branch = c_branch.add(f"[bold yellow]{chapter.name}[/bold yellow] [dim]({ch_tag})[/dim]")
                for video in chapter.videos:
                    total_videos += 1
                    video_out = downloader.build_out_path(
                        output_dir, course.name, subject.name,
                        chapter.name, video.number, video.title,
                        f"{video.number:02d}_{video.title}.mp4"
                    )
                    if video_out.exists():
                        done_videos += 1
                        ch_branch.add(f"[green]✓ {video.number:02d}. {video.title}[/green] [dim](Downloaded)[/dim]")
                    else:
                        temp_dir = video_out.parent / ".temp"
                        if temp_dir.exists():
                            ch_branch.add(f"[yellow]⏳ {video.number:02d}. {video.title}[/yellow] [italic yellow](Partially downloaded)[/italic yellow]")
                        else:
                            ch_branch.add(f"[dim]⏳ {video.number:02d}. {video.title} (Pending)[/dim]")

    console.print()
    console.print(tree)
    rem = total_videos - done_videos
    console.print(f"\n[bold]Status:[/bold] [green]{done_videos}[/green] completed, [yellow]{rem}[/yellow] remaining of [cyan]{total_videos}[/cyan] total videos.\n")
    return total_videos, done_videos


def select_workflow(all_courses: list[Course], cookies: dict, scraper, output_dir: Path) -> list[Course]:
    """Handles resume check with full status view or fresh course + chapter selection."""
    state = _load_state()
    saved_course_ids = state.get("courses", [])
    saved_ch_map = state.get("chapters", {})

    if saved_course_ids:
        matched_courses = [c for c in all_courses if c.id in saved_course_ids]
        if matched_courses:
            # Populate course tree to inspect real on-disk status
            console.print("\n[dim]Checking previous session status...[/dim]")
            for course in matched_courses:
                scraper.get_course_tree(course, cookies)
                for subject in course.subjects:
                    if subject.id in saved_ch_map:
                        saved_ids = saved_ch_map[subject.id]
                        id_to_ch = {ch.id: ch for ch in subject.chapters}
                        ordered = [id_to_ch[cid] for cid in saved_ids if cid in id_to_ch]
                        subject.chapters = ordered
                    else:
                        subject.chapters = []
                course.subjects = [s for s in course.subjects if s.chapters]
            matched_courses = [c for c in matched_courses if c.subjects]

            if matched_courses:
                total, done = print_session_status(matched_courses, output_dir)
                rem = total - done

                choice = inquirer.select(
                    message="Resume previous selection?",
                    choices=[
                        {
                            "name": f"▶ Yes — Resume & download remaining ({rem} videos)",
                            "value": "resume",
                        },
                        {
                            "name": "✎ No  — Select fresh courses/chapters",
                            "value": "fresh",
                        },
                    ],
                ).execute()

                if choice == "resume":
                    return matched_courses

    # Fresh selection flow
    choices = [{"name": c.name, "value": c} for c in all_courses]
    selected_courses = inquirer.checkbox(
        message="Select courses to download (Space to toggle, Enter to confirm):",
        choices=choices,
    ).execute()

    if not selected_courses:
        return []

    console.print("\nFetching course contents via API...")
    for course in selected_courses:
        console.print(f"  [cyan]{course.name}[/cyan]")
        scraper.get_course_tree(course, cookies)

    new_saved_ch_map = {}
    for course in selected_courses:
        for subject in course.subjects:
            choices = [
                {"name": f"{ch.name} ({len(ch.videos)} videos)", "value": ch, "enabled": True}
                for ch in subject.chapters
            ]
            selected_chs = inquirer.checkbox(
                message=f"[{subject.name}] — pick chapters:",
                choices=choices,
            ).execute()

            if not selected_chs:
                subject.chapters = []
                new_saved_ch_map[subject.id] = []
                continue

            # Show numbered list for reordering
            console.print(f"\n[bold]Download order for [{subject.name}]:[/bold]")
            for i, ch in enumerate(selected_chs, 1):
                console.print(f"  [dim]{i}.[/dim] {ch.name}")

            raw = inquirer.text(
                message="Enter new order (e.g. 3,1,2) or Enter to keep:",
            ).execute().strip()

            if raw:
                try:
                    indices = [int(x.strip()) - 1 for x in raw.split(",")]
                    selected_chs = [selected_chs[i] for i in indices if 0 <= i < len(selected_chs)]
                except (ValueError, IndexError):
                    console.print("[yellow]Invalid order input — keeping original.[/yellow]")

            subject.chapters = selected_chs
            new_saved_ch_map[subject.id] = [ch.id for ch in selected_chs]

        course.subjects = [s for s in course.subjects if s.chapters]

    # Save fresh state
    state["courses"] = [c.id for c in selected_courses]
    state["chapters"] = new_saved_ch_map
    _save_state(state)

    return [c for c in selected_courses if c.subjects]


def print_summary(total: int, completed: int, skipped: int, failed: int, output_dir) -> None:
    table = Table(title="Download Summary", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count / Path", style="bold")
    table.add_row("Total Items", str(total))
    table.add_row("Downloaded", f"[green]{completed}[/green]")
    table.add_row("Skipped (Existing)", f"[yellow]{skipped}[/yellow]")
    table.add_row("Failed", f"[red]{failed}[/red]" if failed else "[green]0[/green]")
    table.add_row("Save Directory", f"[cyan]{output_dir}[/cyan]")
    console.print()
    console.print(table)



