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


def select_courses(courses: list[Course]) -> list[Course]:
    state = _load_state()
    last_ids = set(state.get("courses", []))

    choices = [
        {
            "name": c.name,
            "value": c,
            "enabled": (c.id in last_ids) if last_ids else True,
        }
        for c in courses
    ]

    selected = inquirer.checkbox(
        message="Select courses to download (Space to toggle, Enter to confirm):",
        choices=choices,
    ).execute()

    if selected is not None:
        state["courses"] = [c.id for c in selected]
        _save_state(state)

    return selected or []


def select_chapters(courses: list[Course]) -> list[Course]:
    """Pick chapters per subject, with last selection and order remembered."""
    state = _load_state()
    saved_ch_map = state.get("chapters", {})

    for course in courses:
        for subject in course.subjects:
            saved_ids = saved_ch_map.get(subject.id, None)

            if saved_ids is not None:
                # Prioritize previous order, then append any new chapters
                id_to_ch = {ch.id: ch for ch in subject.chapters}
                ordered_chapters = [id_to_ch[cid] for cid in saved_ids if cid in id_to_ch]
                ordered_chapters += [ch for ch in subject.chapters if ch.id not in saved_ids]

                saved_set = set(saved_ids)
                choices = [
                    {
                        "name": f"{ch.name} ({len(ch.videos)} videos)",
                        "value": ch,
                        "enabled": (ch.id in saved_set),
                    }
                    for ch in ordered_chapters
                ]
            else:
                choices = [
                    {
                        "name": f"{ch.name} ({len(ch.videos)} videos)",
                        "value": ch,
                        "enabled": True,
                    }
                    for ch in subject.chapters
                ]

            selected = inquirer.checkbox(
                message=f"[{subject.name}] — pick chapters:",
                choices=choices,
            ).execute()

            if not selected:
                subject.chapters = []
                saved_ch_map[subject.id] = []
                continue

            # Show numbered list for reordering
            console.print(f"\n[bold]Download order for [{subject.name}]:[/bold]")
            for i, ch in enumerate(selected, 1):
                console.print(f"  [dim]{i}.[/dim] {ch.name}")

            raw = inquirer.text(
                message="Enter new order (e.g. 3,1,2) or Enter to keep:",
            ).execute().strip()

            if raw:
                try:
                    indices = [int(x.strip()) - 1 for x in raw.split(",")]
                    selected = [selected[i] for i in indices if 0 <= i < len(selected)]
                except (ValueError, IndexError):
                    console.print("[yellow]Invalid order input — keeping original.[/yellow]")

            subject.chapters = selected
            saved_ch_map[subject.id] = [ch.id for ch in selected]

    state["chapters"] = saved_ch_map
    _save_state(state)

    for course in courses:
        course.subjects = [s for s in course.subjects if s.chapters]
    return courses


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



