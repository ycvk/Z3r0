# ADR-0008 Models are configured as providers, protocols, and explicit request parameters

Status: `accepted`
Date: `2026-11-20`
Owners: `config.py` (structure and validation) · `core/agent/models.py` (model construction) · `core/model_catalog` (catalog) · `service/system_config/config.py` (updates)

## Decision

### Configuration

`config.json` describes models in three places:

- `providers` maps a provider id to one API key, optional request headers, and
  `endpoints`: the base URL of each wire protocol the provider serves.
- `agents.*.model` selects a provider, a protocol, and a model id, and carries
  the request parameters sent with every call: `context_window`, `max_tokens`,
  `temperature`, `top_p`, `reasoning_effort`, `thinking_budget_tokens`,
  `extra_headers`, and `extra_body`.
- `lightrag.llm` selects a provider, a protocol, and a model id.

Every value a model call uses is stored in `config.json`. The catalog fills in
a form; it is never read when a model is built or called.

`ModelConfig` rejects parameters its protocol does not take.
`validate_model_refs` rejects a model whose provider is missing or has no
endpoint for the model's protocol. It runs when `config.json` is read and
before an update is written. It is a function over a whole config because the
shared config object is assigned one field at a time.

### Protocols

`ModelApi` names four wire protocols, one implementation each:

| Protocol | Implementation |
| --- | --- |
| `openai-completions`, `openai-responses` | The Agents SDK `OpenAIProvider` over `AsyncOpenAI`. |
| `anthropic-messages` | `core/agent/anthropic_messages.py` over the `anthropic` SDK. |
| `google-generative-ai` | `core/agent/google_generative_ai.py` over the `google-genai` SDK. |

The Anthropic and Google models translate the SDK's Responses input into their
protocol and feed their stream to `ResponseStreamBuilder`, which emits the
Responses events the runner consumes. Every output item carries
`provider_data` with the protocol and model that produced it:

- A reasoning item is sent back only to the protocol and model that signed it.
  Anthropic thinking blocks are replayed while thinking is on; Gemini thought
  signatures are replayed on the parts they arrived with.
- The SDK's OpenAI models drop reasoning items another provider produced.

`build_model` wraps every protocol model in `Z3r0Model`, the provider
boundary. It normalizes tool-call ordering and lowers `max_tokens` on each call
to what the estimated input leaves of `context_window`, with a margin for the
provider counting more input tokens than the estimate.

LightRAG calls its extraction model over Chat Completions, so `lightrag.llm`
must use an `openai-completions` or `openai-responses` endpoint.

### Catalog

`core/model_catalog/snapshot.json` is built from the `@earendil-works/pi-ai`
npm package, the data behind <https://pi.dev/models>, by
`scripts/update_model_catalog.py`. It keeps chat models on the four protocols
from providers that authenticate with an API key, and for each model the
protocol, base URL, context window, output cap, thinking controls, and price.

`POST /api/model-catalog/refresh` downloads the latest release, checks the
tarball against the registry digest and each data file against the package
manifest, and stores the result as `.z3r0/model_catalog.json`. The catalog in
use is the newer of that file and the snapshot.

## Consequences

- Changing a provider's key or URL applies to every agent that uses it.
- Selecting a model on the system config page loads its catalog values into the
  form; the saved config keeps working when the catalog changes or is absent.
- A catalog refresh never changes how a configured model is called.
- `config.json` files written before this structure do not load.
- A request parameter specific to one provider, such as a non-standard thinking
  switch on a Chat Completions endpoint, goes in `extra_body`.
- Anthropic `redacted_thinking` blocks are not replayed.
- A fifth protocol needs a `ModelApi` member, a model class, and its parameter
  rules in `ModelConfig`.

## Evidence

2026-11-20, local: `tests/test_model_protocols.py` runs tool loops against
stub endpoints built from the provider SDKs' own event types and checks the
requests on the wire for all four protocols: signed thinking and thought
signatures replayed, cache breakpoints, thinking parameters, sampling
parameters, provider and model headers, the fitted output cap, and provider
errors. Anthropic requests validate against the `anthropic` SDK's request
type. `tests/test_agent_turn.py` runs a chat turn through the session runtime
on each protocol. `tests/test_model_config.py` covers parameter and reference
validation and the instance config API. `tests/test_model_catalog.py` covers
the catalog build, the snapshot, and the refresh.

The application image was started against an empty database; through its HTTP
API two providers were added, two agents were moved onto stub Anthropic and
Gemini endpoints, and a chat turn on each returned the stub's text and token
counts. The system config page was driven in Chrome: adding a catalog provider
and a custom relay, selecting models, overriding parameters, saving, and
updating the catalog from the registry.

No request was sent to a real Anthropic, Google, or OpenAI endpoint.
