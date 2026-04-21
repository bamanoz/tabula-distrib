#!/usr/bin/env python3
"""multi_model_review — query multiple LLMs via OpenRouter and return verdicts."""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import log_activity, log_supervisor  # noqa: E402
from skills.lib import SkillConfigError, load_skill_config  # noqa: E402


class ToolError(Exception):
    pass


def _load_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    settings["max_models"] = max(1, int(settings.get("max_models", 10)))
    settings["concurrency"] = max(1, int(settings.get("concurrency", 5)))
    settings["timeout_sec"] = max(10, int(settings.get("timeout_sec", 120)))
    if not settings.get("api_key"):
        raise SkillConfigError("OPENROUTER_API_KEY not configured")
    return settings


def _query_model(model: str, content: str, prompt: str, settings: dict) -> dict:
    try:
        import openai
    except ModuleNotFoundError:
        return {
            "model": model,
            "verdict": "ERROR",
            "text": "openai package is required",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_estimate": 0.0,
        }

    client = openai.OpenAI(
        api_key=settings["api_key"],
        base_url=settings.get("base_url") or "https://openrouter.ai/api/v1",
        timeout=settings["timeout_sec"],
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
        )
    except Exception as exc:
        return {
            "model": model,
            "verdict": "ERROR",
            "text": f"request failed: {exc}",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_estimate": 0.0,
        }

    try:
        text = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        text = ""
    verdict = "UNKNOWN"
    for line in text.splitlines()[:3]:
        upper = line.upper()
        if "PASS" in upper:
            verdict = "PASS"
            break
        if "FAIL" in upper:
            verdict = "FAIL"
            break

    usage = getattr(resp, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    cost = 0.0
    try:
        raw = resp.model_dump() if hasattr(resp, "model_dump") else {}
        u = raw.get("usage", {}) if isinstance(raw, dict) else {}
        for key in ("cost", "total_cost"):
            if key in u:
                cost = float(u[key] or 0.0)
                break
    except Exception:
        cost = 0.0

    return {
        "model": model,
        "verdict": verdict,
        "text": text,
        "tokens_in": prompt_tokens,
        "tokens_out": completion_tokens,
        "cost_estimate": cost,
    }


def multi_model_review(params: dict) -> str:
    content = params.get("content", "")
    prompt = params.get("prompt", "")
    models = params.get("models", [])
    if not isinstance(content, str) or not content:
        raise ToolError("content is required")
    if not isinstance(prompt, str) or not prompt:
        raise ToolError("prompt is required")
    if not isinstance(models, list) or not models:
        raise ToolError("models must be a non-empty list")
    if not all(isinstance(m, str) and m.strip() for m in models):
        raise ToolError("models must be strings")

    settings = _load_settings()
    if len(models) > settings["max_models"]:
        raise ToolError(f"too many models ({len(models)}); max={settings['max_models']}")

    results: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=settings["concurrency"]) as pool:
        futures = [pool.submit(_query_model, m.strip(), content, prompt, settings) for m in models]
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({
                    "model": "?",
                    "verdict": "ERROR",
                    "text": f"executor error: {exc}",
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_estimate": 0.0,
                })

    total_cost = sum(float(r.get("cost_estimate", 0.0) or 0.0) for r in results)
    total_in = sum(int(r.get("tokens_in", 0) or 0) for r in results)
    total_out = sum(int(r.get("tokens_out", 0) or 0) for r in results)
    verdicts = [r.get("verdict", "UNKNOWN") for r in results]
    log_activity(
        "multi_model_review",
        f"models={len(models)} verdicts={','.join(verdicts)} cost=${total_cost:.4f}",
    )
    log_supervisor(
        "multi_model_review",
        models=models,
        verdicts=verdicts,
        tokens_in=total_in,
        tokens_out=total_out,
        cost_usd=total_cost,
    )
    return json.dumps({"model_count": len(models), "results": results}, ensure_ascii=False)


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "tool":
        tool_name = sys.argv[2]
        if tool_name != "multi_model_review":
            raise SystemExit(f"unknown tool: {tool_name}")
        params = json.load(sys.stdin)
        try:
            print(multi_model_review(params))
        except ToolError as exc:
            print(f"ERROR: {exc}")
        except SkillConfigError as exc:
            print(f"ERROR: {exc}")
        return
    raise SystemExit("usage: run.py tool multi_model_review")


if __name__ == "__main__":
    main()
