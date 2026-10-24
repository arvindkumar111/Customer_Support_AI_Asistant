"""Summarize an evaluation/run log into a few copy-friendly lines: which exceptions happened, which LLM error codes,
and which flows crashed. Handles logs written by PowerShell redirection (UTF-16 or UTF-8, wrapped lines).
Usage (from project root):  python -m scripts.summarize_log e2e_out3.txt
"""
import re
import sys
from collections import Counter
from pathlib import Path


def read_text(path: str) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def summarize(text: str) -> dict:
    lines = [re.sub(r"^[>\s]+", "", l) for l in text.splitlines()]          # drop "> " and indentation added by the console
    exc = Counter()
    for l in lines:
        m = re.match(r"^([A-Za-z_][\w.]*(?:Error|Exception|Timeout|Unavailable|Exhausted))\b:?\s*(.*)$", l)
        if m and not l.startswith(("File ", "raise ")):
            exc[f"{m.group(1)}: {m.group(2)[:150]}"] += 1
    codes = Counter(re.findall(r"failed with (\w+)", text))
    crashes = Counter(re.findall(r"(router crashed|flow \w+ crashed)", text))
    return {"exceptions": exc, "llm_error_codes": codes, "crashes": crashes,
            "model_not_available": len(re.findall(r"not available \(404\)", text))}


if __name__ == "__main__":
    s = summarize(read_text(sys.argv[1]))
    print("EXCEPTIONS (last line of each traceback):")
    for k, v in s["exceptions"].most_common(10):
        print(f"  {v:3d} x {k}")
    if not s["exceptions"]:
        print("  none found")
    print("LLM error codes seen by the retry layer:", dict(s["llm_error_codes"]) or "none")
    print("crashes contained by the orchestrator:", dict(s["crashes"]) or "none")
    print("model-not-available (404) lines:", s["model_not_available"])
