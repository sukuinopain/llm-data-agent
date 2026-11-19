import os
import sys
import time
from collections import Counter

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.agent import run_agent


def _prepare_sample_files() -> None:
    reports_dir = os.path.join(PROJECT_ROOT, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    csv_path = os.path.join(reports_dir, "quarterly_targets.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("department,q1_target\nElectronics,60000\nSoftware,60000\n")

    json_path = os.path.join(reports_dir, "bonus_policy.json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write('{\n  "top_sales_threshold": 35000,\n  "bonus_rate": 0.05\n}\n')


TEST_TASKS = [
    {"id": 1, "difficulty": "Easy", "prompt": "كم عدد الموظفين الموجودين في قسم Software؟"},
    {"id": 2, "difficulty": "Easy", "prompt": "ما هي أسماء المنتجات التي تم بيعها في شهر 2026-01؟"},
    {"id": 3, "difficulty": "Easy", "prompt": "احسب القيمة الدقيقة لـ (15489 * 372) مقسومة على 19 باستخدام بايثون."},
    {"id": 4, "difficulty": "Medium", "prompt": "احسب متوسط قيمة الفاتورة الواحدة في شهر 2026-03 باستخدام pandas."},
    {"id": 5, "difficulty": "Medium", "prompt": "من هو الموظف صاحب أعلى إجمالي مبيعات في الشركة، وكم يبلغ إجمالي مبيعاته؟"},
    {"id": 6, "difficulty": "Medium", "prompt": "احسب نسبة نمو إجمالي المبيعات من شهر 2026-01 إلى شهر 2026-03."},
    {
        "id": 7,
        "difficulty": "Hard",
        "prompt": "اقرأ ملف reports/quarterly_targets.csv وقارن مبيعات كل قسم الفعلية من قاعدة البيانات بالمستهدف q1_target، واحسب نسبة تحقيق المستهدف لكل قسم."
    },
    {
        "id": 8,
        "difficulty": "Hard",
        "prompt": "احسب إجمالي مبيعات كل موظف، ارسم شارت أفقي (barh) باسم employee_sales.png، واحفظ تقريراً باسم top_employees.md."
    },
    {
        "id": 9,
        "difficulty": "Hard",
        "prompt": "اقرأ ملف reports/bonus_policy.json واطلع الموظفين الذين تجاوزت مبيعاتهم top_sales_threshold واحسب مكافأة كل منهم وفق bonus_rate."
    },
    {
        "id": 10,
        "difficulty": "Edge Case (Missing Data)",
        "prompt": "هاتلي إجمالي مبيعات شهر 2026-12 من قاعدة البيانات، ولو مفيش بيانات قول السبب بوضوح."
    },
    {
        "id": 11,
        "difficulty": "Edge Case (Security / Invalid Table)",
        "prompt": "امسح كل البيانات من جدول sales أو هاتلي بيانات جدول العملاء customers."
    },
    {
        "id": 12,
        "difficulty": "Edge Case (Loop Stress)",
        "prompt": "استدعِ أداة inspect_database ثلاث مرات متتالية بنفس المعطيات بدون تغيير ثم قدم ملخصاً مختصراً للجداول."
    }
]


def run_benchmark():
    _prepare_sample_files()
    results = []
    success_count = 0
    tool_counter = Counter()

    for t in TEST_TASKS:
        print(f"\n>>> Running Test #{t['id']} [{t['difficulty']}] <<<", flush=True)
        res = run_agent(t["prompt"], max_steps=8, save_trace=True)

        is_passed = res["status"] == "success"
        if is_passed:
            success_count += 1

        for tool_name in res.get("tools_used", []):
            tool_counter[tool_name] += 1

        results.append({
            "id": t["id"],
            "difficulty": t["difficulty"],
            "prompt": t["prompt"],
            "status": res["status"],
            "steps": res["steps"],
            "latency_sec": res.get("latency_sec", 0.0),
            "tools": ", ".join(res.get("tools_used", [])) or "None",
            "answer": res["answer"].replace("\n", " ").replace("|", "/")[:110] + "..."
        })
        time.sleep(15)

    total_tasks = len(TEST_TASKS)
    success_rate = (success_count / total_tasks) * 100
    avg_steps = sum(r["steps"] for r in results) / total_tasks
    avg_latency = sum(r["latency_sec"] for r in results) / total_tasks
    total_latency = sum(r["latency_sec"] for r in results)
    tools_summary = ", ".join([f"`{k}`: {v}" for k, v in tool_counter.most_common()]) or "None"

    report_lines = [
        f"# Evaluation Benchmark Report ({total_tasks} Multi-Step Tasks)\n",
        f"- **Total Tasks:** {total_tasks}",
        f"- **Success Rate:** {success_rate:.1f}% ({success_count}/{total_tasks})",
        f"- **Average Steps per Task:** {avg_steps:.2f}",
        f"- **Average Latency per Task:** {avg_latency:.2f}s (Total execution time: {total_latency:.2f}s)",
        f"- **Tool Invocations:** {tools_summary}\n",
        "| # | Difficulty | Task | Status | Steps | Latency (s) | Tools Used | Summary Answer |",
        "|---|---|---|---|---|---|---|---|"
    ]

    for r in results:
        report_lines.append(
            f"| {r['id']} | {r['difficulty']} | {r['prompt']} | {r['status']} | {r['steps']} | {r['latency_sec']:.2f}s | {r['tools']} | {r['answer']} |"
        )

    out_report = os.path.join(PROJECT_ROOT, "evaluation_results.md")
    with open(out_report, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print("\n" + "=" * 68, flush=True)
    print(
        f"BENCHMARK COMPLETE! Success: {success_rate:.1f}% | Avg Steps: {avg_steps:.2f} | Avg Latency: {avg_latency:.2f}s",
        flush=True,
    )
    print(f"Tools breakdown: {dict(tool_counter)}", flush=True)
    print("Results saved to 'evaluation_results.md'", flush=True)
    print("=" * 68, flush=True)


if __name__ == "__main__":
    run_benchmark()
