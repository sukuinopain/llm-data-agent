import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.agent as agent_mod
from src.tools import (
    MAX_PYTHON_CODE_CHARS,
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

def test_generate_chart_rejects_non_finite_values():
    charts_dir = PROJECT_ROOT / "reports" / "charts"
    test_cases = [
        ([10.0, float("nan")], "test_nan_chart.png"),
        ([10.0, float("inf")], "test_inf_chart.png"),
        ([10.0, float("-inf")], "test_ninf_chart.png"),
        ([10.0, True], "test_bool_chart.png"),
    ]
    created_paths = [charts_dir / fname for _, fname in test_cases]
    try:
        for values, fname in test_cases:
            res = generate_chart(
                chart_type="bar",
                title="Invalid Chart",
                labels=["A", "B"],
                values=values,
                filename=fname,
            )
            assert "Chart Error" in res
            assert not (charts_dir / fname).exists()
    finally:
        for path in created_paths:
            if path.exists():
                path.unlink()


def test_read_file_extension_order_and_max_rows_guards():
    assert "Security Error" in read_file("nonexistent_secret.key")
    assert "Security Error" in read_file("missing_tool.exe")
    assert "Security Error" in read_file(".env")
    assert "Security Error" in read_file(".env.local")
    assert "Security Error" in read_file(".env.json")

    for bad_max_rows in ("abc", 3.5, None, True):
        res = read_file("reports/missing.csv", max_rows=bad_max_rows)
        assert "File Read Error" in res

    temp_csv = PROJECT_ROOT / "reports" / "pytest_temp_read.csv"
    try:
        temp_csv.parent.mkdir(parents=True, exist_ok=True)
        temp_csv.write_text("dept,sales\nElectronics,100\nSoftware,200\n", encoding="utf-8")

        ok_res = read_file("reports/pytest_temp_read.csv", max_rows=1)
        assert "<FILE_CONTENT_CSV>" in ok_res and "Electronics" in ok_res

        bad_res = read_file("reports/pytest_temp_read.csv", max_rows="bad")
        assert "File Read Error" in bad_res
    finally:
        if temp_csv.exists():
            temp_csv.unlink()


def test_python_analyst_hardening_guards():
    oversized = "x = 1\n" * (MAX_PYTHON_CODE_CHARS // 5)
    assert "Security Error" in python_analyst(oversized)

    blocked_snippets = [
        '"{x.__class__}".format_map({"x": 1})',
        'tmpl = "{.__class__}"\nprint(tmpl.format(1))',
        'print(("{." + "__class__}").format(1))',
        'print(str.format("{.__class__}", 1))',
        'print("{0.__class__:{1}}".format(1, 10))',
        'f = str.format\nprint(f("{.__class__}", 1))',
        "fn = open\nprint(fn('.env').read())",
        "import pandas as pd\nf = pd.read_csv\nprint(f('x.csv'))",
        "import pandas as pd\nprint(pd.read_sql_query('SELECT 1', None))",
        "import numpy as np\nprint(np.loadtxt('x.csv'))",
        "import numpy as np\nprint(np.fromfile('x.bin'))",
        "from pandas.io.common import get_handle",
        "import numpy.ctypeslib",
    ]
    for snippet in blocked_snippets:
        assert "Security Error" in python_analyst(snippet), f"Expected Security Error for: {snippet}"

    fmt_ok = python_analyst('print("Total: {val:,.2f}".format(val=1234.5))')
    assert "<PYTHON_OUTPUT>" in fmt_ok and "1,234.50" in fmt_ok

    analytics_ok = python_analyst(
        "import json\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        "df = pd.DataFrame({'a': [10, 20, 30]})\n"
        "res = {'mean': float(np.mean(df['a'])), 'sum': int(df['a'].sum())}\n"
        "print(json.dumps(res))"
    )
    assert "<PYTHON_OUTPUT>" in analytics_ok and "20.0" in analytics_ok and "60" in analytics_ok
