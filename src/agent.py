import os
import sys
import time
import json
import uuid
import requests
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools import TOOLS_MAP, TOOLS_DECLARATIONS

MODEL = "gemini-3.1-flash-lite"


def _get_api_url() -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
    return f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"


def _post_with_retry(url: str, payload: dict, timeout: int = 60, max_retries: int = 2) -> dict:
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            if resp.status_code == 429 and attempt < max_retries:
                wait_sec = 15 * (attempt + 1)
                print(f"Rate limited (429). Waiting {wait_sec}s before retry...", flush=True)
                time.sleep(wait_sec)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(3)
    raise last_err


def _save_trace(trace_record: dict) -> str:
    logs_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"trace_{ts}_{trace_record['run_id'][:8]}.json"
    filepath = os.path.join(logs_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(trace_record, f, ensure_ascii=False, indent=2)
    return os.path.relpath(filepath, PROJECT_ROOT)


def run_agent(task: str, max_steps: int = 8, save_trace: bool = True) -> dict:
    start_time = time.perf_counter()
    run_id = str(uuid.uuid4())
    url = _get_api_url()

    system_prompt = (
        "You are an expert Data Analyst AI Agent. "
        "1. Always call `inspect_database` first if you don't know table/column names or value formats. "
        "2. Use `run_sql_query` for SQLite queries and `read_file` for CSV/JSON/TXT/MD files. Never guess data. "
        "3. Always use `python_analyst` for calculations, statistics, or data transformations. Never do mental math. "
        "4. Use `generate_chart` when asked for a chart/visualization or when a visual comparison adds clarity. "
        "5. Use `save_report` when asked to save a report or summary to a file. "
        "6. If a tool returns an error or empty result, inspect the reason carefully and adapt your next step. "
        "7. Always write your final response in the same language as the user's task (Arabic if the task is in Arabic, English if in English)."
    )

    contents = [{"role": "user", "parts": [{"text": task}]}]
    history_signatures = []
    tools_used = []
    step_logs = []

    print(f"\n{'='*55}\nTask: {task}\n{'='*55}", flush=True)

    for step in range(1, max_steps + 1):
        step_start = time.perf_counter()
        print(f"\n--- Step {step} ---", flush=True)

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "tools": TOOLS_DECLARATIONS,
            "generationConfig": {"temperature": 0.1},
        }

        try:
            data = _post_with_retry(url, payload, timeout=60, max_retries=2)
        except Exception as e:
            latency_sec = round(time.perf_counter() - start_time, 3)
            step_logs.append({
                "step": step,
                "error": str(e),
                "step_latency_sec": round(time.perf_counter() - step_start, 3),
            })
            print(f"API Error: {e}", flush=True)
            trace_record = {
                "run_id": run_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "model": MODEL,
                "task": task,
                "status": "api_error",
                "steps": step,
                "latency_sec": latency_sec,
                "tools_used": tools_used,
                "answer": str(e),
                "step_logs": step_logs,
            }
            trace_file = _save_trace(trace_record) if save_trace else None
            return {
                "status": "api_error",
                "steps": step,
                "latency_sec": latency_sec,
                "tools_used": tools_used,
                "trace_file": trace_file,
                "answer": str(e),
            }

        candidate = data["candidates"][0]["content"]
        contents.append(candidate)
        parts = candidate.get("parts", [])
        fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not fn_calls:
            final_text = "".join([p.get("text", "") for p in parts]).strip()
            latency_sec = round(time.perf_counter() - start_time, 3)
            step_logs.append({
                "step": step,
                "type": "final_answer",
                "step_latency_sec": round(time.perf_counter() - step_start, 3),
            })
            print(f"Finished in {step} steps ({latency_sec}s)!", flush=True)
            trace_record = {
                "run_id": run_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "model": MODEL,
                "task": task,
                "status": "success",
                "steps": step,
                "latency_sec": latency_sec,
                "tools_used": tools_used,
                "answer": final_text,
                "step_logs": step_logs,
            }
            trace_file = _save_trace(trace_record) if save_trace else None
            return {
                "status": "success",
                "steps": step,
                "latency_sec": latency_sec,
                "tools_used": tools_used,
                "trace_file": trace_file,
                "answer": final_text,
            }

        response_parts = []
        for fn_call in fn_calls:
            fn_name = fn_call["name"]
            fn_args = fn_call.get("args", {})
            tool_start = time.perf_counter()

            signature = f"{fn_name}:{json.dumps(fn_args, sort_keys=True, ensure_ascii=False)}"
            loop_triggered = False

            if history_signatures.count(signature) >= 2:
                loop_triggered = True
                print(f"[Loop Detected!] {signature}", flush=True)
                output = (
                    "SYSTEM WARNING: You already called this exact tool with the same arguments twice. "
                    "Stop repeating and either try a different approach or conclude your answer."
                )
            else:
                history_signatures.append(signature)
                tools_used.append(fn_name)
                print(f"Calling: {fn_name} | Args: {fn_args}", flush=True)
                if fn_name in TOOLS_MAP:
                    try:
                        output = TOOLS_MAP[fn_name](**fn_args)
                    except TypeError as te:
                        output = f"Tool Argument Error for '{fn_name}': {str(te)}"
                else:
                    output = f"Error: Unknown tool '{fn_name}'"
                print(f"Output: {str(output)[:160]}...", flush=True)

            tool_latency = round(time.perf_counter() - tool_start, 3)
            step_logs.append({
                "step": step,
                "tool": fn_name,
                "args": fn_args,
                "loop_triggered": loop_triggered,
                "tool_latency_sec": tool_latency,
                "output_preview": str(output)[:400],
            })

            response_parts.append({
                "functionResponse": {
                    "name": fn_name,
                    "response": {"result": output},
                }
            })

        contents.append({"role": "user", "parts": response_parts})

    latency_sec = round(time.perf_counter() - start_time, 3)
    print("Terminated: Max steps reached!", flush=True)
    trace_record = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "task": task,
        "status": "failed_max_steps",
        "steps": max_steps,
        "latency_sec": latency_sec,
        "tools_used": tools_used,
        "answer": "Stopped due to max_steps limit.",
        "step_logs": step_logs,
    }
    trace_file = _save_trace(trace_record) if save_trace else None
    return {
        "status": "failed_max_steps",
        "steps": max_steps,
        "latency_sec": latency_sec,
        "tools_used": tools_used,
        "trace_file": trace_file,
        "answer": "Stopped due to max_steps limit.",
    }


if __name__ == "__main__":
    result = run_agent(
        "احسب إجمالي مبيعات كل قسم، وطلعلي نسبة مساهمة كل قسم، وارسم شارت عمودي (bar) باسم dept_sales.png يوضح مبيعات كل قسم."
    )
    print("\nFinal Answer:\n", result["answer"], flush=True)
    print(f"\nLatency: {result['latency_sec']}s | Tools: {result['tools_used']} | Trace: {result['trace_file']}", flush=True)
