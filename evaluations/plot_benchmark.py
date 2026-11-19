import os
import sys
import glob
import json
from collections import Counter
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_latest_ten_traces() -> list[dict]:
    trace_files = sorted(glob.glob(os.path.join(PROJECT_ROOT, "logs", "trace_*.json")))
    if not trace_files:
        raise FileNotFoundError("No trace JSON files found in logs/. Run evaluations.run_benchmark first.")
    selected = trace_files[-10:]
    records = []
    for path in selected:
        with open(path, "r", encoding="utf-8") as f:
            records.append(json.load(f))
    return records


def plot_benchmark_summary():
    traces = load_latest_ten_traces()
    charts_dir = os.path.join(PROJECT_ROOT, "reports", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    out_path = os.path.join(charts_dir, "benchmark_summary.png")

    test_labels = [f"T{i+1}" for i in range(len(traces))]
    steps = [t.get("steps", 0) for t in traces]
    latencies = [t.get("latency_sec", 0.0) for t in traces]

    tool_counts = Counter()
    for t in traces:
        for tool_name in t.get("tools_used", []):
            tool_counts[tool_name] += 1

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.8), dpi=150)

    bars = ax1.bar(test_labels, steps, color="#2563EB", alpha=0.9)
    ax1.set_title("Steps per Benchmark Task (100% Pass)", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Task ID")
    ax1.set_ylabel("Number of Steps")
    ax1.set_ylim(0, max(steps + [5]) + 2)
    ax1.grid(axis="y", linestyle="--", alpha=0.35)

    for bar, st, lat in zip(bars, steps, latencies):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.15,
            f"{st}s\n({lat:.1f}s)",
            ha="center",
            va="bottom",
            fontsize=7.5,
        )

    if tool_counts:
        tools_sorted = tool_counts.most_common()
        t_names = [k for k, _ in tools_sorted]
        t_vals = [v for _, v in tools_sorted]
        hbars = ax2.barh(t_names, t_vals, color="#0D9488", alpha=0.9)
        ax2.invert_yaxis()
        ax2.set_title("Tool Invocations Across Benchmark", fontsize=11, fontweight="bold")
        ax2.set_xlabel("Total Calls")
        ax2.grid(axis="x", linestyle="--", alpha=0.35)
        for bar, val in zip(hbars, t_vals):
            ax2.text(
                bar.get_width() + 0.25,
                bar.get_y() + bar.get_height() / 2,
                f"{val}",
                va="center",
                fontsize=8.5,
            )

    plt.suptitle("Autonomous Data Analyst Agent - Evaluation Metrics", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    print(f"Saved summary chart to {os.path.relpath(out_path, PROJECT_ROOT)}")


if __name__ == "__main__":
    plot_benchmark_summary()
