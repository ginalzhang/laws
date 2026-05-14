"""
CLI entry point — pvfy

Commands:
  pvfy process <pdf>            Process a single PDF, print JSON results
  pvfy batch   <folder>         Process all PDFs in a folder
  pvfy serve                    Start the FastAPI review UI server
  pvfy import-voters <csv>      Validate and preview voter roll CSV
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich import print as rprint
from rich.console import Console
from rich.table import Table

load_dotenv()

app     = typer.Typer(name="pvfy", help="Petition signature verification pipeline")
admin_app = typer.Typer(help="Admin user management")
app.add_typer(admin_app, name="admin")
console = Console()

ADMIN_ROLES = {"boss", "admin", "field_manager", "worker", "petitioner", "office_worker", "evan", "evann"}


def _require_voter_roll() -> Path:
    path = os.getenv("VOTER_ROLL_CSV")
    if not path:
        console.print("[red]Error:[/red] VOTER_ROLL_CSV env var not set. "
                      "Add it to .env or pass --voter-roll.")
        raise typer.Exit(1)
    p = Path(path)
    if not p.exists():
        console.print(f"[red]Error:[/red] Voter roll not found: {p}")
        raise typer.Exit(1)
    return p


@app.command()
def process(
    pdf: Path = typer.Argument(..., help="Path to petition PDF"),
    voter_roll: Optional[Path] = typer.Option(None, "--voter-roll", "-v",
                                              help="Voter roll CSV (overrides VOTER_ROLL_CSV)"),
    project_id: Optional[str]  = typer.Option(None, "--project-id", "-p"),
    output: Optional[Path]     = typer.Option(None, "--output", "-o",
                                              help="Write JSON to file instead of stdout"),
    save_db: bool              = typer.Option(False, "--save-db",
                                              help="Persist results to database"),
    summary: bool              = typer.Option(False, "--summary", "-s",
                                              help="Print summary table instead of full JSON"),
):
    """Process a single petition PDF and output verification results."""
    from ..pipeline import Pipeline
    from ..storage import Database

    vr_path = voter_roll or _require_voter_roll()
    pipeline = Pipeline(voter_roll_csv=vr_path)

    with console.status(f"[bold green]Processing {pdf.name}…"):
        result = pipeline.process(pdf, project_id=project_id)

    if save_db:
        db = Database()
        db.save_project(result)
        console.print(f"[green]Saved project {result.project_id} to database.[/green]")

    if summary:
        _print_summary_table(result)
    else:
        data = result.model_dump(mode="json")
        text = json.dumps(data, indent=2)
        if output:
            output.write_text(text)
            console.print(f"[green]Results written to {output}[/green]")
        else:
            print(text)


@app.command()
def batch(
    folder: Path = typer.Argument(..., help="Folder containing petition PDFs"),
    voter_roll: Optional[Path] = typer.Option(None, "--voter-roll", "-v"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", "-o"),
    save_db: bool              = typer.Option(True, "--save-db/--no-save-db"),
):
    """Process every PDF in a folder."""
    from ..pipeline import Pipeline
    from ..storage import Database

    pdfs = list(folder.glob("*.pdf")) + list(folder.glob("*.PDF"))
    if not pdfs:
        console.print(f"[yellow]No PDFs found in {folder}[/yellow]")
        raise typer.Exit(0)

    vr_path = voter_roll or _require_voter_roll()
    pipeline = Pipeline(voter_roll_csv=vr_path)
    db = Database() if save_db else None

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    for pdf in pdfs:
        with console.status(f"Processing {pdf.name}…"):
            result = pipeline.process(pdf)

        _print_summary_table(result)

        if db:
            db.save_project(result)

        if output_dir:
            out = output_dir / f"{pdf.stem}_results.json"
            out.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
            console.print(f"  → {out}")


@app.command()
def extract(
    pdf: Path = typer.Argument(..., help="Path to petition PDF"),
    output: Optional[Path] = typer.Option(None, "--output", "-o",
                                          help="Write JSON to file instead of stdout"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
):
    """
    OCR a petition PDF and print extracted fields — no voter roll needed.

    Use this to verify Tesseract is reading your form's columns correctly
    before wiring up matching.
    """
    from ..pipeline import Pipeline

    pipeline = Pipeline(voter_roll_csv=None)

    with console.status(f"[bold green]Extracting from {pdf.name}…"):
        result = pipeline.process(pdf, project_id="extract-only")

    rows = []
    for sig in result.signatures:
        e = sig.extracted
        n = sig.normalized
        rows.append({
            "line":    e.line_number,
            "page":    e.page,
            "raw": {
                "name":    e.raw_name,
                "address": e.raw_address,
                "date":    e.raw_date,
                "sig_present": e.signature_present,
                "ocr_conf":    e.ocr_confidence,
            },
            "normalized": {
                "first":   n.first_name,
                "last":    n.last_name,
                "street":  n.street,
                "city":    n.city,
                "state":   n.state,
                "zip":     n.zip_code,
            },
        })

    indent = 2 if pretty else None
    text   = __import__("json").dumps(rows, indent=indent)

    if output:
        output.write_text(text)
        console.print(f"[green]Extracted {len(rows)} rows → {output}[/green]")
    else:
        # Also print a readable table to stderr so you can see it at a glance
        table = Table(title=f"{pdf.name} — {len(rows)} rows extracted", show_lines=True)
        table.add_column("#",       style="dim",    width=4)
        table.add_column("Raw name",                width=24)
        table.add_column("Raw address",             width=32)
        table.add_column("Date",    style="cyan",   width=12)
        table.add_column("Sig",                     width=4)
        table.add_column("OCR%",    style="dim",    width=6)
        for row in rows:
            table.add_row(
                str(row["line"]),
                row["raw"]["name"]    or "[dim]—[/dim]",
                row["raw"]["address"] or "[dim]—[/dim]",
                row["raw"]["date"]    or "[dim]—[/dim]",
                "✍" if row["raw"]["sig_present"] else "—",
                str(row["raw"]["ocr_conf"] or ""),
            )
        console.print(table)
        print(text)


@app.command(name="import-voters")
def import_voters(
    csv: Path = typer.Argument(..., help="Voter roll CSV to validate"),
):
    """Validate voter roll CSV columns and preview the first 5 rows."""
    import pandas as pd

    df = pd.read_csv(csv, dtype=str, nrows=10).fillna("")
    df.columns = [c.lower().strip() for c in df.columns]

    required = {"voter_id", "last_name", "first_name", "street_address"}
    missing  = required - set(df.columns)

    if missing:
        console.print(f"[red]Missing required columns:[/red] {missing}")
        console.print(f"[yellow]Found:[/yellow] {list(df.columns)}")
        raise typer.Exit(1)

    console.print(f"[green]✓ Voter roll looks good.[/green] Columns: {list(df.columns)}")
    console.print(f"\nFirst 5 rows:")
    table = Table(show_header=True)
    for col in df.columns[:8]:  # cap at 8 cols for readability
        table.add_column(col, style="cyan")
    for _, row in df.head(5).iterrows():
        table.add_row(*[str(row[c]) for c in df.columns[:8]])
    console.print(table)


@admin_app.command("create-user")
def admin_create_user(
    email: str = typer.Option(..., "--email", prompt=True),
    full_name: str = typer.Option(..., "--full-name", prompt=True),
    role: str = typer.Option("boss", "--role"),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without writing to the database"),
):
    """Create an admin or workforce user in the configured database."""
    if role not in ADMIN_ROLES:
        console.print(f"[red]Invalid role:[/red] {role}. Expected one of: {', '.join(sorted(ADMIN_ROLES))}")
        raise typer.Exit(1)
    if not password or len(password) < 12:
        console.print("[red]Password must be at least 12 characters.[/red]")
        raise typer.Exit(1)

    from ..auth import hash_password
    from ..storage import Database

    database = Database()
    existing = database.get_user_by_email(email)
    if existing:
        console.print(f"[red]User already exists:[/red] {email}")
        raise typer.Exit(1)
    if dry_run:
        console.print(f"[green]Dry run OK:[/green] would create {email} as {role}")
        return
    user = database.create_user(email, hash_password(password), role, full_name)
    console.print(f"[green]Created user[/green] id={user.id} email={user.email} role={user.role}")


@admin_app.command("list-users")
def admin_list_users(role: Optional[str] = typer.Option(None, "--role")):
    """List users for login migration/audit checks."""
    from ..storage import Database

    database = Database()
    users = database.list_users(role=role)
    table = Table(title="Users")
    table.add_column("ID", justify="right")
    table.add_column("Email")
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Active")
    for user in users:
        table.add_row(str(user.id), user.email, user.full_name, user.role, "yes" if user.is_active else "no")
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
):
    """Start the FastAPI review UI server."""
    import uvicorn
    uvicorn.run(
        "petition_verifier.api:app",
        host=host,
        port=port,
        reload=reload,
    )


def _print_summary_table(result) -> None:
    s = result.summary()
    table = Table(title=f"Project: {s['project_id']}")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Total lines",   str(s["total"]))
    table.add_row("[green]Approved[/green]",   str(s["approved"]))
    table.add_row("[yellow]Review[/yellow]",   str(s["review"]))
    table.add_row("[red]Rejected[/red]",       str(s["rejected"]))
    table.add_row("[dim]Duplicates[/dim]",     str(s["duplicates"]))
    table.add_row("Auto-approve rate", f"{s['auto_rate_pct']}%")
    console.print(table)


if __name__ == "__main__":
    app()
