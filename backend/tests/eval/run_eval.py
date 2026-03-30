#!/usr/bin/env python3
"""
Evaluation script: runs NLU + catalog resolver on eval_dataset.json
and reports intent accuracy, entity accuracy, and clarification accuracy.

Exits with code 1 if intent accuracy < 90%.

Usage:
    cd backend
    python -m tests.eval.run_eval
    # or directly:
    python tests/eval/run_eval.py
"""

import asyncio
import json
import sys
import unicodedata
from pathlib import Path

# Ensure backend/ is in path when running directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.agent.catalog_resolver import resolve_entities
from app.agent.nlu import parse_query
from app.schemas.agent import ConversationContext, Intent

EVAL_DATASET_PATH = Path(__file__).parent / "eval_dataset.json"
INTENT_ACCURACY_THRESHOLD = 0.90


def _normalize(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    return " ".join(
        c for c in nfd if unicodedata.category(c) != "Mn"
    ).upper().split().__str__().replace("[", "").replace("]", "").replace("'", "").replace(", ", " ")


def _load_catalog():
    from app.services.catalog_service import get_catalog
    return get_catalog()


async def _run_case(case: dict, catalog: dict) -> dict:
    context = ConversationContext(conversation_id="eval")
    try:
        parsed = await parse_query(case["message"], context)
        resolved = resolve_entities(parsed, catalog)

        intent_ok = resolved.intent.value == case["expected_intent"]
        clarification_ok = True
        if "expected_needs_clarification" in case:
            clarification_ok = resolved.needs_clarification == case["expected_needs_clarification"]

        entity_ok = True
        for key, expected_val in case.get("expected_entities", {}).items():
            actual_val = getattr(resolved, key, None)
            if actual_val is None or actual_val.upper() != expected_val.upper():
                entity_ok = False
                break

        return {
            "id": case["id"],
            "message": case["message"],
            "expected_intent": case["expected_intent"],
            "actual_intent": resolved.intent.value,
            "intent_ok": intent_ok,
            "entity_ok": entity_ok,
            "clarification_ok": clarification_ok,
            "needs_clarification": resolved.needs_clarification,
            "confidence": resolved.confidence,
            "notes": case.get("notes", ""),
        }
    except Exception as exc:
        return {
            "id": case["id"],
            "message": case["message"],
            "expected_intent": case["expected_intent"],
            "actual_intent": "ERROR",
            "intent_ok": False,
            "entity_ok": False,
            "clarification_ok": False,
            "needs_clarification": False,
            "confidence": 0.0,
            "error": str(exc),
            "notes": case.get("notes", ""),
        }


async def main() -> int:
    with open(EVAL_DATASET_PATH, encoding="utf-8") as f:
        dataset = json.load(f)

    catalog = _load_catalog()
    print(f"\n{'='*60}")
    print(f"Running eval on {len(dataset)} cases")
    print(f"Catalog source: {catalog.get('source', 'unknown')}")
    print(f"{'='*60}\n")

    results = []
    for case in dataset:
        result = await _run_case(case, catalog)
        results.append(result)
        status = "✓" if result["intent_ok"] else "✗"
        ent_status = "✓" if result["entity_ok"] else "✗"
        print(
            f"[{status}] {result['id']:6s} intent={result['intent_ok']} ent={ent_status} "
            f"| {result['expected_intent']:35s} → {result['actual_intent']:35s} "
            f"| conf={result['confidence']:.2f} | {result['notes']}"
        )

    total = len(results)
    intent_passed = sum(1 for r in results if r["intent_ok"])
    entity_passed = sum(1 for r in results if r["entity_ok"])
    clarif_passed = sum(1 for r in results if r["clarification_ok"])

    intent_acc = intent_passed / total
    entity_acc = entity_passed / total
    clarif_acc = clarif_passed / total

    print(f"\n{'='*60}")
    print(f"RESULTS ({total} cases)")
    print(f"  Intent accuracy:        {intent_passed}/{total} = {intent_acc:.1%}")
    print(f"  Entity accuracy:        {entity_passed}/{total} = {entity_acc:.1%}")
    print(f"  Clarification accuracy: {clarif_passed}/{total} = {clarif_acc:.1%}")
    print(f"  Threshold:              {INTENT_ACCURACY_THRESHOLD:.0%}")

    if intent_acc >= INTENT_ACCURACY_THRESHOLD:
        print(f"\n✓ PASS — intent accuracy {intent_acc:.1%} >= {INTENT_ACCURACY_THRESHOLD:.0%}")
        return 0
    else:
        print(f"\n✗ FAIL — intent accuracy {intent_acc:.1%} < {INTENT_ACCURACY_THRESHOLD:.0%}")
        failed = [r for r in results if not r["intent_ok"]]
        print("\nFailed cases:")
        for r in failed:
            print(f"  {r['id']}: expected={r['expected_intent']} got={r['actual_intent']}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
