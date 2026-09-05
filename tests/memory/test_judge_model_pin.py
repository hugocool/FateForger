"""The judge runs on the model .env pins, and the pin is the decision record.

For weeks after this project moved off gemini (2026-08-24, measured in
scripts/bench/ and recorded in infra/dsh/profile/cordis.patch.yml) the memory
server kept judging on gemini-3.6-flash, because OpenRouterJudge carried its
own default and openrouter_judge_from_env never read the pin. CLAUDE.md then
told every agent that extraction "runs on gemini-3.6-flash", and one of them
dutifully moved the .env pin *to* gemini on 2026-09-01. The pin has to be the
one place the decision lives, and the code has to read it.
"""
from __future__ import annotations

from memory.openrouter_judge import DEFAULT_JUDGE_MODEL, OpenRouterJudge, openrouter_judge_from_env


def test_the_default_is_the_decided_flash_tier_model() -> None:
    assert DEFAULT_JUDGE_MODEL == "openai/gpt-oss-120b:nitro"
    assert OpenRouterJudge(api_key="k", base_url="http://x")._model == DEFAULT_JUDGE_MODEL


def test_the_env_pin_wins_over_the_code_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL_FLASH", "vendor/some-model:nitro")

    assert openrouter_judge_from_env()._model == "vendor/some-model:nitro"


def test_an_unset_or_blank_pin_falls_back_to_the_decided_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL_FLASH", "   ")

    assert openrouter_judge_from_env()._model == DEFAULT_JUDGE_MODEL
