import { Button, Input, Select } from "../../shared/ui/semi";
import { Boxes, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { MODEL_API, MODEL_API_VALUES, SYSTEM_CONFIG_FIELD_CONSTRAINTS } from "../../shared/api/generated/constants";
import type { ModelApi, ModelCatalog } from "../../shared/api/types";
import { FormField } from "../../shared/components/FormField";
import { ResourceModal } from "../../shared/components/ResourceModal";
import { Field } from "./ConfigField";
import { catalogProviderEndpoints, MODEL_API_LABEL, type ProviderFormValue } from "./modelConfig";

const PROVIDER_CONSTRAINTS = SYSTEM_CONFIG_FIELD_CONSTRAINTS.ProviderConfig;
const CUSTOM_PROVIDER = "";

export function ProvidersEditor({ providers, onChange }: {
  providers: ProviderFormValue[];
  onChange: (providers: ProviderFormValue[]) => void;
}) {
  const update = (id: string, patch: Partial<ProviderFormValue>) => {
    onChange(providers.map((provider) => (provider.id === id ? { ...provider, ...patch } : provider)));
  };

  if (providers.length === 0) {
    return <p className="config-empty">No providers yet. Add one to give the agents a model.</p>;
  }
  return (
    <div className="agent-config-list">
      {providers.map((provider) => (
        <ProviderCard
          key={provider.id}
          provider={provider}
          onChange={(patch) => update(provider.id, patch)}
          onRemove={() => onChange(providers.filter((candidate) => candidate.id !== provider.id))}
        />
      ))}
    </div>
  );
}

function ProviderCard({ provider, onChange, onRemove }: {
  provider: ProviderFormValue;
  onChange: (patch: Partial<ProviderFormValue>) => void;
  onRemove: () => void;
}) {
  const unused = MODEL_API_VALUES.filter((api) => !provider.endpoints.some((endpoint) => endpoint.api === api));
  const setBaseUrl = (api: ModelApi, base_url: string) => {
    onChange({
      endpoints: provider.endpoints.map((endpoint) => (endpoint.api === api ? { ...endpoint, base_url } : endpoint)),
    });
  };

  return (
    <div className="agent-config-card">
      <div className="agent-config-card-header">
        <strong>{provider.name || provider.id}</strong>
        <span>{provider.id}</span>
        <Button icon={<Trash2 size={14} />} size="small" type="danger" theme="borderless"
          aria-label={`Remove provider ${provider.id}`} onClick={onRemove} />
      </div>
      <div className="agent-form-grid">
        <Field kind="text" label="Name" value={provider.name} maxLength={PROVIDER_CONSTRAINTS.name.maxLength}
          onChange={(name) => onChange({ name })} />
        <Field kind="text" label="API key" value={provider.api_key} password
          onChange={(api_key) => onChange({ api_key })} />
        {provider.endpoints.map((endpoint) => (
          <label key={endpoint.api} className="field full">
            <span>{MODEL_API_LABEL[endpoint.api]} base URL</span>
            <div className="provider-endpoint-row">
              <Input value={endpoint.base_url} placeholder="https://" autoComplete="off"
                onChange={(base_url) => setBaseUrl(endpoint.api, base_url)} />
              <Button icon={<Trash2 size={14} />} size="small" type="tertiary" theme="borderless"
                aria-label={`Remove ${MODEL_API_LABEL[endpoint.api]} endpoint`}
                disabled={provider.endpoints.length === 1}
                onClick={() => onChange({
                  endpoints: provider.endpoints.filter((candidate) => candidate.api !== endpoint.api),
                })} />
            </div>
          </label>
        ))}
        {unused.length > 0 ? (
          <label className="field full">
            <span>Add endpoint</span>
            <Select
              value={undefined}
              placeholder="Protocol"
              optionList={unused.map((api) => ({ label: MODEL_API_LABEL[api], value: api }))}
              onChange={(api) => {
                if (typeof api !== "string") return;
                onChange({ endpoints: [...provider.endpoints, { api: api as ModelApi, base_url: "" }] });
              }}
            />
          </label>
        ) : null}
        <Field kind="json" label="Headers (JSON)" full value={provider.headers}
          placeholder='{"X-Header": "value"}' onChange={(headers) => onChange({ headers })} />
      </div>
    </div>
  );
}

type NewProviderValue = {
  catalogId: string;
  id: string;
  name: string;
  api_key: string;
  api: ModelApi;
  base_url: string;
};

const EMPTY_PROVIDER: NewProviderValue = {
  catalogId: CUSTOM_PROVIDER,
  id: "",
  name: "",
  api_key: "",
  api: MODEL_API.OPENAI_COMPLETIONS,
  base_url: "",
};

/** Add a provider from the catalog with its endpoints filled in, or a custom one such as a relay. */
export function AddProviderModal({ open, catalog, existingIds, onCancel, onAdd }: {
  open: boolean;
  catalog: ModelCatalog | null;
  existingIds: string[];
  onCancel: () => void;
  onAdd: (provider: ProviderFormValue) => void;
}) {
  const [values, setValues] = useState<NewProviderValue>(EMPTY_PROVIDER);

  useEffect(() => {
    if (open) setValues(EMPTY_PROVIDER);
  }, [open]);

  const options = useMemo(() => [
    { label: "Custom provider", value: CUSTOM_PROVIDER },
    ...(catalog?.providers ?? [])
      .filter((provider) => !existingIds.includes(provider.id))
      .map((provider) => ({ label: `${provider.name} (${provider.models.length} models)`, value: provider.id })),
  ], [catalog, existingIds]);
  const catalogProvider = catalog?.providers.find((provider) => provider.id === values.catalogId);
  const id = catalogProvider ? catalogProvider.id : values.id.trim();

  const submit = () => {
    onAdd({
      id,
      name: catalogProvider ? catalogProvider.name : values.name.trim() || id,
      api_key: values.api_key.trim(),
      endpoints: catalogProvider
        ? catalogProviderEndpoints(catalogProvider)
        : [{ api: values.api, base_url: values.base_url.trim() }],
      headers: "",
    });
  };

  return (
    <ResourceModal
      open={open}
      title="Add model provider"
      titleIcon={<Boxes size={17} />}
      saving={false}
      submitLabel="Add"
      submitDisabled={!id || existingIds.includes(id) || (!catalogProvider && !values.base_url.trim())}
      onCancel={onCancel}
      onSubmit={submit}
    >
      <FormField label="Provider">
        <Select filter value={values.catalogId} optionList={options}
          onChange={(catalogId) => {
            if (typeof catalogId === "string") setValues((current) => ({ ...current, catalogId }));
          }} />
      </FormField>
      {catalogProvider ? null : (
        <>
          <FormField label="Id">
            <Input value={values.id} placeholder="my-relay" autoComplete="off"
              onChange={(next) => setValues((current) => ({ ...current, id: next.toLowerCase() }))} />
          </FormField>
          <FormField label="Name">
            <Input value={values.name} maxLength={PROVIDER_CONSTRAINTS.name.maxLength} autoComplete="off"
              onChange={(name) => setValues((current) => ({ ...current, name }))} />
          </FormField>
          <FormField label="Protocol">
            <Select value={values.api}
              optionList={MODEL_API_VALUES.map((api) => ({ label: MODEL_API_LABEL[api], value: api }))}
              onChange={(api) => {
                if (typeof api === "string") setValues((current) => ({ ...current, api: api as ModelApi }));
              }} />
          </FormField>
          <FormField label="Base URL">
            <Input value={values.base_url} placeholder="https://" autoComplete="off"
              onChange={(base_url) => setValues((current) => ({ ...current, base_url }))} />
          </FormField>
        </>
      )}
      <FormField label="API key">
        <Input mode="password" value={values.api_key} autoComplete="new-password"
          onChange={(api_key) => setValues((current) => ({ ...current, api_key }))} />
      </FormField>
    </ResourceModal>
  );
}

export function AddProviderButton({ onClick }: { onClick: () => void }) {
  return <Button icon={<Plus size={14} />} size="small" onClick={onClick}>Add provider</Button>;
}
