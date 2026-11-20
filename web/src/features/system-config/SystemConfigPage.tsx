import { Button, TextArea } from "../../shared/ui/semi";
import { Bot, Boxes, ChevronDown, ChevronUp, CopyCheck, DatabaseZap, Globe2, RefreshCw, RotateCcw, Save, Settings, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { getInstanceConfig, updateInstanceConfig } from "../../shared/api/systemConfig";
import { getModelCatalog, refreshModelCatalog } from "../../shared/api/modelCatalog";
import { showApiError, showApiSuccess } from "../../shared/api/feedback";
import { MODEL_API_VALUES, SYSTEM_CONFIG_FIELD_CONSTRAINTS } from "../../shared/api/generated/constants";
import { MetricStrip } from "../../shared/components/ResourcePageShell";
import { AsyncContent } from "../../shared/components/AsyncContent";
import { cx } from "../../shared/lib/className";
import type {
  AgentPoolConfig,
  AgentRuntimeConfig,
  FofaConfig,
  InstanceConfig,
  LightRAGConfig,
  ModelApi,
  ModelCatalog,
  ModelRef,
  UpdateInstanceConfigRequest,
} from "../../shared/api/types";
import { useAdminResourceHeader } from "../../shared/hooks/useAdminResourceHeader";
import { useMountedRef } from "../../shared/hooks/useMountedRef";
import { Field, type NumberFieldWidth } from "./ConfigField";
import { ModelEditor, ModelSelect, protocolOptions, providerOptions } from "./ModelEditor";
import { AddProviderButton, AddProviderModal, ProvidersEditor } from "./ProvidersEditor";
import {
  catalogChoices,
  catalogChoicesKey,
  extractionModelToPayload,
  isOpenAIApi,
  modelToForm,
  modelToPayload,
  providerToForm,
  providerToPayload,
  type ModelFormValue,
  type ProviderFormValue,
} from "./modelConfig";

type AgentFormValue = { code: string; name: string; description: string; model: ModelFormValue };
type LightRAGFormValue = Omit<LightRAGConfig, "llm"> & { llm: ModelRef };
type FofaFormValue = FofaConfig;

type ConfigFormValue = {
  providers: ProviderFormValue[];
  agents: AgentFormValue[];
  agent_pool: AgentPoolConfig;
  agent_runtime: AgentRuntimeConfig;
  lightrag: LightRAGFormValue;
  fofa: FofaFormValue;
};

type FieldKey<T, Value> = {
  [Key in keyof T]: T[Key] extends Value ? Key : never;
}[keyof T];

type ConfigField<T> = {
  key: FieldKey<T, number>;
  label: string;
  min?: number;
  max?: number;
  step?: number;
  width?: NumberFieldWidth;
};

type ConfigFieldGroup<T> = {
  title: string;
  fields: ConfigField<T>[];
};

const OPENAI_APIS = MODEL_API_VALUES.filter(isOpenAIApi);
const AGENT_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.AgentConfig;
const POOL_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.AgentPoolConfig;
const RUNTIME_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.AgentRuntimeConfig;
const LIGHTRAG_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.LightRAGConfig;
const FOFA_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.FofaConfig;
const RATIO_STEP = 0.01;

const RUNTIME_FIELD_GROUPS: ConfigFieldGroup<AgentRuntimeConfig>[] = [
  {
    title: "Execution",
    fields: [
      { key: "main_max_turns", label: "Main max turns", min: RUNTIME_CONSTRAINTS.main_max_turns.minimum },
      { key: "subordinate_max_turns", label: "Subordinate max turns", min: RUNTIME_CONSTRAINTS.subordinate_max_turns.minimum },
      { key: "model_stream_idle_timeout_seconds", label: "Stream idle timeout", min: RUNTIME_CONSTRAINTS.model_stream_idle_timeout_seconds.minimum },
      { key: "report_retention_seconds", label: "Report retention seconds", min: RUNTIME_CONSTRAINTS.report_retention_seconds.minimum },
      { key: "traffic_retention_seconds", label: "Traffic retention seconds (0 keeps all)", min: RUNTIME_CONSTRAINTS.traffic_retention_seconds.minimum },
      { key: "traffic_max_entries_per_container", label: "Traffic entries per sandbox (0 keeps all)", min: RUNTIME_CONSTRAINTS.traffic_max_entries_per_container.minimum },
    ],
  },
  {
    title: "Context thresholds",
    fields: [
      ratioField("context_budget_model_call_ratio", "Model call budget"),
      ratioField("context_compression_trigger_ratio", "Trigger ratio"),
      ratioField("context_compression_hard_stop_ratio", "Hard stop ratio"),
      ratioField("context_compression_target_ratio", "Target ratio"),
    ],
  },
  {
    title: "Compression policy",
    fields: [
      ratioField("context_compression_preserve_recent_ratio", "Preserve recent ratio"),
      { key: "context_compression_preserve_recent_items", label: "Preserve recent items", min: RUNTIME_CONSTRAINTS.context_compression_preserve_recent_items.minimum, width: "compact" },
      { key: "context_compression_min_items", label: "Minimum items", min: RUNTIME_CONSTRAINTS.context_compression_min_items.minimum, width: "compact" },
      { key: "context_compression_summary_max_tokens", label: "Summary max tokens", min: RUNTIME_CONSTRAINTS.context_compression_summary_max_tokens.minimum },
    ],
  },
];

const POOL_FIELDS: ConfigField<AgentPoolConfig>[] = [
  { key: "max_size", label: "Max size", min: POOL_CONSTRAINTS.max_size.minimum, width: "compact" },
  { key: "ttl_seconds", label: "TTL seconds", min: POOL_CONSTRAINTS.ttl_seconds.minimum },
  { key: "sweep_interval_seconds", label: "Sweep interval seconds", min: POOL_CONSTRAINTS.sweep_interval_seconds.minimum, width: "compact" },
];

function ratioField(
  key: FieldKey<AgentRuntimeConfig, number>,
  label: string,
): ConfigField<AgentRuntimeConfig> {
  const constraints = RUNTIME_CONSTRAINTS[key as keyof typeof RUNTIME_CONSTRAINTS];
  if (!("exclusiveMinimum" in constraints) || !("exclusiveMaximum" in constraints)) {
    throw new Error(`missing ratio constraints for ${String(key)}`);
  }
  return {
    key,
    label,
    min: constraints.exclusiveMinimum + RATIO_STEP,
    max: constraints.exclusiveMaximum - RATIO_STEP,
    step: RATIO_STEP,
    width: "compact",
  };
}

function toFormValue(config: InstanceConfig): ConfigFormValue {
  if (!config.agent_pool || !config.agent_runtime || !config.lightrag?.llm || !config.fofa) {
    throw new Error("Instance config is incomplete");
  }
  return {
    providers: Object.entries(config.providers ?? {}).map(([id, provider]) => providerToForm(id, provider)),
    agents: Object.values(config.agents ?? {}).map((agent) => ({
      code: agent.code,
      name: agent.name,
      description: agent.description,
      model: modelToForm(agent.model),
    })),
    agent_pool: { ...config.agent_pool },
    agent_runtime: { ...config.agent_runtime },
    lightrag: { ...config.lightrag, llm: { ...config.lightrag.llm } },
    fofa: { ...config.fofa },
  };
}

function cloneFormValue(values: ConfigFormValue): ConfigFormValue {
  return {
    providers: values.providers.map((provider) => ({
      ...provider,
      endpoints: provider.endpoints.map((endpoint) => ({ ...endpoint })),
    })),
    agents: values.agents.map((agent) => ({ ...agent, model: { ...agent.model } })),
    agent_pool: { ...values.agent_pool },
    agent_runtime: { ...values.agent_runtime },
    lightrag: { ...values.lightrag, llm: { ...values.lightrag.llm } },
    fofa: { ...values.fofa },
  };
}

/** Build the update request; throws an Error naming the first field that cannot be saved. */
function toPayload(values: ConfigFormValue): UpdateInstanceConfigRequest {
  const providers: UpdateInstanceConfigRequest["providers"] = {};
  values.providers.forEach((provider) => {
    providers[provider.id] = providerToPayload(provider);
  });
  const agents: UpdateInstanceConfigRequest["agents"] = {};
  values.agents.forEach((agent) => {
    const name = agent.name.trim();
    if (!name) throw new Error(`Agent ${agent.code} needs a name`);
    agents[agent.code] = {
      name,
      description: agent.description.trim(),
      model: modelToPayload(agent.model, `Agent ${name}`, values.providers),
    };
  });
  return {
    providers,
    agents,
    agent_pool: values.agent_pool,
    agent_runtime: values.agent_runtime,
    lightrag: {
      ...values.lightrag,
      embedding_api: values.lightrag.embedding_api.trim(),
      embedding_key: values.lightrag.embedding_key.trim(),
      embedding_model: values.lightrag.embedding_model.trim(),
      llm: extractionModelToPayload(values.lightrag.llm, values.providers),
    },
    fofa: {
      ...values.fofa,
      base_url: values.fofa.base_url.trim(),
      api_key: values.fofa.api_key.trim(),
    },
  };
}

export function SystemConfigPage() {
  const [values, setValues] = useState<ConfigFormValue | null>(null);
  const [savedValues, setSavedValues] = useState<ConfigFormValue | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [refreshingCatalog, setRefreshingCatalog] = useState(false);
  const [addingProvider, setAddingProvider] = useState(false);
  const mountedRef = useMountedRef();
  const loadRequestIdRef = useRef(0);
  const saveRequestIdRef = useRef(0);
  const savingRef = useRef(false);

  useEffect(() => {
    return () => {
      loadRequestIdRef.current += 1;
      saveRequestIdRef.current += 1;
      savingRef.current = false;
    };
  }, []);

  const loadConfig = useCallback(async () => {
    const requestId = loadRequestIdRef.current + 1;
    loadRequestIdRef.current = requestId;
    setLoading(true);
    try {
      const [response, catalogResponse] = await Promise.all([getInstanceConfig(), getModelCatalog()]);
      if (mountedRef.current && loadRequestIdRef.current === requestId && response.data) {
        const nextValues = toFormValue(response.data);
        setValues(nextValues);
        setSavedValues(cloneFormValue(nextValues));
        setCatalog(catalogResponse.data ?? null);
      }
    } catch (error) {
      if (mountedRef.current && loadRequestIdRef.current === requestId) showApiError(error);
    } finally {
      if (mountedRef.current && loadRequestIdRef.current === requestId) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  const metrics = useMemo(() => {
    const agentCount = values?.agents.length ?? 0;
    return [
      { label: "Agents", value: agentCount },
      { label: "Providers", value: values?.providers.length ?? "-" },
      { label: "Pool size", value: values?.agent_pool.max_size ?? "-" },
      { label: "Main turns", value: values?.agent_runtime.main_max_turns ?? "-" },
      {
        label: "Graph / chunks",
        value: values ? `${values.lightrag.graph_matches} / ${values.lightrag.chunk_matches}` : "-",
      },
    ];
  }, [values]);

  const updatePool = (patch: Partial<AgentPoolConfig>) => {
    setValues((current) => current && { ...current, agent_pool: { ...current.agent_pool, ...patch } });
  };

  const updateRuntime = (patch: Partial<AgentRuntimeConfig>) => {
    setValues((current) => current && { ...current, agent_runtime: { ...current.agent_runtime, ...patch } });
  };

  const updateLightRAG = (patch: Partial<LightRAGFormValue>) => {
    setValues((current) => current && { ...current, lightrag: { ...current.lightrag, ...patch } });
  };

  const updateFofa = (patch: Partial<FofaFormValue>) => {
    setValues((current) => current && { ...current, fofa: { ...current.fofa, ...patch } });
  };

  const updateProviders = (providers: ProviderFormValue[]) => {
    setValues((current) => current && { ...current, providers });
  };

  const updateAgent = (code: string, patch: Partial<AgentFormValue>) => {
    setValues((current) => current && {
      ...current,
      agents: current.agents.map((agent) => (agent.code === code ? { ...agent, ...patch } : agent)),
    });
  };

  const applyModelToAllAgents = (model: ModelFormValue) => {
    setValues((current) => current && {
      ...current,
      agents: current.agents.map((agent) => ({ ...agent, model: { ...model } })),
    });
  };

  const handleRefreshCatalog = useCallback(async () => {
    setRefreshingCatalog(true);
    try {
      const response = await refreshModelCatalog();
      if (!mountedRef.current) return;
      setCatalog(response.data ?? null);
      showApiSuccess(response);
    } catch (error) {
      if (mountedRef.current) showApiError(error);
    } finally {
      if (mountedRef.current) setRefreshingCatalog(false);
    }
  }, []);

  const handleCancel = useCallback(() => {
    if (savedValues) setValues(cloneFormValue(savedValues));
  }, [savedValues]);

  const handleSave = useCallback(async () => {
    if (!values || savingRef.current) return;

    let payload: UpdateInstanceConfigRequest;
    try {
      payload = toPayload(values);
    } catch (error) {
      showApiError(error);
      return;
    }

    savingRef.current = true;
    const requestId = saveRequestIdRef.current + 1;
    saveRequestIdRef.current = requestId;
    setSaving(true);
    try {
      const response = await updateInstanceConfig(payload);
      if (!mountedRef.current || saveRequestIdRef.current !== requestId) return;
      showApiSuccess(response);
      if (response.data?.config) {
        const nextValues = toFormValue(response.data.config);
        setValues(nextValues);
        setSavedValues(cloneFormValue(nextValues));
      }
    } catch (error) {
      if (mountedRef.current && saveRequestIdRef.current === requestId) showApiError(error);
    } finally {
      if (saveRequestIdRef.current === requestId) {
        savingRef.current = false;
        if (mountedRef.current) setSaving(false);
      }
    }
  }, [values]);

  const headerActions = useMemo(() => (
    <>
      <Button icon={<X size={16} />} type="tertiary" disabled={!savedValues || saving || loading} onClick={handleCancel}>
        Cancel
      </Button>
      <Button icon={<Save size={16} />} theme="solid" type="primary" loading={saving} disabled={!values} onClick={handleSave}>
        Save
      </Button>
    </>
  ), [handleCancel, loading, savedValues, saving, values]);

  useAdminResourceHeader({
    refreshLabel: "Refresh config",
    loading: loading || saving,
    onRefresh: loadConfig,
    extraActions: headerActions,
  });

  return (
    <section className="system-config-page">
      <MetricStrip metrics={metrics} />

      <div className="system-config-workspace">
        <AsyncContent
          loading={loading}
          empty={values === null}
          emptyIcon={<Settings size={42} />}
          emptyTitle="Configuration is unavailable"
          wrapperClassName="system-config-spin"
        >
          {values ? (
            <div className="system-config-layout">
              <ConfigPanel
                icon={<Boxes size={18} />}
                title="Model providers"
                actions={(
                  <>
                    {catalog ? (
                      <span className="config-panel-note">
                        Catalog {catalog.source.package}@{catalog.source.version}
                      </span>
                    ) : null}
                    <Button icon={<RefreshCw size={14} />} size="small" type="tertiary" loading={refreshingCatalog}
                      onClick={handleRefreshCatalog}>
                      Update catalog
                    </Button>
                    <AddProviderButton onClick={() => setAddingProvider(true)} />
                  </>
                )}
              >
                <ProvidersEditor providers={values.providers} onChange={updateProviders} />
              </ConfigPanel>

              <ConfigPanel icon={<Bot size={18} />} title="Agents">
                <div className="agent-config-list">
                  {values.agents.map((agent) => (
                    <AgentConfigEditor
                      key={agent.code}
                      agent={agent}
                      providers={values.providers}
                      catalog={catalog}
                      onChange={(patch) => updateAgent(agent.code, patch)}
                      onApplyToAll={() => applyModelToAllAgents(agent.model)}
                    />
                  ))}
                </div>
              </ConfigPanel>

              <ConfigPanel icon={<Settings size={18} />} title="Runtime">
                <RuntimeConfigEditor value={values.agent_runtime} onChange={updateRuntime} />
              </ConfigPanel>

              <ConfigPanel icon={<RotateCcw size={18} />} title="Agent pool">
                <ConfigFieldGrid fill fields={POOL_FIELDS} values={values.agent_pool} onChange={updatePool} />
              </ConfigPanel>

              <ConfigPanel icon={<DatabaseZap size={18} />} title="LightRAG">
                <LightRAGConfigEditor value={values.lightrag} providers={values.providers} catalog={catalog}
                  onChange={updateLightRAG} />
              </ConfigPanel>

              <ConfigPanel icon={<Globe2 size={18} />} title="FOFA asset search">
                <FofaConfigEditor value={values.fofa} onChange={updateFofa} />
              </ConfigPanel>
            </div>
          ) : null}
        </AsyncContent>
      </div>

      <AddProviderModal
        open={addingProvider}
        catalog={catalog}
        existingIds={values?.providers.map((provider) => provider.id) ?? []}
        onCancel={() => setAddingProvider(false)}
        onAdd={(provider) => {
          if (values) updateProviders([...values.providers, provider]);
          setAddingProvider(false);
        }}
      />
    </section>
  );
}

function RuntimeConfigEditor({ value, onChange }: {
  value: AgentRuntimeConfig;
  onChange: (patch: Partial<AgentRuntimeConfig>) => void;
}) {
  return (
    <div className="runtime-config-groups">
      {RUNTIME_FIELD_GROUPS.map((group) => (
        <section key={group.title} className="runtime-config-group">
          <h3>{group.title}</h3>
          <ConfigFieldGrid fields={group.fields} values={value} onChange={onChange} />
        </section>
      ))}
    </div>
  );
}

function LightRAGConfigEditor({ value, providers, catalog, onChange }: {
  value: LightRAGFormValue;
  providers: ProviderFormValue[];
  catalog: ModelCatalog | null;
  onChange: (patch: Partial<LightRAGFormValue>) => void;
}) {
  // LightRAG talks Chat Completions, so its model sits on an OpenAI protocol endpoint.
  const llmProviders = providers.filter((provider) => provider.endpoints.some((endpoint) => isOpenAIApi(endpoint.api)));
  const llmProvider = llmProviders.find((provider) => provider.id === value.llm.provider);
  const choices = useMemo(() => catalogChoices(catalog, llmProvider, OPENAI_APIS), [catalog, llmProvider]);
  const ownCatalog = Boolean(catalog?.providers.some((provider) => provider.id === value.llm.provider));
  const updateLlm = (patch: Partial<ModelRef>) => onChange({ llm: { ...value.llm, ...patch } });

  return (
    <div className="config-grid lightrag-config-grid">
      <Field kind="text" label="Embedding API" value={value.embedding_api}
        onChange={(embedding_api) => onChange({ embedding_api })} />
      <Field kind="text" label="Embedding key" value={value.embedding_key} password
        onChange={(embedding_key) => onChange({ embedding_key })} />
      <Field kind="text" label="Embedding model" value={value.embedding_model}
        onChange={(embedding_model) => onChange({ embedding_model })} />
      <Field kind="number" label="Embedding dimension" value={value.embedding_dim}
        width="fill"
        min={LIGHTRAG_CONSTRAINTS.embedding_dim.minimum} max={LIGHTRAG_CONSTRAINTS.embedding_dim.maximum}
        onChange={(embedding_dim) => onChange({ embedding_dim })} />
      <Field kind="select" label="Extraction LLM provider" value={llmProvider ? value.llm.provider : null}
        options={providerOptions(llmProviders)} placeholder="Choose a provider"
        onChange={(providerId) => {
          const next = llmProviders.find((provider) => provider.id === providerId);
          if (!next) return;
          const api = next.endpoints.find((endpoint) => endpoint.api === value.llm.api)?.api
            ?? next.endpoints.find((endpoint) => isOpenAIApi(endpoint.api))?.api;
          if (api) updateLlm({ provider: next.id, api });
        }} />
      <Field kind="select" label="Extraction LLM protocol" value={value.llm.api}
        options={protocolOptions(llmProvider, OPENAI_APIS)}
        onChange={(api) => api && updateLlm({ api: api as ModelApi })} />
      <label className="field">
        <span>Extraction LLM model</span>
        <ModelSelect
          api={value.llm.api}
          id={value.llm.id}
          choices={choices}
          choicesKey={catalogChoicesKey(catalog, llmProvider)}
          labelSource={!ownCatalog}
          onSelectCatalog={(model) => updateLlm({ api: model.api, id: model.id })}
          onSelectCustom={(id) => updateLlm({ id })}
        />
      </label>
      <div className="lightrag-retrieval-fields">
        <Field kind="number" label="Graph matches" value={value.graph_matches} width="fill"
          min={LIGHTRAG_CONSTRAINTS.graph_matches.minimum} max={LIGHTRAG_CONSTRAINTS.graph_matches.maximum}
          onChange={(graph_matches) => onChange({ graph_matches })} />
        <Field kind="number" label="Chunk matches" value={value.chunk_matches} width="fill"
          min={LIGHTRAG_CONSTRAINTS.chunk_matches.minimum} max={LIGHTRAG_CONSTRAINTS.chunk_matches.maximum}
          onChange={(chunk_matches) => onChange({ chunk_matches })} />
      </div>
    </div>
  );
}

function FofaConfigEditor({ value, onChange }: {
  value: FofaFormValue;
  onChange: (patch: Partial<FofaFormValue>) => void;
}) {
  return (
    <div className="config-grid fofa-config-grid">
      <Field kind="toggle" label="Enable FOFA tool" value={value.enabled}
        onChange={(enabled) => onChange({ enabled })} />
      <Field kind="text" label="Base URL" value={value.base_url}
        onChange={(base_url) => onChange({ base_url })} />
      <Field kind="text" label="API key" value={value.api_key}
        maxLength={FOFA_CONSTRAINTS.api_key.maxLength} password
        onChange={(api_key) => onChange({ api_key })} />
    </div>
  );
}

function ConfigPanel({ actions, children, icon, title }: {
  actions?: ReactNode;
  children: ReactNode;
  icon: ReactNode;
  title: string;
}) {
  return (
    <div className="config-panel">
      <div className="config-panel-header">
        <div>
          {icon}
          <h2>{title}</h2>
        </div>
        {actions ? <div className="config-panel-actions">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

function ConfigFieldGrid<T extends object>({ fill = false, fields, values, onChange }: {
  fill?: boolean;
  fields: ConfigField<T>[];
  values: T;
  onChange: (patch: Partial<T>) => void;
}) {
  return (
    <div className={cx("config-value-grid", fill && "config-value-grid-fill")}>
      {fields.map((field) => (
        <Field
          key={String(field.key)}
          kind="number"
          label={field.label}
          value={values[field.key] as number}
          min={field.min}
          max={field.max}
          step={field.step}
          width={field.width}
          onChange={(value) => onChange({ [field.key]: value } as Partial<T>)}
        />
      ))}
    </div>
  );
}

function AgentConfigEditor({ agent, providers, catalog, onChange, onApplyToAll }: {
  agent: AgentFormValue;
  providers: ProviderFormValue[];
  catalog: ModelCatalog | null;
  onChange: (patch: Partial<AgentFormValue>) => void;
  onApplyToAll: () => void;
}) {
  const [advanced, setAdvanced] = useState(false);

  return (
    <div className="agent-config-card">
      <div className="agent-config-card-header">
        <strong>{agent.name || agent.code}</strong>
        <span>{agent.code}</span>
      </div>
      <div className="agent-form-grid">
        <Field kind="text" label="Name" full value={agent.name} maxLength={AGENT_CONSTRAINTS.name.maxLength}
          onChange={(name) => onChange({ name })} />
        <ModelEditor model={agent.model} providers={providers} catalog={catalog} advanced={advanced}
          onChange={(model) => onChange({ model })} />
        <label className="field full">
          <span>Description</span>
          <TextArea value={agent.description} autosize={{ minRows: 1, maxRows: 3 }} onChange={(description) => onChange({ description })} />
        </label>
        <div className={cx("field", "full", "agent-config-actions")}>
          <Button icon={advanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />} size="small" type="tertiary"
            theme="borderless" onClick={() => setAdvanced((current) => !current)}>
            {advanced ? "Hide advanced" : "Advanced"}
          </Button>
          <Button icon={<CopyCheck size={14} />} size="small" type="tertiary" theme="borderless" onClick={onApplyToAll}>
            Use this model for every agent
          </Button>
        </div>
      </div>
    </div>
  );
}
