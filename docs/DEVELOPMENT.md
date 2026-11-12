# Development

## Environment

The project uses an isolated virtualenv (`.venv`) and a local `node_modules`. Nothing is
installed system-wide, and Homebrew is not required.

```bash
make setup          # picks a Python ≥3.12 (uv-managed if present), creates .venv, installs deps
make dev            # backend with --reload on :8000, Vite on :5173 (proxies /api)
make stop
```

Configuration comes from environment variables or a `.env` file; see `.env.example`.
Runtime settings (timeouts, limits, AI provider, repository roots) live in the database
and are edited in the dashboard.

Data lives in `.devflow/` (gitignored): `devflow.db`, `api_token`, `runs/<id>/{src,out}`,
`repos/` (installed examples), `traces.jsonl`. Delete the directory to reset everything.

## Workflow

```bash
make format         # ruff format + autofix
make lint           # ruff, format check, tsc
make typecheck      # mypy --strict
make test           # backend + frontend
make check          # what CI runs
```

Adding a database column: edit `backend/devflow/database/models.py`, then run
`alembic revision --autogenerate -m "describe change"` and review the generated file
in `backend/devflow/migrations/versions/`. The app applies migrations on start.

Adding a provider: implement the `Provider` protocol in `backend/devflow/ai/base.py`
(`name`, `label`, `is_ai`, `generates_patches`, `availability()`, `analyze()`), register
it in `ai/registry.py`, add it to `AIProviderName` in `services/settings.py` and to
`AnalysisCreate.provider`. Providers receive prepared context and return text. They
must not touch the repository.

Adding a runner: implement `availability()` and `run(spec, cancel)` from
`runners/base.py` and reuse `supervise()` for timeouts and cancellation.

Screenshots for the README: `PLAYWRIGHT_BROWSERS_PATH=.cache/ms-playwright python
scripts/screenshots.py` (requires `pip install playwright` and
`playwright install chromium-headless-shell`, which download into `.cache/`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "Missing or invalid API token" | Run `make token` and paste the value. Tokens live in `.devflow/api_token` |
| Health banner: "Docker engine unavailable" | Start Docker Desktop; `docker version` must show a Server section |
| `make: docker: No such file or directory` | Docker Desktop's per-user install puts the CLI in `~/.docker/bin`. The Makefile and DevFlow find it there automatically; for your own shell, add `export PATH="$HOME/.docker/bin:$PATH"` to `~/.zprofile` |
| "Runner image 'devflow-runner:0.1' not built" | `make runner-image` |
| Runs fail with `missing_dependency` | The sandbox has no network. Add the package to `runner/requirements.txt` and rebuild the image |
| `403 Repository path is outside the approved directories` | Add the parent directory under Settings → Repository directories |
| AI provider "not logged in" | `claude auth login` / `codex login` in a terminal |
| AI provider times out | Raise `DEVFLOW_AI_TIMEOUT_SECONDS` (default 300) |
| Port 8000 in use | `DEVFLOW_PORT=8010 devflow serve`, and point Vite at it with `DEVFLOW_BACKEND=http://127.0.0.1:8010` |
| Runs stuck after a crash | They are marked `error: Interrupted` on next start |
| `database is locked` | Only one DevFlow process should use a data dir at a time |
