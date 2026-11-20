import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.agent as agent_mod
from src.tools import (
    generate_chart,
    inspect_database,
    python_analyst,
    read_file,
    run_sql_query,
    save_report,
)


def test_sql_authorizer_and_cte_guard():
    blocked_sql = run_sql_query("DELETE FROM sales;")
    assert "SQL Execution Error" in blocked_sql or "Security or SQL Error" in blocked_sql

    blocked_cte = run_sql_query("WITH x AS (SELECT 1) DELETE FROM sales;")
    assert "SQL Execution Error" in blocked_cte or "Security or SQL Error" in blocked_cte


def test_python_ast_sandbox():
    calc_out = python_analyst(
        "import pandas as pd\ndf = pd.DataFrame({'x': [10, 20, 30]})\nprint(int(df['x'].mean()))"
    )
    assert "<PYTHON_OUTPUT>" in calc_out and "20" in calc_out

    assert "Security Error" in python_analyst("import os as o\no.system('echo hi')")
    assert "Security Error" in python_analyst("import sqlite3\nsqlite3.connect('company_sales.db')")
    assert "Security Error" in python_analyst("open('.env').read()")
    assert "Security Error" in python_analyst("getattr(__builtins__, 'eval')")
    assert "Security Error" in python_analyst("import pandas as pd\npd.read_csv('company_sales.db')")
    assert "Security Error" in python_analyst("print('{.__class__.__mro__}'.format(1))")


def test_path_traversal_and_report_guard():
    assert "Security Error" in read_file("../../outside_secret.txt")
    assert "Security Error" in read_file(".env")
    assert "Security Error" in save_report("evil.exe", "bad")

    test_rep_name = "pytest_temp_report.md"
    test_rep_path = PROJECT_ROOT / "reports" / test_rep_name
    if test_rep_path.exists():
        test_rep_path.unlink()

    rep_out = save_report(test_rep_name, "# Pytest Report")
    assert "Report successfully saved" in rep_out

    dup_out = save_report(test_rep_name, "# Duplicate")
    assert "already exists" in dup_out
    test_rep_path.unlink()


def test_chart_and_schema_inspection():
    chart_out = generate_chart("bar", "Pytest Chart", ["A", "B"], [10, 25], "pytest_temp_chart.png")
    assert "Chart successfully generated" in chart_out
    chart_file = PROJECT_ROOT / "reports" / "charts" / "pytest_temp_chart.png"
    assert chart_file.exists()
    chart_file.unlink()

    if (PROJECT_ROOT / "company_sales.db").exists():
        schema_out = inspect_database()
        assert "employees" in schema_out and "DATABASE_SCHEMA_AND_SAMPLES" in schema_out


def test_mock_llm_loop_detection_and_max_steps():
    calls = {"count": 0}

    def fake_post(url, headers, payload, timeout=60, max_retries=2):
        calls["count"] += 1
        return (
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "read_file",
                                        "args": {"filepath": "reports/nonexistent_check.txt"},
                                    }
                                }
                            ],
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
            },
            0.01,
            0.0,
        )

    with (
        patch.dict("os.environ", {"GEMINI_API_KEY": "test-dummy-key"}),
        patch.object(agent_mod, "_post_with_retry", side_effect=fake_post),
    ):
        res = agent_mod.run_agent("Test loop guardrail", max_steps=4, save_trace=False)

    assert res["status"] == "failed_max_steps"
    assert res["steps"] == 4
    assert calls["count"] >= 4
