# LVE-LAP — local run & review helpers.
# Usage: `make setup`, then `make dashboard` / `make dossier` / `make test`.

PY := python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help setup deps data seed dossier dashboard test test-live clean

help:
	@echo "LVE-LAP local targets:"
	@echo "  make setup      Create venv + install all deps (engine, dashboard, dev)"
	@echo "  make seed       Seed the water-rights SQLite DB"
	@echo "  make data       Fetch real HIFLD/USGS GIS for the corridor (network)"
	@echo "  make dossier    Run the pipeline and generate an HTML/PDF dossier"
	@echo "  make dashboard  Launch the Streamlit dashboard (http://localhost:8501)"
	@echo "  make test       Run the Python test suite"
	@echo "  make clean      Remove venv + generated reports"

setup: $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.txt -r requirements-dashboard.txt \
		-r requirements-onchain.txt
	@echo "Setup complete. Next: 'make seed' then 'make dashboard'."

$(VENV):
	$(PY) -m venv $(VENV)

seed:
	$(BIN)/python data/db/seed_water_rights.py

# Real corridor data (writes to data/gis/, git-ignored). Requires network.
data:
	$(BIN)/python data_loaders.py --states NV,AZ,UT,NM

dossier:
	$(BIN)/python master_engine.py --top 10

dashboard:
	$(BIN)/streamlit run dashboard.py

# Live run uses your APIFY_API_TOKEN + TARGET_ZIPS in .env (costs money).
test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(BIN)/pytest -q

clean:
	rm -rf $(VENV) output_reports/*.html output_reports/*.pdf
