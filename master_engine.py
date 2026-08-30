"""
master_engine.py — Core LVE-LAP pipeline orchestrator.

    Ingest (Apify or mock)
      -> Spatial enrich + hard-gate filter (transmission / substation / ingress)
      -> Latent Arbitrage Scoring (LAS)
      -> HBU valuation
      -> Rank + generate institutional PDF/HTML dossier

Run:
    python master_engine.py            # top 10 (or --top N)
    python master_engine.py --top 5
    python master_engine.py --mock     # force synthetic parcels
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import List

from config import Config, ConfigError, load_config
from confidence import score_confidence
from lifecycle import build_lifecycle, summarize_lifecycle
from parcels import Parcel, ingest
from report_generator import RankedParcel, generate_dossier
from scoring import score_parcel
from spatial import SpatialContext, enrich_and_filter
from valuation import model_hbu
from water_rights import WaterRightsRepository, enrich_water_rights


def _log(msg: str) -> None:
    print(f"[engine] {msg}")


def rank_parcels(parcels: List[Parcel], cfg: Config) -> List[RankedParcel]:
    """Score + value every surviving parcel and sort by LAS descending."""
    ranked: List[RankedParcel] = []
    for p in parcels:
        score = score_parcel(
            p,
            baseline_per_acre=cfg.regional_baseline_price_per_acre,
            dom_threshold=cfg.days_on_market_threshold,
            min_headroom_mw=cfg.min_substation_headroom_mw,
            surface_water_bonus_miles=cfg.surface_water_bonus_miles,
        )
        valuation = model_hbu(p, surface_water_bonus_miles=cfg.surface_water_bonus_miles)
        conf = score_confidence(p)
        stages = build_lifecycle(p, valuation)
        summary = summarize_lifecycle(stages, valuation)
        ranked.append(RankedParcel(
            parcel=p, score=score, valuation=valuation,
            confidence=conf, lifecycle=stages, lifecycle_summary=summary,
        ))

    ranked.sort(key=lambda rp: rp.score.total, reverse=True)
    for i, rp in enumerate(ranked, start=1):
        rp.rank = i
    return ranked


@dataclass
class AnalysisResult:
    """Outcome of a full analysis pass — consumed by both the CLI and dashboard."""

    ranked: List[RankedParcel]           # survivors, scored + sorted (rank set)
    disqualified: List[Parcel]           # parcels dropped by a gate
    ingested: int                        # total parcels ingested
    water_rights_matched: int            # parcels enriched from the water DB
    warnings: List[str]                  # non-fatal messages for display


def analyze(cfg: Config, parcels: List[Parcel] | None = None) -> AnalysisResult:
    """Run ingest → water-rights → spatial filter → score/rank. No side effects.

    This is the pure pipeline: no logging, no dossier. `run()` wraps it for the
    CLI; the Streamlit dashboard calls it directly. Pass `parcels` to analyze a
    pre-built set (e.g. a synthetic corridor) instead of running ingestion.
    """
    warnings = list(cfg.warnings)

    if parcels is None:
        parcels = ingest(cfg)
    if not parcels:
        return AnalysisResult([], [], 0, 0, warnings)

    wr_repo = WaterRightsRepository(cfg.water_rights_db_path)
    matched = 0
    if wr_repo.warning:
        warnings.append(wr_repo.warning)
    else:
        matched = enrich_water_rights(parcels, wr_repo)

    ctx = SpatialContext(
        cfg.transmission_lines_path, cfg.substations_path, cfg.hydrography_path
    )
    if not ctx.lines:
        warnings.append("No transmission lines loaded — check data/gis/.")
    survivors = enrich_and_filter(parcels, ctx, cfg)
    disqualified = [p for p in parcels if not p.passes_spatial_gate]

    ranked = rank_parcels(survivors, cfg)
    return AnalysisResult(ranked, disqualified, len(parcels), matched, warnings)


def run(cfg: Config, top_n: int) -> int:
    for warn in cfg.warnings:
        _log(f"WARNING: {warn}")

    _log(f"Run mode: {cfg.run_mode.upper()} | corridor: {', '.join(cfg.target_states)}")

    result = analyze(cfg)
    _log(f"Ingested {result.ingested} candidate parcels.")
    if result.ingested == 0:
        _log("No parcels ingested — nothing to score. Exiting.")
        return 1

    if result.water_rights_matched or not any("Water-rights DB" in w for w in result.warnings):
        _log(f"Water-rights: {result.water_rights_matched} parcels enriched.")
    for warn in result.warnings:
        if warn not in cfg.warnings:
            _log(f"WARNING: {warn}")

    _log(f"{len(result.ranked)} parcels passed spatial/infrastructure gates; "
         f"{len(result.disqualified)} disqualified.")
    for p in result.disqualified:
        _log(f"  - {p.parcel_id}: {p.disqualification_reason}")

    if not result.ranked:
        _log("No parcels survived filtering. Adjust thresholds or corridor.")
        return 1

    top = result.ranked[: max(1, top_n)]
    _log(f"Top {len(top)} anomalies by Latent Arbitrage Score:")
    for rp in top:
        _log(f"  #{rp.rank} {rp.parcel.parcel_id} "
             f"({rp.parcel.county}, {rp.parcel.state}) "
             f"LAS={rp.score.total} | {rp.valuation.arbitrage_multiple}x arbitrage")

    # 5. Dossier
    artifact = generate_dossier(top, cfg)
    _log(f"Dossier written: {artifact}")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Land Value Engine (LVE-LAP) pipeline")
    parser.add_argument("--top", type=int, default=10, help="Number of anomalies to report")
    parser.add_argument("--mock", action="store_true", help="Force synthetic parcels")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"[config] ERROR: {e}", file=sys.stderr)
        return 2

    if args.mock:
        cfg = Config(**{**cfg.__dict__, "run_mode": "mock"})

    return run(cfg, args.top)


if __name__ == "__main__":
    raise SystemExit(main())
