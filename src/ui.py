"""UI: Rich console helpers + InquirerPy course selection."""
from InquirerPy import inquirer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from src.scraper import Course

console = Console()


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
    choices = [{"name": c.name, "value": c} for c in courses]
    selected = inquirer.checkbox(
        message="Select courses to download (Space to toggle, Enter to confirm):",
        choices=choices,
    ).execute()
    return selected


def select_chapters(courses: list[Course]) -> list[Course]:
    """Pick chapters per subject, then optionally reorder them."""
    for course in courses:
        for subject in course.subjects:
            choices = [
                {"name": f"{ch.name} ({len(ch.videos)} videos)", "value": ch}
                for ch in subject.chapters
            ]
            selected = inquirer.checkbox(
                message=f"[{subject.name}] — pick chapters:",
                choices=choices,
                default=subject.chapters,
            ).execute()

            if not selected:
                subject.chapters = []
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

    for course in courses:
        course.subjects = [s for s in course.subjects if s.chapters]
    return courses


def print_manifest(items: list[dict]) -> None:
    table = Table(title="Download Manifest", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Chapter", style="blue")
    table.add_column("Video", style="cyan")
    table.add_column("Type", style="magenta", width=6)
    table.add_column("URL", style="green")
    table.add_column("PDFs", style="yellow")
    for i, item in enumerate(items, 1):
        v = item["video"]
        pdfs = sum(1 for u in [v.lecture_sheet_url, v.note_url,
                               v.practice_sheet_url, v.solve_sheet_url] if u)
        vtype = "YT" if v.video_type == "youtube" else "Bunny"
        table.add_row(
            str(i),
            item["chapter"].name[:25],
            v.title,
            vtype,
            "[green]OK[/green]" if item["video_url"] else "[red]MISS[/red]",
            str(pdfs) if pdfs else "-",
        )
    console.print(table)


