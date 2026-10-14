# Data DJ

Data DJ explores datasets through interactive visualizations. This repository
keeps the existing Vite/TypeScript prototype and adds a Python API for future
server-side dimensionality reduction.

## Project Layout

```text
backend/
	app/
		api/routes/health.py   # Typed health endpoint
		api/routes/datasets.py # Dataset upload and metadata retrieval
		models/dataset.py      # Prepared dataset passed between layers
		schemas/dataset.py     # Dataset metadata API schema
		services/datasets.py   # CSV validation, preprocessing, sampling
		dr/                    # Typed UMAP, t-SNE, and PHATE runners
		storage/               # Storage contract and local persistence
		main.py                # FastAPI app and local-development CORS
	tests/                   # Health and dataset API tests
	pyproject.toml           # Python dependencies and tool configuration
	uv.lock                  # Resolved Python dependency versions
main/                     # Existing Vite + TypeScript frontend
```

The frontend currently lives in `main/` and is already initialized with Vite,
TypeScript, and `package-lock.json`. It is deliberately left in place because
its existing GitHub Pages configuration depends on that directory. The intended
monorepo pairing for now is `backend/` plus `main/`; no frontend framework or
additional frontend dependency is needed for the backend milestones.

## Prerequisites

- Python 3.13, managed by [uv](https://docs.astral.sh/uv/)
- Node.js and npm for the existing Vite frontend

FastAPI, Uvicorn, pandas, NumPy, scikit-learn, pytest, HTTPX, and Ruff support
Python 3.13. The backend pins that minor version in `backend/.python-version`;
uv can download it if it is not installed locally.

## Install and Run

From the repository root in PowerShell, initialize/sync the backend and start it:

```powershell
uv sync --project backend --dev
uv run --project backend uvicorn app.main:app --reload --app-dir backend
```

In another terminal, install and run the existing frontend:

```powershell
npm --prefix main ci
npm --prefix main run dev
```

The Vite app runs at `http://localhost:8080`. The API runs at
`http://127.0.0.1:8000`, with OpenAPI docs at `/docs`. CORS permits the Vite
development origins on `localhost:8080` and `127.0.0.1:8080`.

Upload CSV files with `POST /api/datasets` and retrieve metadata with
`GET /api/datasets/{dataset_id}`. The upload can specify a `label_column`,
repeat `identifier_columns` to exclude from features, and optionally designate a
`row_id_column`. Without that column, zero-based source row positions are used
as row IDs. `preprocessing` accepts `none` or `standardize`; `max_samples` and
`random_seed` control deterministic random subsampling. The Reduction Lab has a
header-row checkbox; when disabled, the first record is retained and columns are
named `column_0`, `column_1`, and so on. For MNIST-style data with the label in
the first column, choose `column_0` as the label column before preparing.

## Test and Lint

```powershell
uv run --project backend pytest -c backend/pyproject.toml
uv run --project backend ruff check .
```

The tests cover the health route, CSV parsing and validation, feature selection,
identifier/label alignment, deterministic sampling, preprocessing, API errors,
and local persistence. Prepared arrays and metadata are retained in
`backend/data/datasets/` until manually deleted; raw CSV files are not retained.
Uploads are capped at 200 MiB and at most 100,000 observations can be selected.
Synchronous processing does not provide a hard execution timeout; that requires
isolated processes or jobs. The frontend's existing production build can be
checked with `npm --prefix main run build`.

The backend also exposes a framework-independent runner factory in
`backend/app/dr/factory.py`. It runs real UMAP, scikit-learn t-SNE, or PHATE
against finite numeric matrices and enforces finite output coordinates in either
two or three dimensions. `POST /api/embeddings` returns a downloadable CSV with
`row_id`, optional `label`, and `embedding_1` through `embedding_2` or
`embedding_3`. It also returns the full embedding UUID in `X-Embedding-ID`.
`GET /api/embeddings/{embedding_id}` returns the in-memory JSON result while the
API process is running; generated files are downloaded directly and are not
persisted on the server. Synchronous requests are limited to 100,000 observations
and 5,000 features and do not have a hard execution timeout.

The workflows are separate: the dimensionality-reduction/export interface is
available at `http://localhost:8080/reduction.html`; the existing CSV
visualization interface remains at `http://localhost:8080/`. The reduction page
downloads a CSV after computation; load that file into the visualization page
to explore the exported embedding coordinates.