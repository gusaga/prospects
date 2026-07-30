"""Browser-use agent adapter plus testable domain-specific agent classes."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from .config import Settings
from .prompts import account_discovery_prompt, contact_discovery_prompt, rapport_research_prompt
from .schemas import (
    AccountDiscoveryOutput,
    CompanyCandidate,
    ContactDiscoveryOutput,
    ICPProfile,
    RapportResearchOutput,
)


T = TypeVar("T", bound=BaseModel)


class BrowserAgentUnavailable(RuntimeError):
    """Raised when a live browser task is requested without its local dependency/configuration."""


@dataclass
class AgentRun(Generic[T]):
    output: T
    source_urls: list[str] = field(default_factory=list)
    steps: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class AgentRunner(Protocol):
    async def run(self, task: str, output_model: type[T]) -> AgentRun[T]: ...


class BrowserUseRunner:
    """Use a local Chromium session and an OpenAI-compatible model through browser-use."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, task: str, output_model: type[T]) -> AgentRun[T]:
        if not self.settings.llm_api_key:
            raise BrowserAgentUnavailable("LLM_API_KEY is required to run live browser research. Add it to .env.")
        self.settings.ensure_local_directories()
        os.environ.setdefault("BROWSER_USE_CONFIG_DIR", str(self.settings.browser_config_dir))
        os.environ.setdefault("BROWSER_HARNESS_HOME", str(self.settings.browser_config_dir / "harness"))
        try:
            from browser_use import Agent, Browser, ChatOpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dependency install path
            raise BrowserAgentUnavailable("browser-use is not installed. Run `uv sync` then `browser-use install`.") from exc

        llm_kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "api_key": self.settings.llm_api_key.get_secret_value(),
        }
        if self.settings.llm_base_url:
            llm_kwargs["base_url"] = self.settings.llm_base_url
        browser = Browser(headless=self.settings.browser_headless)
        agent = Agent(
            task=task,
            llm=ChatOpenAI(**llm_kwargs),
            browser=browser,
            output_model_schema=output_model,
            use_vision="auto",
        )
        started = perf_counter()
        try:
            history = await agent.run(max_steps=self.settings.agent_max_steps)
            raw_output = getattr(history, "structured_output", None)
            if raw_output is None:
                raise BrowserAgentUnavailable("Browser agent completed without validated structured output.")
            output = raw_output if isinstance(raw_output, output_model) else output_model.model_validate(raw_output)
            urls = list(dict.fromkeys(getattr(history, "urls", lambda: [])() or []))
            errors = [str(error) for error in (getattr(history, "errors", lambda: [])() or []) if error]
            return AgentRun(
                output=output,
                source_urls=urls,
                steps=int(getattr(history, "number_of_steps", lambda: 0)() or 0),
                duration_seconds=round(perf_counter() - started, 3),
                errors=errors,
            )
        finally:
            stop = getattr(browser, "stop", None)
            if stop:
                result = stop()
                if asyncio.iscoroutine(result):
                    await result


class TargetAccountAgent:
    def __init__(self, runner: AgentRunner, settings: Settings):
        self.runner, self.settings = runner, settings

    async def discover(self, icp: ICPProfile) -> AgentRun[AccountDiscoveryOutput]:
        return await self.runner.run(account_discovery_prompt(icp, self.settings.max_accounts_per_run), AccountDiscoveryOutput)


class ContactDiscoveryAgent:
    def __init__(self, runner: AgentRunner, settings: Settings):
        self.runner, self.settings = runner, settings

    async def discover(self, icp: ICPProfile, company: CompanyCandidate) -> AgentRun[ContactDiscoveryOutput]:
        return await self.runner.run(
            contact_discovery_prompt(
                icp,
                company.name,
                company.website_url,
                self.settings.max_prospects_per_account,
            ),
            ContactDiscoveryOutput,
        )


class RapportAgent:
    def __init__(self, runner: AgentRunner, settings: Settings):
        self.runner, self.settings = runner, settings

    async def research(
        self,
        icp: ICPProfile,
        company: CompanyCandidate,
        prospect_name: str,
        prospect_role: str,
    ) -> AgentRun[RapportResearchOutput]:
        return await self.runner.run(
            rapport_research_prompt(icp, company.name, company.website_url, prospect_name, prospect_role),
            RapportResearchOutput,
        )
