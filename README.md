# Land Value Engine & Latent Arbitrage Protocol (LVE-LAP)

An autonomous intelligence pipeline that identifies, scores, and packages
**mispriced, high-latent-value land assets** across the Western US corridor
(NV · AZ · UT · NM). It contrasts current asking prices against
highest-and-best-use (HBU) utility — modular energy/compute nodes,
agrivoltaics, water banking, mineral extraction — and produces
institutional-grade diagnostic dossiers with a state-machine execution playbook.

> Modeled figures are diagnostic estimates, **not appraisals or investment
> advice**.

---

## Pipeline

```
Ingest (Apify or mock)
  → Spatial enrich + hard-gate filter  (transmission buffer · substation headroom · ingress)
  → Latent Arbitrage Scoring (LAS)
  → HBU valuation
  → Rank → institutional PDF / HTML dossier
```

## Quick start

```bash
# 1. (optional) create a virtualenv
python -m venv .venv && source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt
#    for PDF output also install the wkhtmltopdf system binary:
#    macOS:  brew install wkhtmltopdf   |   Debian/Ubuntu: apt install wkhtmltopdf

# 3. configure
cp .env.example .env      # fill in APIFY_API_TOKEN for live ingestion

# 4. run
python master_engine.py            # top 10 anomalies
python master_engine.py --top 5    # top 5
python master_engine.py --mock     # force synthetic corridor (no token needed)
```

**Runs with zero setup.** With no `APIFY_API_TOKEN` the engine falls back to a
deterministic synthetic corridor, and if `wkhtmltopdf` is missing it emits a
standalone HTML dossier instead of PDF — so you can see the full pipeline
end-to-end before wiring up any credentials. Dossiers land in `output_reports/`.

## Latent Arbitrage Score (LAS)

Composite 0–100 score, weighted per the PRD:

| Component | Weight | Signal |
|---|---|---|
| Price Arbitrage | 30% | asking $/acre vs. regional baseline |
| Days-on-Market / Lazy Listing | 20% | stale (>threshold DOM) + non-optimized broker copy |
| Infrastructure Headroom | 40% | substation MW headroom + interconnection proximity |
| Resource Optionality | 10% | water rights · geothermal · mineral/gold claims · **USGS surface-water proximity** |

Every dossier carries the full sub-score breakdown — the score is explainable,
not a black box.

## Hard gates (Module B)

A parcel is disqualified before scoring if it is:
- **landlocked** or lacks a **legal recorded easement** (ingress hard-gate),
- outside the **transmission buffer** (`MIN_TRANSMISSION_BUFFER_MILES`),
- below the **substation headroom** minimum (`MIN_SUBSTATION_HEADROOM_MW`),
- above the **price ceiling** (`MAX_PRICE_PER_ACRE`).

Only lines ≥ 69 kV count as high-voltage transmission.

## Project layout

```
land-value-engine/
├── config.py            # env loading + validation (fail-fast)
├── parcels.py           # Parcel domain model + Apify/mock ingestion
├── spatial.py           # GeoJSON load, distance, hard-gate filtering
├── scoring.py           # Latent Arbitrage Score (LAS)
├── valuation.py         # HBU value modeling
├── data_loaders.py      # real HIFLD/USGS GIS loaders (replaces sample data)
├── water_rights.py      # SQLite water-rights enrichment (Phase 2)
├── report_generator.py  # Jinja2 → HTML → PDF dossier
├── dashboard.py         # Streamlit Top-N matrix dashboard (Phase 3)
├── master_engine.py     # pipeline orchestrator: analyze() + CLI entry point
├── templates/
│   └── dossier.html     # institutional dossier template
├── data/
│   ├── gis/             # transmission / substations / hydrography GeoJSON (real, via data_loaders.py)
│   └── db/              # seed_water_rights.py → western_water_rights.sqlite
├── tests/fixtures/gis/  # deterministic sample GIS for the test suite
├── onchain/             # Phase 4: Solidity contracts + Foundry tests
└── output_reports/      # generated dossiers
```

## Configuration (`.env`)

| Key | Default | Purpose |
|---|---|---|
| `APIFY_API_TOKEN` | — | Apify token; absent → mock mode |
| `PROPERTY_SCRAPER_ACTOR_ID` | `rigelbytes~landdotcom-scraper` | scraper actor |
| `RUN_MODE` | `auto` | `auto` / `mock` / `live` |
| `MAX_PRICE_PER_ACRE` | `2000.0` | price ceiling gate |
| `MIN_TRANSMISSION_BUFFER_MILES` | `3.0` | transmission buffer gate |
| `MIN_SUBSTATION_HEADROOM_MW` | `10.0` | headroom gate |
| `DAYS_ON_MARKET_THRESHOLD` | `90` | stale-listing threshold |
| `REGIONAL_BASELINE_PRICE_PER_ACRE` | `6000.0` | arbitrage contrast point |
| `SURFACE_WATER_BONUS_MILES` | `5.0` | surface-water proximity bonus radius (0 disables) |
| `TARGET_STATES` / `TARGET_ZIPS` | corridor | ingestion targeting |

## Dashboard (Phase 3)

An interactive Streamlit dashboard visualizes the Top-N latent-arbitrage matrix
on a live map, reusing the exact same analysis path as the CLI
(`master_engine.analyze`) so the dashboard and PDF dossiers never disagree.

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard.py
```

Sidebar controls drive the pipeline in real time (corridor size, price ceiling,
headroom + buffer gates, state/LAS filters). The map is colored and sized by
LAS; a detail panel breaks down each parcel's score and HBU composition.

## Real GIS data (HIFLD + USGS)

`data_loaders.py` replaces the sample GIS with live public data and writes the
same GeoJSON files the pipeline reads — so the engine picks up real
infrastructure with no other changes:

```bash
python data_loaders.py --states NV,AZ,UT,NM        # whole corridor
python data_loaders.py --bbox=-118.5,37,-116,39.5   # a specific bbox
```

- **Transmission lines** — HIFLD *Electric Power Transmission Lines* (ArcGIS), filtered to ≥ 69 kV.
- **Substations** — **derived** from transmission-line endpoints + `SUB_1`/`SUB_2` names, because HIFLD's public substation *point* layer is no longer open. Real names/locations (e.g. SILVER PEAK, TONOPAH, CONTROL).
- **Hydrography** — USGS National Hydrography Dataset waterbodies. Feeds a
  surface-water proximity bonus in the resource score (`SURFACE_WATER_BONUS_MILES`,
  default 5; set 0 to disable). Proximity uses a grid-indexed nearest-waterbody
  lookup, so it scales to the full corridor.

Large fetches are capped by `--max-records` (default 20 000) and **log a
truncation warning** rather than silently dropping data — raise it or narrow the
bbox for exhaustive coverage. A full 4-state run yields ~4.3k transmission lines
and ~3.2k derived substations.

> ⚠️ **Headroom is a proxy.** HIFLD carries no interconnection-queue capacity, so
> `headroom_mw` is estimated from voltage class × line-connectivity degree and
> flagged `headroom_estimated: true`. Replace `estimate_headroom_mw()` with real
> ISO/RTO queue data before relying on the infrastructure score.

Deterministic sample GIS is preserved under `tests/fixtures/gis/` so the test
suite is stable regardless of what's in `data/gis/`. To demo against real data,
`synthetic_near_infrastructure()` scatters parcels around the loaded substations
(the dashboard uses this automatically).

## Water rights (Phase 2)

Parcels are enriched at ingestion from `data/db/western_water_rights.sqlite`
(the DB is authoritative — it overrides scraped water figures and zeroes
revoked/abandoned rights). Seed the reference DB once:

```bash
python data/db/seed_water_rights.py
```

Missing DB degrades gracefully (a warning, no enrichment) — the pipeline still runs.

## Testing

```bash
pip install -r requirements-dev.txt
pytest                       # 103 tests: config, scoring, valuation, spatial gates,
                             #            hydrography, water rights, GIS loaders,
                             #            on-chain bridge, pipeline, dashboard
```

Set `LVE_LIVE_GIS=1` to also run the opt-in live HIFLD-endpoint smoke test.
The on-chain Solidity suite (17 tests) runs separately: `cd onchain && forge test`.

The suite runs offline (mock corridor + sample GIS data) and needs no
credentials. If your environment has a broken third-party pytest plugin that
crashes collection, disable plugin autoload:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest
```

## Roadmap

- **Phase 2 ✅** — water-rights SQLite enrichment (`water_rights.py`). Next: automated state water-engineer scrapers to replace the seed data behind the same API.
- **Phase 3 ✅** — Streamlit dashboard for the live Top-N arbitrage matrix (`dashboard.py`).
- **Phase 4 ▸ scaffold** — milestone-gated capital drawdown contracts for Base in [`onchain/`](onchain/README.md) (Solidity + Foundry, 17 tests) with a Python payload bridge (`onchain_bridge.py`). **Demo/testnet scaffold — unaudited, no mainnet value.** Remaining: audit, testnet deploy, signer wiring.

## Security

`.env` is git-ignored and holds credentials — never commit it. Sample GIS data
is illustrative HIFLD-style placeholder geometry, not authoritative infrastructure data.
