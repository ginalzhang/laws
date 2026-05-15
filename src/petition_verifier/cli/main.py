# ruff: noqa: UP045
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
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

app     = typer.Typer(name="pvfy", help="Petition signature verification pipeline")
db_app  = typer.Typer(help="Database migration commands")
admin_app = typer.Typer(help="Admin user commands")
console = Console()
app.add_typer(db_app, name="db")
app.add_typer(admin_app, name="admin")

VALID_USER_ROLES = {
    "boss",
    "admin",
    "field_manager",
    "worker",
    "petitioner",
    "office_worker",
}


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


def _alembic_config():
    from alembic.config import Config

    return Config(str(PROJECT_ROOT / "alembic.ini"))


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Argument("head", help="Alembic revision to upgrade to"),
) -> None:
    """Apply database migrations."""
    from alembic import command

    from ..storage.database import has_unversioned_application_schema

    if revision == "head" and has_unversioned_application_schema():
        console.print(
            "[red]Error:[/red] Existing application tables are not Alembic-versioned. "
            "If this is the current production schema, run [bold]pvfy db stamp head[/bold] "
            "once before enabling automatic migration upgrades."
        )
        raise typer.Exit(1)

    command.upgrade(_alembic_config(), revision)


@db_app.command("revision")
def db_revision(
    message: str = typer.Option(..., "--message", "-m", help="Migration message"),
    autogenerate: bool = typer.Option(False, "--autogenerate", "-a"),
) -> None:
    """Create a new Alembic migration revision."""
    from alembic import command

    command.revision(_alembic_config(), message=message, autogenerate=autogenerate)


@db_app.command("stamp")
def db_stamp(
    revision: str = typer.Argument("head", help="Alembic revision to stamp"),
) -> None:
    """Mark an existing database as being at a revision without running DDL."""
    from alembic import command

    command.stamp(_alembic_config(), revision)


@db_app.command("downgrade")
def db_downgrade(
    revision: str = typer.Argument("-1", help="Alembic revision to downgrade to"),
) -> None:
    """Roll back database migrations."""
    from alembic.migration import MigrationContext

    from alembic import command

    from ..storage.database import create_database_engine

    with create_database_engine().connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current == "0001_baseline" and revision in {"-1", "base"}:
        console.print(
            "[red]Error:[/red] Refusing to downgrade below the baseline migration; "
            "that would remove the app's schema. Restore from backup instead."
        )
        raise typer.Exit(1)

    command.downgrade(_alembic_config(), revision)


@admin_app.command("create-user")
def admin_create_user(
    email: str = typer.Argument(..., help="User email used for login"),
    role: str = typer.Argument(..., help="Role to assign"),
    full_name: Optional[str] = typer.Option(None, "--full-name", "-n"),
    phone: str = typer.Option("", "--phone"),
    hourly_wage: float = typer.Option(25.0, "--hourly-wage"),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        envvar="PVFY_ADMIN_PASSWORD",
        help="Initial password. If omitted, prompts securely.",
    ),
) -> None:
    """Create a login user after the database has been migrated."""
    from ..auth import hash_password
    from ..storage import Database
    from ..storage.database import check_schema_current

    normalized_email = email.strip().lower()
    normalized_role = role.strip().lower()
    if normalized_role not in VALID_USER_ROLES:
        allowed = ", ".join(sorted(VALID_USER_ROLES))
        console.print(f"[red]Error:[/red] Invalid role '{role}'. Allowed roles: {allowed}.")
        raise typer.Exit(1)
    if not normalized_email:
        console.print("[red]Error:[/red] Email is required.")
        raise typer.Exit(1)

    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < 6:
        console.print("[red]Error:[/red] Password must be at least 6 characters.")
        raise typer.Exit(1)

    check_schema_current()
    db = Database()
    existing = db.get_user_by_email(normalized_email)
    if existing:
        console.print(f"[red]Error:[/red] User already exists: {normalized_email}")
        raise typer.Exit(1)

    user = db.create_user(
        email=normalized_email,
        password_hash=hash_password(password),
        role=normalized_role,
        full_name=full_name or normalized_email.split("@", maxsplit=1)[0],
        phone=phone,
        hourly_wage=hourly_wage,
    )
    console.print(f"[green]Created user {user.email}[/green] ({user.role})")


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
        from ..storage.database import check_schema_current
        check_schema_current()
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
    if save_db:
        from ..storage.database import check_schema_current
        check_schema_current()
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
    console.print("\nFirst 5 rows:")
    table = Table(show_header=True)
    for col in df.columns[:8]:  # cap at 8 cols for readability
        table.add_column(col, style="cyan")
    for _, row in df.head(5).iterrows():
        table.add_row(*[str(row[c]) for c in df.columns[:8]])
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
