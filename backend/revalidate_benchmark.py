"""Revalidate saved Groq outputs locally. This module makes zero provider calls."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from benchmark_intents import DATASET, score, summarize, process_text
from service import TextRequest


class SavedExtraction:
    def __init__(self, payload):
        self.payload = payload

    def extract(self, *_):
        return self.payload


def revalidate(source, output):
    original = json.loads(source.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in json.loads(DATASET.read_text(encoding="utf-8"))}
    records = []
    for row in original["records"]:
        if row.get("state") != "completed":
            records.append(row)
            continue
        case = cases[row["case_id"]]
        response = row["response"]
        started = perf_counter()
        if row.get("raw_extraction") is not None:
            response = process_text(TextRequest(case["message"], case["city"], "evaluation", context=case.get("context")), SavedExtraction(row["raw_extraction"]))
        records.append({**row, "response": response, "score": score(case, response),
                        "local_revalidation_seconds": perf_counter() - started})
    report = {"source": source.name, "live_calls_in_this_step": 0,
              "note": "Scores use saved live model outputs with corrected local validation. seconds retains original measured live process_text latency; local replay time is separate. Original failures remain in the source artifact.",
              "validator_sha256": hashlib.sha256((Path(__file__).parent / "intents.py").read_bytes()).hexdigest(),
              "stop_reason": original["stop_reason"], "summary": summarize(records), "records": records}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).parent / "evaluation" / "live_results.json")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "evaluation" / "revalidated_results.json")
    arguments = parser.parse_args()
    revalidate(arguments.source, arguments.output)
