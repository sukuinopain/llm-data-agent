# Autonomous Data Analyst AI Agent

An autonomous multi-step Data Analyst AI Agent built from scratch in Python using **Gemini (`gemini-3.1-flash-lite`)** via native function calling, a custom ReAct execution loop, SQLite read-only enforcement, AST-validated Python analytics, workspace file ingestion, PNG charting, Markdown reporting, and structured JSON telemetry traces.

## Architecture

- **ReAct Loop (`src/agent.py`):** Sends task + database-path/stat-keyed cached schema context in the initial user message with `x-goog-api-key` header to Gemini, configures UTF-8 console output safely, executes tool calls via `src/tools.py`, detects loops, and logs JSON traces to `logs/`.
- **Tools & Guardrails (`src/tools.py`):** Read-only SQLite connection (`mode=ro`, `PRAGMA query_only = ON`, authorizer, progress handler, row cap), AST-validated Python execution with risky format-string and data-IO call checks in an isolated temporary directory plus runtime audit hooks, workspace path checks, finite-value validated chart rendering (`reports/charts/`), and report saving (`reports/`).

## Defense-in-Depth & Threat Model

- **SQLite Read-Only Enforcement:** Opens `company_sales.db` in read-only mode (`mode=ro`), sets `PRAGMA query_only = ON`, enforces an explicit SQLite authorizer (`SQLITE_SELECT`, `SQLITE_READ`, `SQLITE_FUNCTION`, `SQLITE_RECURSIVE`), caps returned rows (`MAX_SQL_ROWS = 200`), and applies a progress handler timeout.
- **Python Sandbox Boundaries:** Uses AST validation (`_validate_python_ast`, `_has_risky_format_call`, and `BLOCKED_PYTHON_CALLS_EXTRA`) with an import allowlist, blocks dunder attributes/names and high-risk calls, enforces runtime audit hooks, and runs inside an isolated temporary directory with a stripped environment. **Note:** AST/subprocess filtering is *defense-in-depth*, not a hard container security boundary; for untrusted multi-tenant workloads, isolate execution inside a hardened Docker container (`--network none --read-only --memory 256m --pids-limit 64`).
- **Path & Data Isolation:** Enforces workspace boundary checks (`Path.resolve().is_relative_to(...)`), extension allowlists, non-overwrite report saving, finite numeric chart checks (`math.isfinite`), and wraps untrusted data outputs in delimiters.

## Quickstart

1. Create and activate virtual environment: `python -m venv .venv` and `..\.venv\Scripts\Activate.ps1`
2. Install dependencies: `pip install -r requirements.txt pytest ruff`
3. Set API key: `$env:GEMINI_API_KEY="your_api_key_here"`
4. Initialize DB & run agent: `python -m src.setup_db` then `python -m src.agent`

## Testing & Linting

- Run tests: `python -m pytest -q`
- Run linter: `ruff check src tests evaluations`

## Benchmark Evaluation

Run `python -m evaluations.run_benchmark` to execute ground-truth numerical checks (verified by tool usage, numeric matching, and output artifacts) and adversarial security tasks with a short inter-task pacing delay (`2.0s`), generating `evaluation_results.md` and per-run traces in `logs/`. Latest benchmark run achieved **100.0% ground-truth accuracy** and **100.0% adversarial block rate** across 5 tasks.

## License

MIT License — see [LICENSE](LICENSE).
