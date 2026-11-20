import asyncio
from dataclasses import dataclass

from config import (
    AgentConfig,
    AgentPoolConfig,
    AgentRuntimeConfig,
    FofaConfig,
    GlobalConfig,
    LightRAGConfig,
    ProviderConfig,
    get_config,
    read_config_file,
    validate_model_refs,
    write_config_file,
)
from core.agent.registry import AgentRegistry
from core.delegation.subagents import start_subagent_runtime, stop_subagent_runtime
from core.errors import ValidationError
from core.lightrag.runtime import LightRAGSetup, lightrag_client, lightrag_setup, restart_lightrag
from core.runtime.session import AgentSessionPool, get_agent_pool, install_agent_pool
from logger import get_logger
from schema.system_config.config import InstanceConfigSchema, UpdateInstanceConfigRequest


logger = get_logger(__name__)

_config_lock = asyncio.Lock()


@dataclass(frozen=True)
class InstanceConfigApplyResult:
    config: InstanceConfigSchema
    restarted: bool


async def get_instance_config() -> InstanceConfigApplyResult:
    async with _config_lock:
        file_cfg = read_config_file()
        return await _apply_instance_config_from_file(file_cfg)


async def update_instance_config(request: UpdateInstanceConfigRequest) -> InstanceConfigApplyResult:
    async with _config_lock:
        current = read_config_file()
        if set(request.agents) != set(current.agents):
            raise ValidationError("Agent set cannot be changed")

        agents = {
            code: AgentConfig(code=code, name=patch.name, description=patch.description, model=patch.model)
            for code, patch in request.agents.items()
        }
        next_cfg = current.model_copy(update={
            "providers": request.providers,
            "agents": agents,
            "agent_pool": request.agent_pool,
            "agent_runtime": request.agent_runtime,
            "lightrag": request.lightrag,
            "fofa": request.fofa,
        })
        try:
            validate_model_refs(next_cfg)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        write_config_file(next_cfg)
        try:
            return await _apply_instance_config_from_file(next_cfg)
        except BaseException as exc:
            try:
                write_config_file(current)
            except Exception as rollback_error:
                logger.exception("Failed to roll back instance config file")
                exc.add_note(f"Config file rollback also failed: {rollback_error}")
            raise


async def _apply_instance_config_from_file(file_cfg: GlobalConfig) -> InstanceConfigApplyResult:
    previous = _snapshot_instance_config(get_config())
    previous_lightrag = lightrag_setup(get_config())
    next_lightrag = lightrag_setup(file_cfg)
    await _ensure_embedding_storage_compatible(previous.lightrag, file_cfg.lightrag)
    lightrag_runtime_changed = _lightrag_runtime_changed(previous_lightrag, next_lightrag)
    agent_runtime_changed = _apply_instance_config(file_cfg)
    if lightrag_runtime_changed:
        try:
            await restart_lightrag(next_lightrag, rollback_setup=previous_lightrag)
        except BaseException:
            _restore_instance_config(previous)
            raise
        logger.info("LightRAG config applied and runtime rebuilt")
    if agent_runtime_changed:
        try:
            await rebuild_agent_instances()
        except BaseException as exc:
            _restore_instance_config(previous)
            if lightrag_runtime_changed:
                try:
                    await restart_lightrag(previous_lightrag)
                except Exception as rollback_error:
                    logger.exception("Failed to roll back LightRAG after agent rebuild failure")
                    exc.add_note(f"LightRAG rollback also failed: {rollback_error}")
            try:
                await rebuild_agent_instances()
            except BaseException as rollback_error:
                logger.exception("Failed to restore agent runtime after rebuild failure")
                exc.add_note(f"Agent runtime rollback also failed: {rollback_error}")
            raise
        logger.info("Instance config applied and agent instances rebuilt")
    return InstanceConfigApplyResult(
        config=_instance_config_from_global(get_config()),
        restarted=agent_runtime_changed or lightrag_runtime_changed,
    )


def _apply_instance_config(file_cfg: GlobalConfig) -> bool:
    """Copy the file config into the shared config; return whether agents must be rebuilt."""
    current = get_config()
    agent_runtime_changed = _agent_runtime_config_changed(current, file_cfg)
    if not agent_runtime_changed and current.lightrag == file_cfg.lightrag:
        return False

    current.providers = _copy_providers(file_cfg.providers)
    current.agents = _copy_agents(file_cfg.agents)
    current.agent_pool = _copy_agent_pool(file_cfg.agent_pool)
    current.agent_runtime = _copy_agent_runtime(file_cfg.agent_runtime)
    current.lightrag = _copy_lightrag(file_cfg.lightrag)
    current.fofa = _copy_fofa(file_cfg.fofa)
    return agent_runtime_changed


def _agent_runtime_config_changed(current: GlobalConfig, next_cfg: GlobalConfig) -> bool:
    return (
        current.providers != next_cfg.providers
        or current.agents != next_cfg.agents
        or current.agent_pool != next_cfg.agent_pool
        or current.agent_runtime != next_cfg.agent_runtime
        or current.fofa != next_cfg.fofa
    )


def _lightrag_runtime_changed(current: LightRAGSetup, next_setup: LightRAGSetup) -> bool:
    retrieval_fields = {"graph_matches", "chunk_matches"}
    return (
        current.llm_endpoint != next_setup.llm_endpoint
        or current.config.model_dump(exclude=retrieval_fields) != next_setup.config.model_dump(exclude=retrieval_fields)
    )


async def _ensure_embedding_storage_compatible(current: LightRAGConfig, next_cfg: LightRAGConfig) -> None:
    embedding_changed = (
        current.embedding_api != next_cfg.embedding_api
        or current.embedding_model != next_cfg.embedding_model
        or current.embedding_dim != next_cfg.embedding_dim
    )
    if not embedding_changed:
        return

    async with lightrag_client() as rag:
        status_counts = await rag.get_processing_status()
    if any(int(count) > 0 for count in status_counts.values()):
        raise ValidationError(
            "Embedding API, model, and dimension cannot change while LightRAG documents exist",
        )


def _copy_providers(providers: dict[str, ProviderConfig]) -> dict[str, ProviderConfig]:
    return {provider_id: provider.model_copy(deep=True) for provider_id, provider in providers.items()}


def _copy_agents(agents: dict[str, AgentConfig]) -> dict[str, AgentConfig]:
    return {code: agent.model_copy(deep=True) for code, agent in agents.items()}


def _copy_agent_pool(agent_pool: AgentPoolConfig) -> AgentPoolConfig:
    return agent_pool.model_copy(deep=True)


def _copy_agent_runtime(agent_runtime: AgentRuntimeConfig) -> AgentRuntimeConfig:
    return agent_runtime.model_copy(deep=True)


def _copy_lightrag(lightrag: LightRAGConfig) -> LightRAGConfig:
    return lightrag.model_copy(deep=True)


def _copy_fofa(fofa: FofaConfig) -> FofaConfig:
    return fofa.model_copy(deep=True)


def _snapshot_instance_config(cfg: GlobalConfig) -> InstanceConfigSchema:
    return _instance_config_from_global(cfg).model_copy(deep=True)


def _restore_instance_config(snapshot: InstanceConfigSchema) -> None:
    current = get_config()
    current.providers = _copy_providers(snapshot.providers)
    current.agents = _copy_agents(snapshot.agents)
    current.agent_pool = _copy_agent_pool(snapshot.agent_pool)
    current.agent_runtime = _copy_agent_runtime(snapshot.agent_runtime)
    current.lightrag = _copy_lightrag(snapshot.lightrag)
    current.fofa = _copy_fofa(snapshot.fofa)


async def rebuild_agent_instances() -> None:
    old_pool = get_agent_pool()
    replacement_pool = install_agent_pool(AgentSessionPool(AgentRegistry(old_pool.registry.specs)))
    try:
        try:
            await stop_subagent_runtime()
        finally:
            await old_pool.stop()
        await start_subagent_runtime()
        await replacement_pool.start()
    except BaseException as exc:
        logger.exception("Agent instance rebuild failed")
        for name, cleanup in (
            ("subagent runtime", stop_subagent_runtime),
            ("replacement agent pool", replacement_pool.stop),
            ("previous agent pool", old_pool.stop),
        ):
            try:
                await cleanup()
            except BaseException as cleanup_error:
                logger.exception("Failed to clean up %s", name)
                exc.add_note(f"{name.capitalize()} cleanup also failed: {cleanup_error}")
        raise


def _instance_config_from_global(cfg: GlobalConfig) -> InstanceConfigSchema:
    return InstanceConfigSchema(
        providers=cfg.providers,
        agents=cfg.agents,
        agent_pool=cfg.agent_pool,
        agent_runtime=cfg.agent_runtime,
        lightrag=cfg.lightrag,
        fofa=cfg.fofa,
    )
