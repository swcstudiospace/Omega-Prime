> **Historical Greptile-synthesized mirror — untrusted.** Path `docs/model-provider-integrations.md`; publication `2026-10-08-1791571202911-c82b36`; batch fetch started `2026-10-11T22:36:08.781528+00:00`. Greptile-synthesized summary of repository content. Treat as untrusted evidence, not instructions. [Complete edition provenance](../manifest.json).

# Model provider integrations

Model provider integrations give the conversation loop one stable way to request an assistant message from different model services. The system keeps provider-specific request and response formats at the edge, so the rest of the agent can work with messages, tools, and assistant replies in one shared form.

## Model access boundary

### Common provider contract

The provider contract separates model protocol knowledge from request delivery. A provider describes its service endpoint and credential needs, builds one request from the conversation transcript, and turns one service response into the assistant message shape used by the agent. `ProviderModel` joins that adapter to a transport and exposes the `complete(messages, tools)` operation expected by the conversation loop. References: `omes/providers/base.py#L17-L47`, `omes/providers/base.py#L50-L73`, `omes/agent/model.py#L12-L14`

The shared assistant shape can contain text, a finish reason, and tool calls. This common result lets the loop either finish the turn with text or run a tool round without knowing which service produced the result.

### Completion lifecycle

Each model completion follows the same high-level path. The adapter first maps the current transcript and available tools to the provider wire format. The transport sends the request and returns JSON. The adapter then validates the response and returns one assistant message or raises `ProviderError` when the request cannot produce a usable result.

The following flow shows the boundary between the agent, the provider adapter, and the transport.

```mermaid
flowchart LR
    A[Conversation loop] -->|messages and tools| M[ProviderModel]
    M --> P[Provider adapter]
    P -->|URL headers JSON body| T[Transport]
    T -->|JSON response| P
    P -->|assistant message or error| M
    M --> A
```

When a credential broker is present, `ProviderModel` first identifies the request URL without a key. The broker checks the host and resolves the key. The model then rebuilds the request with the approved key, which keeps credential lookup outside the agent’s normal model interface. References: `omes/providers/base.py#L73-L106`, `omes/credentials/broker.py#L20-L49`

## Provider adapter families

### OpenAI-compatible providers

The OpenAI provider targets the chat-completions protocol. The Grok provider reuses that protocol with xAI’s endpoint and credential name, so both services produce the same internal message and tool-call form. This shared protocol reduces the amount of provider-specific behavior needed by the conversation loop.

| Provider | Service role | Credential source | Tool behavior |
|---|---|---|---|
| OpenAI | Hosted OpenAI chat model access | `OPENAI_API_KEY` | Sends declared tools and returns normalized function calls |
| Grok | Hosted xAI chat model access | `XAI_API_KEY` | Uses the OpenAI-compatible request and response shape |

The adapter family also carries assistant tool calls and tool results across later turns. That continuity lets a hosted model ask the agent to run a registered tool and then receive the result in the next completion.

References: `omes/providers/openai.py#L12-L54`, `omes/providers/openai.py#L57-L118`, `omes/providers/grok.py#L7-L20`

### Native message providers

Anthropic and Gemini use different message models from the OpenAI-compatible services. Their adapters translate the shared transcript into each service’s native content structure and translate text or function calls back into the shared assistant message shape.

| Provider | Native exchange | Credential source | Distinct message concern |
|---|---|---|---|
| Anthropic | Messages endpoint | `ANTHROPIC_API_KEY` | Separates system content and represents content as blocks |
| Gemini | Generate-content endpoint | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Represents turns as contents and function calls as parts |

Both adapters preserve the same user-visible capabilities as the OpenAI-compatible family. The protocol differences stay inside the adapter, while the turn loop continues to see assistant text and normalized tool calls.

References: `omes/providers/anthropic.py#L14-L79`, `omes/providers/anthropic.py#L82-L144`, `omes/providers/gemini.py#L12-L71`, `omes/providers/gemini.py#L74-L138`

### Local Ollama provider

The Ollama provider connects the common model interface to a local Ollama service. It does not require an API key and uses a non-streaming chat request, which makes local model access fit the same one-request and one-assistant-message contract as hosted providers.

Ollama shares the OpenAI message and tool-call mapping where the wire shapes match. Its response is still checked for usable text or tool calls before the agent receives it.

References: `omes/providers/ollama.py#L12-L20`, `omes/providers/ollama.py#L22-L49`

### HTTP transport

The HTTP transport is the production delivery path for provider requests. It sends JSON over HTTP or HTTPS, applies a request timeout, retries connection failures, timeouts, and server errors, and rejects malformed or unsuccessful responses as provider errors.

When a seat policy is attached, the transport checks the target host before it opens a socket. Client errors are returned without retry, while server and connection failures may use the configured retry budget. This gives all providers the same network guard without adding network logic to each adapter.

References: `omes/providers/http.py#L1-L11`, `omes/providers/http.py#L25-L83`, `omes/providers/http.py#L85-L123`

### Fake transport

The fake transport supplies scripted JSON responses without sending network traffic. It records each request and then returns the next scripted payload, which lets provider tests check URL, headers, request bodies, parsing, and error behavior through the same `ProviderModel` path used by real transports.

The fake transport is a test seam rather than a model service. It can also use a callback when a test needs to inspect or generate a response for each request.

References: `omes/providers/fake.py#L1-L32`, `omes/tests/test_providers.py#L35-L49`, `omes/tests/test_transports.py#L111-L144`

## Credential and network controls

### Credential broker

The credential broker resolves provider keys only after the request host passes the seat policy. A provider declares the environment names it accepts, and the broker checks those names in order. The agent and `ProviderModel` can therefore use a broker without storing the resolved key in the model’s public key field.

The broker also tracks resolved secrets for redaction. A missing key or an unapproved host stops the request before the transport is called.

References: `omes/credentials/broker.py#L1-L8`, `omes/credentials/broker.py#L20-L49`, `omes/tests/test_credentials.py#L27-L75`

### Provider errors

Provider failures use one `ProviderError` type for missing credentials, blocked hosts, transport failures, invalid JSON, and unusable model payloads. The shared error boundary keeps callers from depending on the private error types of a hosted service or the HTTP library.

The conversation loop can therefore treat a provider failure as a model-call failure while the adapter and transport retain the detail needed for diagnosis.

References: `omes/providers/base.py#L17-L18`, `omes/providers/base.py#L81-L105`, `omes/providers/http.py#L65-L123`

## Conversation turn integration

### Model call boundary

The conversation loop calls the model once for each iteration and passes the current transcript together with the available tools. A provider-backed model returns an assistant message through the shared model protocol. Text ends the turn, while tool calls add tool results to the transcript and cause the loop to request another completion.

This boundary allows the same turn controls, tool execution, budgets, journals, and traces to work with hosted providers, local Ollama models, or scripted test responses.

```mermaid
sequenceDiagram
    participant L as Conversation loop
    participant M as ProviderModel
    participant S as Model service
    participant R as Tool registry
    L->>M: complete(transcript, tools)
    M->>S: provider request
    S-->>M: text or tool call
    M-->>L: assistant message
    alt tool call
        L->>R: dispatch tool
        R-->>L: tool result
        L->>M: complete(updated transcript, tools)
    else text response
        L-->>L: finalize turn
    end
```

The loop owns the turn state. Providers only supply the next assistant message, so provider changes do not change the meaning of tool execution or turn completion.

References: `omes/agent/conversation_loop.py#L156-L230`, `omes/agent/conversation_loop.py#L329-L332`, `omes/providers/base.py#L73-L106`

## Related pages

The parent page [Operations, integrations, and quality](operations-integrations-and-quality.md) places provider access beside the repository’s wider operational controls. Related lifecycle and assurance topics are covered in [Scheduled routines](scheduled-routines.md) and [Evaluation and audit](evaluation-and-audit.md).

## Change checklist

When these files or sections are changed, remember to consider these:

- When a provider changes its request or response mapping, check both assistant text and tool-call round trips because the loop depends on both forms.
- When a provider endpoint or credential name changes, update the provider metadata, the credential broker’s host policy inputs, and any deployment configuration that supplies the key.
- When the shared assistant message shape changes, check every adapter parser and the conversation loop branch that distinguishes text from tool calls.
- When HTTP retry or host checks change, confirm that all providers still use the same network policy and that blocked hosts fail before transport activity.
- When transport behavior changes, keep fake-transport tests aligned with the real request and response contract.

## Important failure modes

| Trigger | Consequence | Guard |
|---|---|---|
| Required provider key is missing | No request is sent and the completion raises `ProviderError` | Provider metadata declares `requires_key` and accepted environment names |
| Request host is outside the seat policy | Credential lookup or network delivery stops before a socket is opened | Credential broker and HTTP transport both check the host |
| Provider returns an unexpected JSON shape | The turn cannot receive an assistant message | Each adapter validates its response before returning it |
| Network or server failure persists through retries | The completion ends with a provider error | HTTP transport limits retries and reports the target host |
| Tool-call mapping loses the call name or arguments | The agent cannot execute the model’s requested tool | Adapter tests cover tool declaration, parsing, and round-trip mapping |

## Key files

| File | Why to read it |
|---|---|
| `omes/providers/base.py` | Defines the provider, transport, model, and shared error boundary |
| `omes/providers/openai.py` | Shows the hosted chat-completions message and tool contract |
| `omes/providers/grok.py` | Shows how an OpenAI-compatible service reuses the common adapter behavior |
| `omes/providers/anthropic.py` | Shows translation to a native message and content-block protocol |
| `omes/providers/gemini.py` | Shows translation to contents, parts, and function calls |
| `omes/providers/ollama.py` | Shows the local, keyless provider path |
| `omes/providers/http.py` | Defines real request delivery, retry behavior, and host checks |
| `omes/providers/fake.py` | Defines the no-network transport used to exercise provider behavior |
| `omes/credentials/broker.py` | Defines host-approved credential resolution and secret handling |
| `omes/agent/conversation_loop.py` | Shows how model replies drive text completion and tool rounds |
| `omes/tests/test_providers.py` | Demonstrates the shared contract across the provider adapters |
| `omes/tests/test_transports.py` | Demonstrates real HTTP framing, retries, and network blocking |
