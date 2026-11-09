"""Opt-in live scene evaluation; semantic quality is reviewed by a human."""

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import AIDMError, ProviderError
from ai_dm.prompts import PromptTemplates
from ai_dm.scene_scenarios import scene_scenarios


def main(argv: list[str] | None = None) -> int:
    scenarios = scene_scenarios()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=scenarios, help="Один сценарий для проверки")
    parser.add_argument("--live", action="store_true", help="Отправить сценарий подключённой LLM")
    args = parser.parse_args(argv)
    if args.live and not args.case:
        parser.error("Для --live укажите один --case.")
    selected = {args.case: scenarios[args.case]} if args.case else scenarios
    if not args.live:
        print(json.dumps({
            "mode": "preview", "llm_called": False,
            "scenarios": {key: {"name": value.name, "criteria": value.criteria}
                          for key, value in selected.items()},
        }, ensure_ascii=False, indent=2))
        return 0

    scenario = scenarios[args.case]
    report = {
        "mode": "live", "case": args.case, "name": scenario.name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "request": scenario.request.model_dump(), "criteria": scenario.criteria,
        "review_status": "pending",  # Never turn schema validity into a semantic pass.
    }
    try:
        templates = json.dumps(asdict(PromptTemplates.load()), ensure_ascii=False, sort_keys=True)
        report["prompt_sha256"] = hashlib.sha256(templates.encode("utf-8")).hexdigest()
        response = AIDungeonMaster().process_turn(scenario.request)
        report["response"] = response.model_dump()
        report["status"] = "generated"
    except AIDMError as exc:
        # No traceback or provider body: causes may contain credentials or private data.
        report["status"] = "error"
        report["error"] = {"type": type(exc).__name__}
        if isinstance(exc, ProviderError):
            report["error"]["category"] = exc.category
    report["model"] = os.getenv("MODEL_NAME", "Qwen/Qwen3.8-27B")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
