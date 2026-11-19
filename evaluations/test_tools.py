import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools import inspect_database, run_sql_query, python_analyst, read_file, generate_chart


def test_all_tools():
    # 1. SQL read-only guard
    blocked_sql = run_sql_query("DELETE FROM sales;")
    assert "Security Error" in blocked_sql, f"Expected SQL security error, got: {blocked_sql}"

    # 2. Python analyst safe execution & restriction guard
    calc_out = python_analyst("import pandas as pd\ndf = pd.DataFrame({'x': [10, 20, 30]})\nprint(int(df['x'].mean()))")
    assert calc_out.strip() == "20", f"Expected 20, got: {calc_out}"

    blocked_py = python_analyst("import os\nos.system('echo hi')")
    assert "Security Error" in blocked_py, f"Expected Python security error, got: {blocked_py}"

    # 3. Path traversal guard in read_file
    blocked_path = read_file("../../outside_secret.txt")
    assert "Security Error" in blocked_path, f"Expected path security error, got: {blocked_path}"

    # 4. Chart generation
    chart_out = generate_chart("bar", "Unit Test Chart", ["A", "B"], [10, 25], "unit_test_chart.png")
    assert "Chart successfully generated" in chart_out, f"Expected chart success, got: {chart_out}"
    chart_file = os.path.join(PROJECT_ROOT, "reports", "charts", "unit_test_chart.png")
    assert os.path.exists(chart_file), "Chart PNG file was not created."
    os.remove(chart_file)

    # 5. Schema inspection if DB exists
    if os.path.exists(os.path.join(PROJECT_ROOT, "company_sales.db")):
        schema_out = inspect_database()
        assert "employees" in schema_out and "Sample Rows:" in schema_out

    print("All tool unit and security checks passed successfully!")


if __name__ == "__main__":
    test_all_tools()
