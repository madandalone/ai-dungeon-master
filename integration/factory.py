import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from integration.errors import IntegrationConfigError
from integration.orchestrator import TurnOrchestrator
from integration.ports import DungeonMaster, RulesProvider
from rules_service.build import DEFAULT_CACHE_DIR, MODES, build_rules_service
from rules_service.embedder import Embedder
from rules_service.errors import RulesUnavailableError


@dataclass(frozen=True)
class RulesSettings:
    book_path: Path
    mode: str = "keyword"
    cache_dir: Path = DEFAULT_CACHE_DIR
    embedding_model: str | None = None

    def __post_init__(self):
        if self.mode not in MODES:
            raise IntegrationConfigError(f"RULES_MODE must be one of {MODES}, got {self.mode!r}")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RulesSettings":
        env = os.environ if env is None else env
        book_path = env.get("RULES_BOOK_PATH")
        if not book_path:
            raise IntegrationConfigError("RULES_BOOK_PATH is not set")
        return cls(
            book_path=Path(book_path),
            mode=env.get("RULES_MODE", "keyword").strip().lower(),
            cache_dir=Path(env.get("RULES_CACHE_DIR", str(DEFAULT_CACHE_DIR))),
            embedding_model=env.get("RULES_EMBEDDING_MODEL") or None,
        )


def build_rules_provider(settings: RulesSettings, embedder: Embedder | None = None) -> RulesProvider:
    """Build the rules retriever. Slow for rag/hybrid on first run (embeds the whole book): call once at startup."""
    if not settings.book_path.is_file():
        raise IntegrationConfigError(f"Rule book not found: {settings.book_path}")
    try:
        return build_rules_service(
            settings.book_path, settings.mode, settings.cache_dir, settings.embedding_model, embedder
        )
    except RulesUnavailableError as exc:
        raise IntegrationConfigError(f"RULES_MODE={settings.mode} needs the embedding model: {exc}") from exc


def build_orchestrator(
    settings: RulesSettings | None = None,
    dm: DungeonMaster | None = None,
    rules: RulesProvider | None = None,
) -> TurnOrchestrator:
    """Wire the real components. Explicit `dm`/`rules` override the defaults (tests, stubs).

    The orchestrator holds no per-turn state, so build it once at startup and reuse it.
    """
    if rules is None:
        rules = build_rules_provider(settings or RulesSettings.from_env())
    if dm is None:
        from ai_dm.dm_engine import AIDungeonMaster

        dm = AIDungeonMaster()
    return TurnOrchestrator(dm, rules)
