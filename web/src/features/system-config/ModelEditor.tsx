import { Select } from "../../shared/ui/semi";
import { useMemo } from "react";
import { MODEL_API, SYSTEM_CONFIG_FIELD_CONSTRAINTS } from "../../shared/api/generated/constants";
import type { CatalogModel, ModelApi, ModelCatalog, ReasoningEffort } from "../../shared/api/types";
import { Field } from "./ConfigField";
import {
  applyCatalogModel,
  catalogChoices,
  catalogChoicesKey,
  findCatalogModel,
  formatTokens,
  MODEL_API_LABEL,
  reasoningEffortOptions,
  takesThinkingBudget,
  type CatalogChoice,
  type ModelFormValue,
  type ProviderFormValue,
} from "./modelConfig";

const MODEL_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.ModelConfig;
const MODEL_LIST_HEIGHT = 288;
const MODEL_LIST_ITEM_HEIGHT = 36;

function choiceKey(choice: CatalogChoice): string {
  return `${choice.model.api}\u0000${choice.model.id}`;
}

export function providerOptions(providers: ProviderFormValue[]) {
  return providers.map((provider) => ({ label: provider.name || provider.id, value: provider.id }));
}

export function protocolOptions(provider: ProviderFormValue | undefined, apis?: readonly ModelApi[]) {
  return (provider?.endpoints ?? [])
    .filter((endpoint) => !apis || apis.includes(endpoint.api))
    .map((endpoint) => ({ label: MODEL_API_LABEL[endpoint.api], value: endpoint.api }));
}

/** Pick a model from the catalog entries a provider can serve, or type a model id it does not list. */
export function ModelSelect({ api, id, choices, choicesKey, labelSource, onSelectCatalog, onSelectCustom }: {
  api: ModelApi;
  id: string;
  choices: CatalogChoice[];
  choicesKey: string;
  labelSource: boolean;
  onSelectCatalog: (model: CatalogModel) => void;
  onSelectCustom: (id: string) => void;
}) {
  const byKey = useMemo(() => new Map(choices.map((choice) => [choiceKey(choice), choice])), [choices]);
  const options = useMemo(() => choices.map((choice) => ({
    value: choiceKey(choice),
    label: labelSource
      ? `${choice.model.name} (${choice.model.id}) — ${choice.provider.name}`
      : `${choice.model.name} (${choice.model.id})`,
  })), [choices, labelSource]);
  const selected = choices.find((choice) => choice.model.id === id && choice.model.api === api);

  return (
    <Select
      // A creatable Semi Select keeps its previous options when the option list changes, so the
      // list is remounted whenever it comes from a different provider or catalog.
      key={choicesKey}
      filter
      allowCreate
      value={selected ? choiceKey(selected) : id || undefined}
      optionList={options}
      placeholder="Search the catalog or type a model id"
      virtualize={{ height: MODEL_LIST_HEIGHT, width: "100%", itemSize: MODEL_LIST_ITEM_HEIGHT }}
      onChange={(next) => {
        if (typeof next !== "string") return;
        const choice = byKey.get(next);
        if (choice) onSelectCatalog(choice.model);
        else onSelectCustom(next.trim());
      }}
    />
  );
}

function CatalogDefault({ value, catalogValue, onReset }: {
  value: number | null;
  catalogValue: number;
  onReset: () => void;
}) {
  if (value === catalogValue) return <>Catalog default</>;
  return (
    <>
      Catalog default {formatTokens(catalogValue)}
      <button type="button" className="field-hint-action" onClick={onReset}>Reset</button>
    </>
  );
}

export function ModelEditor({ model, providers, catalog, advanced, onChange }: {
  model: ModelFormValue;
  providers: ProviderFormValue[];
  catalog: ModelCatalog | null;
  advanced: boolean;
  onChange: (model: ModelFormValue) => void;
}) {
  const provider = providers.find((candidate) => candidate.id === model.provider);
  const choices = useMemo(() => catalogChoices(catalog, provider), [catalog, provider]);
  const catalogModel = findCatalogModel(choices, model);
  const ownCatalog = Boolean(catalog?.providers.some((candidate) => candidate.id === model.provider));
  const efforts = reasoningEffortOptions(model.api, catalogModel);
  const patch = (fields: Partial<ModelFormValue>) => onChange({ ...model, ...fields });

  const changeProvider = (providerId: string | null) => {
    const next = providers.find((candidate) => candidate.id === providerId);
    if (!next) return;
    const servesProtocol = next.endpoints.some((endpoint) => endpoint.api === model.api);
    patch({ provider: next.id, api: servesProtocol ? model.api : next.endpoints[0]?.api ?? model.api });
  };

  return (
    <>
      <Field kind="select" label="Provider" value={provider ? model.provider : null}
        options={providerOptions(providers)} placeholder="Choose a provider" onChange={changeProvider} />
      <Field kind="select" label="Protocol" value={model.api} options={protocolOptions(provider)}
        onChange={(api) => api && patch({ api: api as ModelApi })} />
      <label className="field full">
        <span>Model</span>
        <ModelSelect
          api={model.api}
          id={model.id}
          choices={choices}
          choicesKey={catalogChoicesKey(catalog, provider)}
          labelSource={!ownCatalog}
          onSelectCatalog={(selected) => onChange(applyCatalogModel(model, selected))}
          onSelectCustom={(id) => patch({ id })}
        />
        {catalogModel ? (
          <small className="field-hint">
            {formatTokens(catalogModel.context_window)} context · {formatTokens(catalogModel.max_tokens)} max output
            {" · "}${catalogModel.cost.input} in / ${catalogModel.cost.output} out per 1M
            {catalogModel.image_input ? " · images" : ""}
            {catalogModel.reasoning ? " · reasoning" : ""}
          </small>
        ) : model.id ? (
          <small className="field-hint">Not in the catalog; every parameter below is yours to set.</small>
        ) : null}
      </label>
      <Field kind="number" label="Context window" value={model.context_window} width="fill"
        min={MODEL_CONSTRAINTS.context_window.minimum}
        hint={catalogModel ? (
          <CatalogDefault value={model.context_window} catalogValue={catalogModel.context_window}
            onReset={() => patch({ context_window: catalogModel.context_window })} />
        ) : "0 disables context compaction"}
        onChange={(context_window) => patch({ context_window })} />
      <Field kind="optional-number" label="Max output tokens" value={model.max_tokens} min={1}
        placeholder={model.api === MODEL_API.ANTHROPIC_MESSAGES ? "Required" : "Provider default"}
        hint={catalogModel ? (
          <CatalogDefault value={model.max_tokens} catalogValue={catalogModel.max_tokens}
            onReset={() => patch({ max_tokens: catalogModel.max_tokens })} />
        ) : undefined}
        onChange={(max_tokens) => patch({ max_tokens })} />
      <Field kind="select" label="Reasoning effort" value={model.reasoning_effort} clearable
        options={efforts.map((effort) => ({ label: effort, value: effort }))}
        placeholder={catalogModel && efforts.length === 0 ? "No effort control" : "Provider default"}
        onChange={(effort) => patch({ reasoning_effort: effort as ReasoningEffort | null })} />
      {takesThinkingBudget(model.api) ? (
        <Field kind="optional-number" label="Thinking budget tokens" value={model.thinking_budget_tokens} min={0}
          placeholder={catalogModel && !catalogModel.thinking_budget ? "Uses effort instead" : "Provider default"}
          onChange={(thinking_budget_tokens) => patch({ thinking_budget_tokens })} />
      ) : null}
      {advanced ? (
        <>
          <Field kind="optional-number" label="Temperature" value={model.temperature} min={0} max={2} step={0.1}
            placeholder="Provider default" onChange={(temperature) => patch({ temperature })} />
          <Field kind="optional-number" label="Top P" value={model.top_p} min={0.01} max={1} step={0.05}
            placeholder="Provider default" onChange={(top_p) => patch({ top_p })} />
          <Field kind="json" label="Extra headers (JSON)" full value={model.extra_headers}
            placeholder='{"X-Header": "value"}' onChange={(extra_headers) => patch({ extra_headers })} />
          <Field kind="json" label="Extra request body (JSON)" full value={model.extra_body}
            placeholder='{"service_tier": "flex"}' onChange={(extra_body) => patch({ extra_body })} />
        </>
      ) : null}
    </>
  );
}
