import os
import sys
import json
import sqlite3
import subprocess
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "company_sales.db")

BLOCKED_PYTHON_TOKENS = [
    "os.system",
    "os.popen",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "shutil.",
    "subprocess.",
    "socket.",
    "urllib.",
    "requests.",
    "__import__",
    "eval(",
    "exec(",
    "compile(",
]


def _resolve_db_path(db_path: str = None) -> str:
    if db_path and os.path.exists(db_path):
        return db_path
    if os.path.exists("company_sales.db"):
        return os.path.abspath("company_sales.db")
    return DEFAULT_DB_PATH


def _resolve_safe_path(user_path: str, base_dir: str = PROJECT_ROOT) -> tuple[bool, str]:
    abs_base = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(abs_base, user_path)) if not os.path.isabs(user_path) else os.path.abspath(user_path)
    if os.path.commonpath([abs_base, target]) != abs_base:
        return False, "Security Error: Path outside project workspace is not allowed."
    return True, target


def _is_safe_python_code(code: str) -> tuple[bool, str]:
    for token in BLOCKED_PYTHON_TOKENS:
        if token in code:
            return False, f"Security Error: Restricted Python construct '{token}' is not allowed."
    if "open(" in code and any(mode in code for mode in ['"w"', "'w'", '"a"', "'a'", '"x"', "'x'", '"r+"', "'r+'"]):
        return False, "Security Error: Writing or modifying files inside python_analyst is not allowed."
    return True, ""


def inspect_database() -> str:
    """Returns all table names, column schemas, and sample values in the SQLite database."""
    db_path = _resolve_db_path()
    try:
        conn = sqlite3.connect(db_path)
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
        return "\n\n".join(sections)
    except Exception as e:
        return f"Database Inspection Error: {str(e)}"


def run_sql_query(query: str) -> str:
    """Executes a read-only SELECT SQL query on the database and returns the rows."""
    db_path = _resolve_db_path()
    try:
        cleaned = query.strip()
        if not cleaned.upper().startswith("SELECT"):
            return "Security Error: Only SELECT queries are allowed."
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "ATTACH", "PRAGMA"]
        upper_q = cleaned.upper()
        if any(word in upper_q for word in forbidden):
            return "Security Error: Potentially destructive or non-read-only SQL keyword detected."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(cleaned)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return "Query executed successfully, but returned 0 rows."
        result = [dict(zip(columns, row)) for row in rows]
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"SQL Execution Error: {str(e)}"


def python_analyst(code: str) -> str:
    """Executes safe Python code (with pandas/numpy available) for math, statistics, or data analysis. Always print() the final result."""
    safe, reason = _is_safe_python_code(code)
    if not safe:
        return reason

    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=PROJECT_ROOT,
            env=clean_env,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "Code executed successfully (no output printed)."
        else:
            return f"Python Error: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "Error: Python execution timed out after 8 seconds."
    except Exception as e:
        return f"System Error: {str(e)}"


def read_file(filepath: str, max_rows: int = 20) -> str:
    """Reads CSV, JSON, TXT, or Markdown files inside the project workspace and returns a structured preview."""
    allowed, safe_path = _resolve_safe_path(filepath)
    if not allowed:
        return safe_path
    if not os.path.exists(safe_path):
        return f"File Error: '{filepath}' does not exist."

    ext = os.path.splitext(safe_path)[1].lower()
    try:
        if ext == ".csv":
            df = pd.read_csv(safe_path)
            summary = {
                "rows": len(df),
                "columns": list(df.columns),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "preview": df.head(max_rows).to_dict(orient="records"),
            }
            return json.dumps(summary, ensure_ascii=False, indent=2)
        elif ext == ".json":
            with open(safe_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            return text[:4000] + ("\n... [truncated]" if len(text) > 4000 else "")
        else:
            with open(safe_path, "r", encoding="utf-8") as f:
                content = f.read()
            return content[:4000] + ("\n... [truncated]" if len(content) > 4000 else "")
    except Exception as e:
        return f"File Read Error: {str(e)}"


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

        charts_dir = os.path.join(PROJECT_ROOT, "reports", "charts")
        os.makedirs(charts_dir, exist_ok=True)
        safe_name = os.path.basename(filename)
        if not safe_name.lower().endswith(".png"):
            safe_name += ".png"
        out_path = os.path.join(charts_dir, safe_name)

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

        rel_path = os.path.relpath(out_path, PROJECT_ROOT)
        return f"Chart successfully generated and saved to {rel_path}"
    except Exception as e:
        plt.close("all")
        return f"Chart Generation Error: {str(e)}"


def save_report(filename: str, content: str) -> str:
    """Saves a final analytical report or summary into reports/."""
    try:
        reports_dir = os.path.join(PROJECT_ROOT, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        safe_name = os.path.basename(filename)
        filepath = os.path.join(reports_dir, safe_name)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        rel_path = os.path.relpath(filepath, PROJECT_ROOT)
        return f"Report successfully saved to {rel_path}"
    except Exception as e:
        return f"File Save Error: {str(e)}"


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
            "description": "Returns all table names, column schemas, and sample rows in the SQLite database.",
            "parameters": {"type": "OBJECT", "properties": {}}
        },
        {
            "name": "run_sql_query",
            "description": "Executes a read-only SELECT SQL query on the database and returns the rows as JSON.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING", "description": "Valid SQLite SELECT query"}},
                "required": ["query"]
            }
        },
        {
            "name": "python_analyst",
            "description": "Executes safe Python code (pandas, numpy, math) for statistics, aggregations, or percentages. Always print() the final result.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"code": {"type": "STRING", "description": "Python code to execute"}},
                "required": ["code"]
            }
        },
        {
            "name": "read_file",
            "description": "Reads CSV, JSON, TXT, or Markdown files in the project workspace and returns schema/preview or text.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "filepath": {"type": "STRING", "description": "Relative or workspace file path"},
                    "max_rows": {"type": "INTEGER", "description": "Max rows preview for CSV files (default 20)"}
                },
                "required": ["filepath"]
            }
        },
        {
            "name": "generate_chart",
            "description": "Generates and saves a PNG chart (bar, barh, line, pie) under reports/charts/.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "chart_type": {"type": "STRING", "description": "One of: bar, barh, line, pie"},
                    "title": {"type": "STRING", "description": "Chart title"},
                    "labels": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Category or x-axis labels"},
                    "values": {"type": "ARRAY", "items": {"type": "NUMBER"}, "description": "Numeric values corresponding to labels"},
                    "filename": {"type": "STRING", "description": "Output PNG filename e.g. dept_sales.png"},
                    "xlabel": {"type": "STRING", "description": "Optional x-axis label"},
                    "ylabel": {"type": "STRING", "description": "Optional y-axis label"}
                },
                "required": ["chart_type", "title", "labels", "values", "filename"]
            }
        },
        {
            "name": "save_report",
            "description": "Saves a final analytical report or summary into reports/.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "filename": {"type": "STRING", "description": "Filename e.g. report.md"},
                    "content": {"type": "STRING", "description": "Markdown report content"}
                },
                "required": ["filename", "content"]
            }
        }
    ]
}]
