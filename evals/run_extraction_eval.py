"""
Baseline extraction eval.

Runs the *existing* TweakExtractor against evals/golden_reviews.json and
reports Layer 1 metrics:
    - extraction_success_rate
    - edit_count_parity_rate
    - modification_type_accuracy
    - groundedness_accuracy

Does NOT modify production code. Pure measurement to establish a baseline.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv

from llm_pipeline.models import Recipe, Review
from llm_pipeline.tweak_extractor import TweakExtractor

load_dotenv(REPO_ROOT / ".env")

GOLDEN_PATH = REPO_ROOT / "evals" / "golden_reviews.json"
OUTPUT_DIR = REPO_ROOT / "evals" / "results"


def normalize(s: str) -> str:
    return " ".join((s or "").lower().split())


def is_grounded(find: str, recipe: Recipe) -> bool:
    n = normalize(find)
    if not n:
        return False
    for line in list(recipe.ingredients) + list(recipe.instructions):
        if n in normalize(line):
            return True
    return False


def run_one(extractor: TweakExtractor, review_text: str, recipe: Recipe) -> dict:
    review = Review(text=review_text, has_modification=True)
    try:
        mods = extractor.extract_modification(review, recipe)
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}", "modifications": []}
    if not mods:
        return {"success": False, "error": "extractor returned no modifications", "modifications": []}
    return {
        "success": True,
        "error": None,
        "modifications": [m.model_dump() for m in mods],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="baseline",
                        help="Run label; report saved as {label}_extraction_report.json")
    args = parser.parse_args()

    json_report = OUTPUT_DIR / f"{args.label}_extraction_report.json"

    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    recipes = {
        key: Recipe(
            recipe_id=key,
            title=key.replace("_", " ").title(),
            ingredients=spec["ingredients"],
            instructions=spec["instructions"],
        )
        for key, spec in golden["recipes"].items()
    }

    extractor = TweakExtractor(model="gpt-4o-mini")

    per_review = []
    for r in golden["reviews"]:
        recipe = recipes[r["recipe"]]
        print(f"[{r['id']}] {r['recipe']}  expected={len(r['expected_edits'])}", flush=True)
        result = run_one(extractor, r["review_text"], recipe)

        expected_edits = r["expected_edits"]
        expected_types = sorted({e["modification_type"] for e in expected_edits})

        rec = {
            "id": r["id"],
            "recipe": r["recipe"],
            "review_text": r["review_text"],
            "categories": r["categories"],
            "expected_edit_count": len(expected_edits),
            "expected_types": expected_types,
            "success": result["success"],
            "error": result["error"],
        }

        if result["success"]:
            mods = result["modifications"]
            predicted_edits = [e for m in mods for e in m.get("edits", [])]
            predicted_types = sorted({m.get("modification_type") for m in mods})

            rec["predicted_modification_count"] = len(mods)
            rec["predicted_types"] = predicted_types
            rec["predicted_edit_count"] = len(predicted_edits)
            rec["count_parity"] = len(predicted_edits) == len(expected_edits)
            rec["count_delta"] = len(predicted_edits) - len(expected_edits)
            rec["type_accurate"] = bool(set(predicted_types) & set(expected_types))

            grounded = [is_grounded(e.get("find", ""), recipe) for e in predicted_edits]
            rec["grounded_per_edit"] = grounded
            rec["grounded_count"] = sum(grounded)
            rec["all_edits_grounded"] = bool(grounded) and all(grounded)

            rec["predicted_modifications"] = mods

        per_review.append(rec)

    # ---- aggregate metrics ----
    total = len(per_review)
    successes = [r for r in per_review if r["success"]]
    n_success = len(successes)

    n_parity = sum(1 for r in successes if r["count_parity"])
    n_type = sum(1 for r in successes if r["type_accurate"])
    n_fully_grounded = sum(1 for r in successes if r["all_edits_grounded"])

    total_pred_edits = sum(r["predicted_edit_count"] for r in successes)
    total_grounded_edits = sum(r["grounded_count"] for r in successes)

    metrics = {
        "extraction_success_rate": n_success / total,
        "edit_count_parity_rate": (n_parity / n_success) if n_success else 0,
        "modification_type_accuracy": (n_type / n_success) if n_success else 0,
        "groundedness_per_edit": (total_grounded_edits / total_pred_edits) if total_pred_edits else 0,
        "fully_grounded_reviews": (n_fully_grounded / n_success) if n_success else 0,
        "totals": {
            "reviews": total,
            "successful_extractions": n_success,
            "predicted_edits_total": total_pred_edits,
            "grounded_edits_total": total_grounded_edits,
        },
    }

    # ---- failure attribution ----
    buckets = defaultdict(list)
    for r in per_review:
        if not r["success"]:
            buckets["extraction_failed"].append(r["id"])
            continue
        if r["expected_edit_count"] > 1 and r["predicted_edit_count"] == 1:
            buckets["collapsed_to_one_edit"].append(r["id"])
        elif r["count_delta"] < 0:
            buckets["under_extraction"].append(r["id"])
        elif r["count_delta"] > 0:
            buckets["over_extraction"].append(r["id"])
        if not r["type_accurate"]:
            buckets["wrong_modification_type"].append(r["id"])
        if not r["all_edits_grounded"]:
            buckets["ungrounded_find_string"].append(r["id"])

    report = {
        "version": "1.0",
        "label": args.label,
        "model": "gpt-4o-mini",
        "golden_set": str(GOLDEN_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "metrics": metrics,
        "failure_buckets": {k: v for k, v in buckets.items()},
        "per_review": per_review,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== Extraction metrics: {args.label} (gpt-4o-mini) ===")
    for k, v in metrics.items():
        if k == "totals":
            continue
        print(f"  {k}: {v:.2%}" if isinstance(v, float) else f"  {k}: {v}")
    print(f"  totals: {metrics['totals']}")

    print("\n=== Failure buckets ===")
    for bucket, ids in buckets.items():
        head = ", ".join(ids[:8])
        more = f" (+{len(ids)-8} more)" if len(ids) > 8 else ""
        print(f"  {bucket}: {len(ids)}  [{head}{more}]")

    print(f"\nFull JSON report: {json_report}")


if __name__ == "__main__":
    main()
