"""End-to-end pipeline tests: ingest -> filter -> score -> rank -> dossier."""

from __future__ import annotations

from master_engine import rank_parcels, run
from parcels import ingest
from spatial import SpatialContext, enrich_and_filter


def test_rank_orders_by_score_descending(cfg):
    ctx = SpatialContext(cfg.transmission_lines_path, cfg.substations_path)
    survivors = enrich_and_filter(ingest(cfg), ctx, cfg)
    ranked = rank_parcels(survivors, cfg)

    scores = [rp.score.total for rp in ranked]
    assert scores == sorted(scores, reverse=True)
    assert [rp.rank for rp in ranked] == list(range(1, len(ranked) + 1))


def test_top_ranked_is_the_esmeralda_anomaly(cfg):
    ctx = SpatialContext(cfg.transmission_lines_path, cfg.substations_path)
    survivors = enrich_and_filter(ingest(cfg), ctx, cfg)
    ranked = rank_parcels(survivors, cfg)
    assert ranked[0].parcel.parcel_id == "NV-ESM-0417"
    assert ranked[0].valuation.arbitrage_multiple > 1.0


def test_run_produces_a_dossier_artifact(cfg):
    exit_code = run(cfg, top_n=5)
    assert exit_code == 0
    artifacts = list(cfg.output_dir.glob("lve_dossier_*"))
    assert artifacts, "expected a dossier file in the output dir"


def test_run_writes_html_when_pdf_unavailable(cfg):
    run(cfg, top_n=3)
    # pdfkit/wkhtmltopdf absent in CI -> HTML fallback must still be produced.
    htmls = list(cfg.output_dir.glob("lve_dossier_*.html"))
    assert htmls
    content = htmls[0].read_text(encoding="utf-8")
    assert "Latent Arbitrage" in content


def test_run_returns_error_when_nothing_survives(cfg):
    # Impossible headroom requirement -> every parcel disqualified.
    object.__setattr__(cfg, "min_substation_headroom_mw", 10_000.0)
    assert run(cfg, top_n=5) == 1
