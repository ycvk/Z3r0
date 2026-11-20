import { MODEL_API, MODEL_API_VALUES } from "../../shared/api/generated/constants";
import type {
  CatalogModel,
  CatalogProvider,
  ModelApi,
  ModelCatalog,
  ModelConfig,
  ModelRef,
  ProviderConfig,
  ReasoningEffort,
} from "../../shared/api/types";

export type ProviderEndpointValue = { api: ModelApi; base_url: string };

export type ProviderFormValue = {
  id: string;
  name: string;
  api_key: string;
  endpoints: ProviderEndpointValue[];
  headers: string;
};

export type ModelFormValue = {
  provider: string;
  api: ModelApi;
  id: string;
  context_window: number;
  max_tokens: number | null;
  temperature: number | null;
  top_p: number | null;
  reasoning_effort: ReasoningEffort | null;
  thinking_budget_tokens: number | null;
  extra_headers: string;
  extra_body: string;
};

export type CatalogChoice = { provider: CatalogProvider; model: CatalogModel };

export const MODEL_API_LABEL: Record<ModelApi, string> = {
  [MODEL_API.OPENAI_COMPLETIONS]: "OpenAI Chat Completions",
  [MODEL_API.OPENAI_RESPONSES]: "OpenAI Responses",
  [MODEL_API.ANTHROPIC_MESSAGES]: "Anthropic Messages",
  [MODEL_API.GOOGLE_GENERATIVE_AI]: "Google Generative AI",
};

const OPENAI_APIS: readonly ModelApi[] = [MODEL_API.OPENAI_COMPLETIONS, MODEL_API.OPENAI_RESPONSES];

const REASONING_EFFORTS: Record<ModelApi, readonly ReasoningEffort[]> = {
  [MODEL_API.OPENAI_COMPLETIONS]: ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
  [MODEL_API.OPENAI_RESPONSES]: ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
  [MODEL_API.ANTHROPIC_MESSAGES]: ["none", "low", "medium", "high", "xhigh", "max"],
  [MODEL_API.GOOGLE_GENERATIVE_AI]: ["none", "minimal", "low", "medium", "high"],
};

const PROVIDER_ID_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const BASE_URL_PATTERN = /^https?:\/\/[^\s{}]+$/;
const ANTHROPIC_MIN_THINKING_BUDGET = 1024;

export function isOpenAIApi(api: ModelApi): boolean {
  return OPENAI_APIS.includes(api);
}

export function takesThinkingBudget(api: ModelApi): boolean {
  return !isOpenAIApi(api);
}

function formatJson(value: Record<string, unknown> | undefined): string {
  return value && Object.keys(value).length > 0 ? JSON.stringify(value, null, 2) : "";
}

export function providerToForm(id: string, provider: ProviderConfig): ProviderFormValue {
  return {
    id,
    name: provider.name,
    api_key: provider.api_key ?? "",
    endpoints: MODEL_API_VALUES
      .filter((api) => api in provider.endpoints)
      .map((api) => ({ api, base_url: provider.endpoints[api] })),
    headers: formatJson(provider.headers),
  };
}

export function modelToForm(model: ModelConfig): ModelFormValue {
  return {
    provider: model.provider,
    api: model.api,
    id: model.id,
    context_window: model.context_window,
    max_tokens: model.max_tokens ?? null,
    temperature: model.temperature ?? null,
    top_p: model.top_p ?? null,
    reasoning_effort: model.reasoning_effort ?? null,
    thinking_budget_tokens: model.thinking_budget_tokens ?? null,
    extra_headers: formatJson(model.extra_headers),
    extra_body: formatJson(model.extra_body),
  };
}

function parseJsonObject(text: string, label: string): Record<string, unknown> {
  if (!text.trim()) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

function parseHeaders(text: string, label: string): Record<string, string> {
  const headers = parseJsonObject(text, label);
  for (const [name, value] of Object.entries(headers)) {
    if (typeof value !== "string") throw new Error(`${label}: "${name}" must be a string`);
  }
  return headers as Record<string, string>;
}

/** Build the provider payload; throws an Error describing the first invalid field. */
export function providerToPayload(provider: ProviderFormValue): ProviderConfig {
  const label = provider.name.trim() || provider.id;
  if (!PROVIDER_ID_PATTERN.test(provider.id)) {
    throw new Error(`Provider id "${provider.id}" must be lowercase letters, digits, ".", "_" or "-"`);
  }
  if (!provider.name.trim()) throw new Error(`Provider ${provider.id} needs a name`);
  if (provider.endpoints.length === 0) throw new Error(`Provider ${label} needs at least one endpoint`);
  const endpoints: Record<string, string> = {};
  for (const endpoint of provider.endpoints) {
    const baseUrl = endpoint.base_url.trim().replace(/\/+$/, "");
    if (!BASE_URL_PATTERN.test(baseUrl)) {
      throw new Error(
        `Provider ${label}: the ${MODEL_API_LABEL[endpoint.api]} endpoint must be an http(s) URL without placeholders`,
      );
    }
    endpoints[endpoint.api] = baseUrl;
  }
  return {
    name: provider.name.trim(),
    api_key: provider.api_key.trim(),
    endpoints,
    headers: parseHeaders(provider.headers, `Provider ${label} headers`),
  };
}

function checkModelRef(model: ModelRef, owner: string, providers: ProviderFormValue[]): void {
  const provider = providers.find((candidate) => candidate.id === model.provider);
  if (!provider) throw new Error(`${owner}: choose a provider`);
  if (!model.id.trim()) throw new Error(`${owner}: choose a model`);
  if (!provider.endpoints.some((endpoint) => endpoint.api === model.api)) {
    throw new Error(`${owner}: provider ${provider.name || provider.id} has no ${MODEL_API_LABEL[model.api]} endpoint`);
  }
}

/** Build the model payload; throws an Error describing the first invalid field. */
export function modelToPayload(model: ModelFormValue, owner: string, providers: ProviderFormValue[]): ModelConfig {
  checkModelRef(model, owner, providers);
  const budget = model.thinking_budget_tokens;
  if (model.reasoning_effort !== null && !REASONING_EFFORTS[model.api].includes(model.reasoning_effort)) {
    throw new Error(`${owner}: ${MODEL_API_LABEL[model.api]} does not accept reasoning effort "${model.reasoning_effort}"`);
  }
  if (budget !== null && !takesThinkingBudget(model.api)) {
    throw new Error(`${owner}: ${MODEL_API_LABEL[model.api]} does not accept a thinking token budget`);
  }
  if (budget !== null && model.reasoning_effort !== null) {
    throw new Error(`${owner}: set either a reasoning effort or a thinking token budget`);
  }
  if (model.api === MODEL_API.ANTHROPIC_MESSAGES) {
    if (model.max_tokens === null) throw new Error(`${owner}: Anthropic Messages requires max output tokens`);
    if (budget !== null && (budget < ANTHROPIC_MIN_THINKING_BUDGET || budget >= model.max_tokens)) {
      throw new Error(
        `${owner}: the thinking budget must be at least ${ANTHROPIC_MIN_THINKING_BUDGET} and below max output tokens`,
      );
    }
  }
  return {
    provider: model.provider,
    api: model.api,
    id: model.id.trim(),
    context_window: model.context_window,
    max_tokens: model.max_tokens,
    temperature: model.temperature,
    top_p: model.top_p,
    reasoning_effort: model.reasoning_effort,
    thinking_budget_tokens: budget,
    extra_headers: parseHeaders(model.extra_headers, `${owner} extra headers`),
    extra_body: parseJsonObject(model.extra_body, `${owner} extra body`),
  };
}

/** Build the LightRAG extraction model payload; throws an Error describing the first invalid field. */
export function extractionModelToPayload(model: ModelRef, providers: ProviderFormValue[]): ModelRef {
  const owner = "LightRAG extraction LLM";
  checkModelRef(model, owner, providers);
  if (!isOpenAIApi(model.api)) throw new Error(`${owner} must use an OpenAI protocol endpoint`);
  return { provider: model.provider, api: model.api, id: model.id.trim() };
}

/** The endpoints a catalog provider serves, one per protocol. */
export function catalogProviderEndpoints(provider: CatalogProvider): ProviderEndpointValue[] {
  return MODEL_API_VALUES.flatMap((api) => {
    const model = provider.models.find((candidate) => candidate.api === api);
    return model ? [{ api, base_url: model.base_url }] : [];
  });
}

/**
 * Catalog models a configured provider can serve. A provider that is itself in the catalog offers
 * its own models. Any other provider, such as a relay, offers every catalog model on a protocol it
 * has an endpoint for, once each: gateways re-list other vendors' models under the same id, so the
 * entry kept is the one from the provider serving the fewest protocols, which is the vendor itself.
 */
export function catalogChoices(
  catalog: ModelCatalog | null,
  provider: ProviderFormValue | undefined,
  apis?: readonly ModelApi[],
): CatalogChoice[] {
  if (!catalog || !provider) return [];
  const allowed = (api: ModelApi) => !apis || apis.includes(api);
  const own = catalog.providers.find((candidate) => candidate.id === provider.id);
  if (own) {
    return own.models.filter((model) => allowed(model.api)).map((model) => ({ provider: own, model }));
  }
  const served = new Set(provider.endpoints.map((endpoint) => endpoint.api));
  const protocolCount = (candidate: CatalogProvider) => new Set(candidate.models.map((model) => model.api)).size;
  const choices = new Map<string, CatalogChoice>();
  [...catalog.providers]
    .sort((left, right) => protocolCount(left) - protocolCount(right))
    .forEach((candidate) => candidate.models.forEach((model) => {
      const key = `${model.api}\u0000${model.id}`;
      if (served.has(model.api) && allowed(model.api) && !choices.has(key)) {
        choices.set(key, { provider: candidate, model });
      }
    }));
  return [...choices.values()].sort((left, right) => left.model.id.localeCompare(right.model.id));
}

/** Identifies the list catalogChoices returns for the same arguments. */
export function catalogChoicesKey(catalog: ModelCatalog | null, provider: ProviderFormValue | undefined): string {
  const apis = provider?.endpoints.map((endpoint) => endpoint.api).join(",") ?? "";
  return `${catalog?.source.version ?? ""}|${provider?.id ?? ""}|${apis}`;
}

/** The catalog entry behind a model selection, if the selection came from the catalog. */
export function findCatalogModel(
  choices: CatalogChoice[],
  model: Pick<ModelFormValue, "api" | "id">,
): CatalogModel | undefined {
  return choices.find((choice) => choice.model.id === model.id && choice.model.api === model.api)?.model;
}

/** Load a catalog model's defaults into a model form, keeping the provider selection. */
export function applyCatalogModel(model: ModelFormValue, catalogModel: CatalogModel): ModelFormValue {
  return {
    ...model,
    api: catalogModel.api,
    id: catalogModel.id,
    context_window: catalogModel.context_window,
    max_tokens: catalogModel.max_tokens,
    temperature: null,
    top_p: null,
    reasoning_effort: null,
    thinking_budget_tokens: null,
    extra_headers: formatJson(catalogModel.headers),
    extra_body: "",
  };
}

export function reasoningEffortOptions(api: ModelApi, catalogModel: CatalogModel | undefined): readonly ReasoningEffort[] {
  if (!catalogModel) return REASONING_EFFORTS[api];
  return catalogModel.reasoning_efforts.filter((effort) => REASONING_EFFORTS[api].includes(effort));
}

export function formatTokens(value: number): string {
  return value.toLocaleString("en-US");
}
