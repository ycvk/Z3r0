from pydantic import BaseModel, ConfigDict, Field, model_validator

from config import (
    AgentConfig,
    AgentPoolConfig,
    AgentRuntimeConfig,
    FofaConfig,
    LightRAGConfig,
    ModelConfig,
    ProviderConfig,
    ProviderId,
)


class InstanceConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    providers: dict[ProviderId, ProviderConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    agent_pool: AgentPoolConfig = Field(default_factory=AgentPoolConfig)
    agent_runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    lightrag: LightRAGConfig = Field(default_factory=LightRAGConfig)
    fofa: FofaConfig = Field(default_factory=FofaConfig)

    @model_validator(mode="after")
    def validate_agent_codes(self):
        for code, agent in self.agents.items():
            if agent.code != code:
                raise ValueError(f"Agent code mismatch: {code}")
        return self


class UpdateAgentConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str
    model: ModelConfig


class UpdateInstanceConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[ProviderId, ProviderConfig]
    agents: dict[str, UpdateAgentConfigRequest]
    agent_pool: AgentPoolConfig
    agent_runtime: AgentRuntimeConfig
    lightrag: LightRAGConfig
    fofa: FofaConfig


class UpdateInstanceConfigResponse(BaseModel):
    config: InstanceConfigSchema
    restarted: bool
