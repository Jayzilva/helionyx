"""Helionyx command-line interface (SRS §4.7)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer

from helionyx import DISCLAIMER, __version__
from helionyx.errors import HelionyxError

app = typer.Typer(name="helionyx", help="Hybrid renewable energy sizing for AI assistants.", no_args_is_help=True,
                  add_completion=False)
export_app = typer.Typer(help="Export inputs and results.", no_args_is_help=True)
pack_app = typer.Typer(help="Country data packs.", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluations.", no_args_is_help=True)
app.add_typer(export_app, name="export")
app.add_typer(pack_app, name="pack")
app.add_typer(eval_app, name="eval")


def _fail(exc: HelionyxError) -> None:
    typer.secho(f"{exc.code.value} {exc.code.name}: {exc.message}", fg=typer.colors.RED, err=True)
    typer.secho(f"Hint: {exc.hint}", err=True)
    raise typer.Exit(2)


def _app():  # type: ignore[no-untyped-def]
    from helionyx.services.context import Helionyx

    return Helionyx()


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def serve(
    transport: Annotated[str, typer.Option(help="stdio or http")] = "stdio",
    host: Annotated[str, typer.Option(help="Bind address for http; non-local needs HNX_API_KEY")] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8080,
) -> None:
    """Start the MCP server."""
    if transport == "stdio":
        from helionyx.api.mcp_server import mcp

        mcp.run("stdio")
    elif transport == "http":
        import os

        import uvicorn

        from helionyx.api.http_app import create_app

        if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("HNX_API_KEY"):
            typer.secho("Refusing to bind to a non-local address without HNX_API_KEY (bearer key auth). "
                        "OAuth with Entra ID arrives in v1.0.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        uvicorn.run(create_app(host), host=host, port=port, log_level="info")
    else:
        raise typer.BadParameter("transport must be stdio or http")


def _print_results(core: dict, top: int) -> None:  # type: ignore[type-arg]
    from helionyx.services.results import get_results

    res = get_results(core["app"], core["run_id"], top_n=top)
    cur = res["currency"]
    typer.echo(f"Run {res['run_id']}  scenario hash {res['scenario_hash'][:19]}…  "
               f"{res['feasible_count']} feasible / {res['infeasible_count']} infeasible")
    typer.echo(f"{'rank':>4} {'PV kWp':>8} {'wind':>5} {'BESS kWh':>9} {'gen kW':>7} {'NPC ' + cur:>16} "
               f"{'LCOE':>8} {'RF %':>6} {'payback':>8}")
    for c in res["candidates"]:
        s, m = c["sizes"], c["metrics"]
        pb = "—" if m["simple_payback_yr"] is None else f"{m['simple_payback_yr']:.1f}"
        lc = "—" if m["lcoe_per_kwh"] is None else f"{m['lcoe_per_kwh']:.2f}"
        typer.echo(f"{c['rank']:>4} {s['pv_kwp']:>8.1f} {s['wind_count']:>5.0f} {s['bess_kwh']:>9.0f} "
                   f"{s['genset_kw']:>7.0f} {m['npc']:>16,.0f} {lc:>8} {m['renewable_fraction_pct']:>6.1f} {pb:>8}")
    if res["base_case"]:
        b = res["base_case"]
        typer.echo(f"base ({b['label']}): NPC {b['metrics']['npc']:,.0f} {cur}")
    for w in res["warnings"]:
        typer.secho(f"  {w['code']}: {w['message']}", fg=typer.colors.YELLOW)
    typer.echo(f"\n{DISCLAIMER}")


@app.command("run")
def run_cmd(
    scenario_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    solver: Annotated[str, typer.Option()] = "native",
    top: Annotated[int, typer.Option()] = 5,
    sort_by: Annotated[str, typer.Option()] = "npc",
) -> None:
    """Run a study or scenario YAML file without an MCP client."""
    from helionyx.services.run import start_run
    from helionyx.services.study import create_from_study

    hx = _app()
    try:
        scn = create_from_study(hx, scenario_file)
        if scn["errors"]:
            for e in scn["errors"]:
                typer.secho(f"{e['code']} {e.get('path') or ''}: {e['message']}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        typer.echo(f"Scenario {scn['scenario_id']} ({scn['candidate_count']} candidates)")
        job = start_run(hx, scn["scenario_id"], solver, sort_by)
        with typer.progressbar(length=100, label="Simulating") as bar:
            done = 0.0
            while True:
                st = hx.jobs.wait(job["job_id"], 0.5)
                pct = float(st.get("progress_pct") or 0.0)
                bar.update(int(pct - done))
                done = float(int(pct))
                if st["state"] not in ("queued", "running"):
                    break
        if st["state"] != "completed":
            typer.secho(f"Job {st['state']}: {(st.get('error') or {}).get('message')}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        _print_results({"app": hx, "run_id": job["run_id"]}, top)
    except HelionyxError as exc:
        _fail(exc)
    finally:
        hx.close()


@app.command()
def results(run_id: str, top: Annotated[int, typer.Option()] = 10) -> None:
    """Print the ranked results of a run."""
    hx = _app()
    try:
        _print_results({"app": hx, "run_id": run_id}, top)
    except HelionyxError as exc:
        _fail(exc)
    finally:
        hx.close()


@export_app.command("homer")
def export_homer(scenario_id: str, directory: Path) -> None:
    """Write HOMER-importable series and a parameter sheet to DIRECTORY."""
    from helionyx.services.export import export_homer_csv

    hx = _app()
    try:
        out = export_homer_csv(hx, scenario_id=scenario_id)
        directory.mkdir(parents=True, exist_ok=True)
        for f in out["files"]:
            shutil.copy(f, directory / Path(f).name)
            typer.echo(directory / Path(f).name)
    except HelionyxError as exc:
        _fail(exc)
    finally:
        hx.close()


@export_app.command("report")
def export_report(run_id: str, format: Annotated[str, typer.Option("--format")] = "md",  # noqa: A002
                  output: Annotated[Path | None, typer.Option()] = None) -> None:
    """Write a client report for a run."""
    from helionyx.services.export import export_report as do_export

    hx = _app()
    try:
        out = do_export(hx, run_id, format)
        if output:
            shutil.copy(out["path"], output)
        typer.echo(output or out["path"])
    except HelionyxError as exc:
        _fail(exc)
    finally:
        hx.close()


@pack_app.command("validate")
def pack_validate(path: Path) -> None:
    """Validate a data pack directory against its schemas."""
    from helionyx.infra.packs import load_pack_from

    pack, errors = load_pack_from(path)
    if errors:
        for e in errors:
            typer.secho(e, fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    assert pack is not None
    typer.echo(f"OK {pack.ref}: {len(pack.tariffs)} tariffs, {len(pack.components)} components, "
               f"{len(pack.archetypes)} archetypes")


@pack_app.command("build")
def pack_build(path: Path, out_dir: Path) -> None:
    """Build a release archive and checksum manifest from a pack directory (maintainers)."""
    from helionyx.infra.pack_update import build

    try:
        m = build(path, out_dir)
    except HelionyxError as exc:
        _fail(exc)
    typer.echo(json.dumps(m, indent=2))


@pack_app.command("update")
def pack_update(country: Annotated[str, typer.Option()] = "lk",
                manifest: Annotated[str | None, typer.Option(help="Manifest URL or path")] = None) -> None:
    """Download, verify (SHA-256) and install a newer data-pack release into the workspace."""
    from helionyx.infra.pack_update import update
    from helionyx.infra.settings import Settings

    try:
        out = update(Settings().ensure().workspace, country, manifest)
    except HelionyxError as exc:
        _fail(exc)
    typer.echo(json.dumps(out, indent=2))


parity_app = typer.Typer(help="HOMER Pro parity study.", no_args_is_help=True)
app.add_typer(parity_app, name="parity")


@parity_app.command("compare")
def parity_compare(homer_csv: Path, output: Annotated[Path | None, typer.Option()] = None,
                   cases_dir: Annotated[Path, typer.Option()] = Path("reference_cases")) -> None:
    """Compare HOMER Pro results (filled template CSV) with Helionyx runs of RC-1..RC-3."""
    from helionyx.services.parity import compare, to_markdown

    hx = _app()
    try:
        md = to_markdown(compare(hx, homer_csv, cases_dir))
    except HelionyxError as exc:
        _fail(exc)
    finally:
        hx.close()
    if output:
        output.write_text(md, encoding="utf-8")
    typer.echo(md)


@eval_app.command("grounding")
def eval_grounding(transcripts_dir: Path, threshold: Annotated[float, typer.Option()] = 0.95) -> None:
    """Score assistant transcripts for numbers not traceable to tool outputs."""
    from helionyx.evals import run_grounding

    report = run_grounding(transcripts_dir, threshold)
    typer.echo(json.dumps(report, indent=2))
    raise typer.Exit(0 if report["passed"] else 1)


if __name__ == "__main__":  # pragma: no cover
    app()
