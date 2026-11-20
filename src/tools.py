import ast
import json
import math
import os
import sqlite3
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "company_sales.db"

MAX_SQL_ROWS = 200
MAX_OUTPUT_CHARS = 4000
MAX_FILE_BYTES = 1_000_000

ALLOWED_SQL_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}

ALLOWED_PYTHON_IMPORTS = {
    "pandas",
    "pd",
    "numpy",
    "np",
    "math",
    "statistics",
    "datetime",
    "json",
}

MAX_PYTHON_CODE_CHARS = 20_000

BLOCKED_PYTHON_CALLS_EXTRA = {
    "read_csv",
    "read_excel",
    "read_parquet",
    "read_json",
    "read_sql",
    "read_sql_query",
    "read_sql_table",
    "read_table",
    "read_fwf",
    "read_pickle",
    "read_html",
    "read_xml",
    "read_feather",
    "read_orc",
    "read_stata",
    "read_sas",
    "read_spss",
    "read_hdf",
    "read_clipboard",
    "read_gbq",
    "to_csv",
    "to_excel",
    "to_parquet",
    "to_json",
    "to_sql",
    "to_pickle",
    "to_feather",
    "to_orc",
    "to_stata",
    "to_hdf",
    "to_html",
    "to_xml",
    "to_clipboard",
    "ExcelFile",
    "ExcelWriter",
    "HDFStore",
    "get_handle",
    "loadtxt",
    "genfromtxt",
    "fromfile",
    "tofile",
    "load",
    "save",
    "savez",
    "savez_compressed",
    "savetxt",
    "memmap",
    "DataSource",
    "dump",
}

BLOCKED_PYTHON_BUILTINS = {
    "open",
    "eval",
    "exec",
    "compile",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "__import__",
    "input",
    "breakpoint",
}

BLOCKED_PYTHON_CALLS = {
    *BLOCKED_PYTHON_CALLS_EXTRA,
    *BLOCKED_PYTHON_BUILTINS,
    "format_map",
}

BLOCKED_PYTHON_SUBMODULES = {
    "io",
    "compat",
    "ctypeslib",
    "testing",
    "f2py",
    "distutils",
}

BLOCKED_IMPORTED_NAMES = {
    "os",
    "sys",
    "subprocess",
    "sqlite3",
    "pathlib",
    "shutil",
    "socket",
    "ctypes",
    "importlib",
    "builtins",
    "io",
}

ALLOWED_READ_EXTENSIONS = {".csv", ".json", ".txt", ".md"}
ALLOWED_REPORT_EXTENSIONS = {".md", ".txt"}


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _wrap_untrusted(label: str, content: str) -> str:
    return f"<{label}>\n{content}\n</{label}>"


def _resolve_db_path(db_path: str | None = None) -> Path:
    if db_path:
        p = Path(db_path).resolve()
        if p.exists():
            return p
    cwd_db = (Path.cwd() / "company_sales.db").resolve()
    if cwd_db.exists():
        return cwd_db
    return DEFAULT_DB_PATH.resolve()


def _resolve_safe_path(user_path: str, base_dir: Path = PROJECT_ROOT) -> tuple[bool, Path | str]:
    try:
        base_resolved = base_dir.resolve()
        raw_path = Path(user_path)
        candidate = raw_path.resolve() if raw_path.is_absolute() else (base_resolved / raw_path).resolve()
        if not candidate.is_relative_to(base_resolved):
            return False, "Security Error: Path outside project workspace is not allowed."
        return True, candidate
    except (OSError, ValueError) as exc:
        return False, f"Security Error: Invalid path ({exc})."




def _is_risky_format_template(tmpl: str, depth: int = 0) -> bool:
    if depth > 2:
        return True
    try:
        parsed = list(string.Formatter().parse(tmpl))
    except ValueError:
        return True
    for _, field_name, format_spec, _ in parsed:
        if field_name is not None and field_name != "":
            is_simple_pos = field_name.isdigit()
            is_simple_ident = field_name.isidentifier() and not field_name.startswith("_")
            if not (is_simple_pos or is_simple_ident):
                return True
        if (
            format_spec
            and ("{" in format_spec or "}" in format_spec)
            and _is_risky_format_template(format_spec, depth + 1)
        ):
            return True
    return False


def _has_risky_format_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Attribute):
        if node.func.attr == "format_map":
            return True
        if node.func.attr == "format":
            if not (isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str)):
                return True
            return _is_risky_format_template(node.func.value.value)
    return False


def _validate_python_ast(code: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"Syntax Error in Python code: {exc}"

    safe_format_attr_ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and not _has_risky_format_call(node)
        ):
            safe_format_attr_ids.add(id(node.func))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] not in ALLOWED_PYTHON_IMPORTS or any(
                    p.startswith("_") or p in BLOCKED_PYTHON_SUBMODULES for p in parts[1:]
                ):
                    return False, f"Security Error: Import '{alias.name}' is not allowed."
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            parts = mod_name.split(".")
            if parts[0] not in ALLOWED_PYTHON_IMPORTS or any(
                p.startswith("_") or p in BLOCKED_PYTHON_SUBMODULES for p in parts[1:]
            ):
                return False, f"Security Error: Importing from '{mod_name}' is not allowed."
            for alias in node.names:
                if (
                    alias.name == "*"
                    or alias.name.startswith("_")
                    or alias.name in BLOCKED_PYTHON_CALLS
                    or alias.name in BLOCKED_IMPORTED_NAMES
                ):
                    return False, f"Security Error: Importing '{alias.name}' from '{mod_name}' is not allowed."
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return False, f"Security Error: Access to dunder attribute '{node.attr}' is not allowed."
            if node.attr in BLOCKED_PYTHON_CALLS:
                return False, f"Security Error: Access to blocked attribute '{node.attr}' is not allowed."
            if node.attr == "format" and id(node) not in safe_format_attr_ids:
                return False, "Security Error: Only direct literal string '.format(...)' calls are allowed."
        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):
                return False, f"Security Error: Access to dunder name '{node.id}' is not allowed."
            if node.id in BLOCKED_PYTHON_BUILTINS:
                return False, f"Security Error: Access to blocked builtin '{node.id}' is not allowed."
        elif isinstance(node, ast.Call):
            if _has_risky_format_call(node):
                return False, "Security Error: Format string attribute or index access is not allowed."
            if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_PYTHON_CALLS:
                return False, f"Security Error: Call to '{node.func.id}' is not allowed."
            if isinstance(node.func, ast.Attribute) and node.func.attr in BLOCKED_PYTHON_CALLS:
                return False, f"Security Error: Call to '.{node.func.attr}' is not allowed."

    return True, ""


def inspect_database() -> str:
    """Returns table names, SQL schemas, and sample rows from the SQLite database in read-only mode."""
    db_path = _resolve_db_path()
    if not db_path.exists():
        return "Database Error: Database file does not exist."
    try:
        uri = f"{db_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON;")
        cursor = conn.cursor()
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        if not tables:
            conn.close()
            return "Database is empty."

        sections = []
        for table_name, schema_sql in tables:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 2;")
            sample_rows = cursor.fetchall()
            cols = [desc[0] for desc in cursor.description]
            samples = [dict(zip(cols, r)) for r in sample_rows]
            sections.append(
                f"Table: {table_name}\nSchema: {schema_sql}\nSample Rows: {json.dumps(samples, ensure_ascii=False)}"
            )
        conn.close()
        return _wrap_untrusted("DATABASE_SCHEMA_AND_SAMPLES", "\n\n".join(sections))
    except (sqlite3.Error, OSError, ValueError) as exc:
        return f"Database Inspection Error: {exc}"


def run_sql_query(query: str) -> str:
    """Executes a single read-only SELECT query enforced by SQLite authorizer, progress limit, and row cap."""
    db_path = _resolve_db_path()
    if not db_path.exists():
        return "Database Error: Database file does not exist."

    cleaned = query.strip()
    if not cleaned:
        return "SQL Error: Empty query."

    deadline = time.monotonic() + 3.0

    def _authorizer(action, arg1, arg2, dbname, source):
        return sqlite3.SQLITE_OK if action in ALLOWED_SQL_ACTIONS else sqlite3.SQLITE_DENY

    def _progress():
        return 1 if time.monotonic() > deadline else 0

    try:
        uri = f"{db_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON;")
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(_progress, 10_000)

        cursor = conn.cursor()
        cursor.execute(cleaned)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        fetched = cursor.fetchmany(MAX_SQL_ROWS + 1)
        conn.close()

        truncated = len(fetched) > MAX_SQL_ROWS
        rows = fetched[:MAX_SQL_ROWS]
        if not rows:
            return "Query executed successfully, but returned 0 rows."

        result = [dict(zip(columns, row)) for row in rows]
        payload = {
            "row_count": len(rows),
            "truncated": truncated,
            "rows": result,
        }
        return _wrap_untrusted("SQL_QUERY_RESULT", _truncate(json.dumps(payload, ensure_ascii=False)))
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).lower():
            return "SQL Execution Error: Query timed out (exceeded 3s progress budget)."
        return f"SQL Execution Error: {exc}"
    except sqlite3.DatabaseError as exc:
        return f"Security or SQL Error: {exc}"
    except (sqlite3.Error, OSError, ValueError) as exc:
        return f"SQL Execution Error: {exc}"


def python_analyst(code: str) -> str:
    """Executes AST-validated Python code in an isolated temp directory with a stripped environment."""
    if not isinstance(code, str) or not code.strip():
        return "Python Error: Empty code."
    if len(code) > MAX_PYTHON_CODE_CHARS:
        return f"Security Error: Python code exceeds maximum allowed length ({MAX_PYTHON_CODE_CHARS} chars)."

    safe, reason = _validate_python_ast(code)
    if not safe:
        return reason

    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONIOENCODING": "utf-8",
    }

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True,
                check=False,
                text=True,
                timeout=8,
                cwd=tmp_dir,
                env=clean_env,
            )
        if proc.returncode == 0:
            out = proc.stdout.strip() or "Code executed successfully (no output printed)."
            return _wrap_untrusted("PYTHON_OUTPUT", _truncate(out))
        return f"Python Error: {_truncate(proc.stderr.strip())}"
    except subprocess.TimeoutExpired:
        return "Error: Python execution timed out after 8 seconds."
    except (OSError, ValueError) as exc:
        return f"System Error: {exc}"


def read_file(filepath: str, max_rows: int = 20) -> str:
    """Safely reads workspace CSV, JSON, TXT, or Markdown files within the project workspace."""
    allowed, resolved = _resolve_safe_path(filepath)
    if not allowed:
        return str(resolved)
    target = Path(resolved)
    if target.name.lower() == ".env" or target.name.lower().startswith(".env."):
        return "Security Error: Reading environment files is not allowed."

    ext = target.suffix.lower()
    if ext not in ALLOWED_READ_EXTENSIONS:
        return f"Security Error: Unsupported or disallowed file extension '{ext}'."

    if isinstance(max_rows, bool) or not isinstance(max_rows, int):
        return "File Read Error: 'max_rows' must be an integer."
    bounded_rows = max(1, min(max_rows, 100))

    if not target.exists() or not target.is_file():
        return f"File Error: '{filepath}' does not exist."

    if target.stat().st_size > MAX_FILE_BYTES:
        return f"File Error: '{filepath}' exceeds maximum allowed size ({MAX_FILE_BYTES} bytes)."

    try:
        if ext == ".csv":
            df = pd.read_csv(target)
            summary = {
                "rows": len(df),
                "columns": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "preview": df.head(bounded_rows).to_dict(orient="records"),
            }
            return _wrap_untrusted("FILE_CONTENT_CSV", _truncate(json.dumps(summary, ensure_ascii=False, indent=2)))
        if ext == ".json":
            data = json.loads(target.read_text(encoding="utf-8"))
            return _wrap_untrusted("FILE_CONTENT_JSON", _truncate(json.dumps(data, ensure_ascii=False, indent=2)))
        content = target.read_text(encoding="utf-8")
        return _wrap_untrusted("FILE_CONTENT_TEXT", _truncate(content))
    except (OSError, ValueError, json.JSONDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        return f"File Read Error: {exc}"


def generate_chart(
    chart_type: str,
    title: str,
    labels: list,
    values: list,
    filename: str,
    xlabel: str = "",
    ylabel: str = "",
) -> str:
    """Generates and saves a PNG chart (bar, barh, line, or pie) under reports/charts/."""
    try:
        if not labels or not values or len(labels) != len(values):
            return "Chart Error: 'labels' and 'values' must be non-empty lists of equal length."

        for val in values:
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                return "Chart Error: 'values' must contain only finite numbers."

        charts_dir = PROJECT_ROOT / "reports" / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)

        raw_name = Path(filename).name
        if not raw_name.lower().endswith(".png"):
            raw_name += ".png"

        allowed, resolved = _resolve_safe_path(raw_name, base_dir=charts_dir)
        if not allowed:
            return str(resolved)
        out_path = Path(resolved)

        plt.figure(figsize=(8, 4.8), dpi=150)
        ctype = chart_type.strip().lower()

        if ctype == "bar":
            bars = plt.bar(labels, values, color="#2563EB", edgecolor="none")
            plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            for bar, val in zip(bars, values):
                plt.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{val:,.1f}" if isinstance(val, float) else f"{val:,}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )
        elif ctype == "barh":
            bars = plt.barh(labels, values, color="#0D9488", edgecolor="none")
            plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            plt.gca().invert_yaxis()
            for bar, val in zip(bars, values):
                plt.text(
                    bar.get_width(),
                    bar.get_y() + bar.get_height() / 2,
                    f" {val:,.1f}" if isinstance(val, float) else f" {val:,}",
                    va="center",
                    fontsize=9,
                )
        elif ctype == "line":
            plt.plot(labels, values, marker="o", linewidth=2.2, color="#4F46E5")
            plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            plt.grid(True, linestyle="--", alpha=0.4)
            for x_lbl, val in zip(labels, values):
                plt.annotate(
                    f"{val:,.1f}" if isinstance(val, float) else f"{val:,}",
                    (x_lbl, val),
                    textcoords="offset points",
                    xytext=(0, 8),
                    ha="center",
                    fontsize=9,
                )
        elif ctype == "pie":
            plt.pie(values, labels=labels, autopct="%1.1f%%", startangle=140)
            plt.axis("equal")
        else:
            plt.close()
            return f"Chart Error: Unsupported chart_type '{chart_type}'. Use bar, barh, line, or pie."

        plt.title(title, fontsize=12, fontweight="bold", pad=12)
        plt.tight_layout()
        plt.savefig(out_path)
        plt.close()

        rel_path = out_path.relative_to(PROJECT_ROOT)
        return f"Chart successfully generated and saved to {rel_path}"
    except (OSError, ValueError, RuntimeError) as exc:
        plt.close("all")
        return f"Chart Generation Error: {exc}"


def save_report(filename: str, content: str) -> str:
    """Safely saves a Markdown or text report under reports/ without overwriting existing files."""
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    raw_name = Path(filename).name
    if not raw_name:
        return "File Save Error: Invalid filename."

    allowed, resolved = _resolve_safe_path(raw_name, base_dir=reports_dir)
    if not allowed:
        return str(resolved)
    filepath = Path(resolved)

    if filepath.suffix.lower() not in ALLOWED_REPORT_EXTENSIONS:
        return f"Security Error: Report extension '{filepath.suffix}' is not allowed. Use .md or .txt."

    if filepath.exists():
        return f"File Save Error: '{filepath.relative_to(PROJECT_ROOT)}' already exists. Choose a new filename."

    try:
        filepath.write_text(content, encoding="utf-8")
        return f"Report successfully saved to {filepath.relative_to(PROJECT_ROOT)}"
    except (OSError, ValueError) as exc:
        return f"File Save Error: {exc}"


TOOLS_MAP = {
    "inspect_database": inspect_database,
    "run_sql_query": run_sql_query,
    "python_analyst": python_analyst,
    "read_file": read_file,
    "generate_chart": generate_chart,
    "save_report": save_report,
}

TOOLS_DECLARATIONS = [{
    "functionDeclarations": [
        {
            "name": "inspect_database",
            "description": "Returns table names, SQL schemas, and sample rows from the read-only SQLite database.",
            "parameters": {"type": "OBJECT", "properties": {}}
        },
        {
            "name": "run_sql_query",
            "description": "Executes a read-only SELECT SQL query on the database and returns rows as JSON.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING", "description": "Read-only SQLite SELECT query"}},
                "required": ["query"]
            }
        },
        {
            "name": "python_analyst",
            "description": "Executes safe Python code (pandas, numpy, math, statistics, datetime, json) for statistics or transformations. Always print() the final result.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"code": {"type": "STRING", "description": "Python code to execute"}},
                "required": ["code"]
            }
        },
        {
            "name": "read_file",
            "description": "Safely reads workspace CSV, JSON, TXT, or Markdown files and returns structure/preview or text.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "filepath": {"type": "STRING", "description": "Relative workspace file path"},
                    "max_rows": {"type": "INTEGER", "description": "Max rows preview for CSV files (default 20)"}
                },
                "required": ["filepath"]
            }
        },
        {
            "name": "generate_chart",
            "description": "Generates and saves a PNG chart (bar, barh, line, or pie) under reports/charts/.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "chart_type": {"type": "STRING", "enum": ["bar", "barh", "line", "pie"], "description": "Chart type"},
                    "title": {"type": "STRING", "description": "Chart title"},
                    "labels": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Category labels"},
                    "values": {"type": "ARRAY", "items": {"type": "NUMBER"}, "description": "Numeric values"},
                    "filename": {"type": "STRING", "description": "Output PNG filename e.g. dept_sales.png"},
                    "xlabel": {"type": "STRING", "description": "Optional x-axis label"},
                    "ylabel": {"type": "STRING", "description": "Optional y-axis label"}
                },
                "required": ["chart_type", "title", "labels", "values", "filename"]
            }
        },
        {
            "name": "save_report",
            "description": "Saves a new Markdown (.md) or text (.txt) report under reports/.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "filename": {"type": "STRING", "description": "Report filename e.g. summary.md"},
                    "content": {"type": "STRING", "description": "Report content"}
                },
                "required": ["filename", "content"]
            }
        }
    ]
}]
