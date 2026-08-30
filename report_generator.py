"""
report_generator.py — Automated PDF dossier generator (PRD Module D).

Renders the top-ranked anomalies into an institutional-grade HTML report via
Jinja2, then compiles to PDF with pdfkit/wkhtmltopdf. If wkhtmltopdf is not
installed, it gracefully leaves the standalone HTML so the pipeline still
produces a shareable artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from parcels import Parcel
from scoring import ScoreBreakdown
from valuation import Valuation

# The 4-stage execution roadmap is identical across dossiers (PRD Module D).
EXECUTION_ROADMAP = [
    {
        "stage": 1,
        "title": "Acquisition",
        "detail": "Lock the parcel via option/purchase agreement; complete title, "
                  "easement, and assessor due diligence to confirm clean ingress.",
    },
    {
        "stage": 2,
        "title": "Interconnection & Permitting",
        "detail": "File the interconnection queue position against the target "
                  "substation headroom; initiate conditional-use and grading permits.",
    },
    {
        "stage": 3,
        "title": "Equity Leverage",
        "detail": "Refinance/recapitalize against the entitled, interconnect-ready "
                  "basis to fund the build without diluting the arbitrage.",
    },
    {
        "stage": 4,
        "title": "Exit / JV Build",
        "detail": "Execute the HBU build (energy/compute node, agrivoltaics, water "
                  "bank) or JV/sell the shovel-ready position at the modeled HBU value.",
    },
]


@dataclass
class RankedParcel:
    """A scored + valued parcel ready for rendering — the full opportunity."""

    parcel: Parcel
    score: ScoreBreakdown
    valuation: Valuation
    rank: int = 0
    confidence: "object" = None       # ConfidenceBreakdown (set in rank_parcels)
    lifecycle: list = None            # List[LifecycleStage]
    lifecycle_summary: "object" = None  # LifecycleSummary
    flags: list = None                # List[SanityFlag]


def _currency(value: float) -> str:
    return f"${value:,.0f}"


def _build_context(ranked: List[RankedParcel], methodology_sections=None) -> dict:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return {
        "generated_at": generated,
        "roadmap": EXECUTION_ROADMAP,
        "parcels": ranked,
        "currency": _currency,
        "count": len(ranked),
        "methodology": methodology_sections or [],
    }


def _render_html(ctx: dict, template_dir: Path) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html"]),
        )
        env.filters["currency"] = _currency
        template = env.get_template("dossier.html")
        return template.render(**ctx)
    except ImportError:
        return _render_html_fallback(ctx)


def _render_html_fallback(ctx: dict) -> str:
    """Minimal HTML if Jinja2 is unavailable — keeps the pipeline functional."""
    rows = []
    for rp in ctx["parcels"]:
        p, s, v = rp.parcel, rp.score, rp.valuation
        rows.append(
            f"<tr><td>{rp.rank}</td><td>{p.parcel_id}</td>"
            f"<td>{p.county}, {p.state}</td><td>{s.total}</td>"
            f"<td>{_currency(p.asking_price)}</td>"
            f"<td>{_currency(v.modeled_hbu_value)}</td>"
            f"<td>{v.arbitrage_multiple}x</td></tr>"
        )
    return (
        "<html><head><meta charset='utf-8'><title>LVE-LAP Dossier</title></head>"
        "<body><h1>Land Value Engine — Latent Arbitrage Dossier</h1>"
        f"<p>Generated {ctx['generated_at']} | {ctx['count']} anomalies</p>"
        "<table border='1' cellpadding='6'><tr><th>Rank</th><th>Parcel</th>"
        "<th>Location</th><th>LAS</th><th>Asking</th><th>HBU</th><th>Multiple</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
    )


def generate_dossier(ranked: List[RankedParcel], cfg) -> Path:
    """Render the ranked parcels to HTML and (best-effort) PDF.

    Returns the path to the primary artifact (PDF if produced, else HTML).
    """
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    from methodology import methodology as _methodology
    ctx = _build_context(ranked, methodology_sections=_methodology(cfg))
    html = _render_html(ctx, cfg.template_dir)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = cfg.output_dir / f"lve_dossier_{stamp}.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = cfg.output_dir / f"lve_dossier_{stamp}.pdf"
    try:
        import pdfkit

        pdfkit.from_string(html, str(pdf_path))
        return pdf_path
    except Exception as exc:  # pdfkit missing, or wkhtmltopdf binary absent
        print(
            f"[report] PDF rendering unavailable ({exc.__class__.__name__}); "
            f"wrote standalone HTML instead. Install wkhtmltopdf for PDF output."
        )
        return html_path
