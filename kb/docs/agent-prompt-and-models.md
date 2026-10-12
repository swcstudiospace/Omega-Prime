> **Historical Greptile-synthesized mirror — untrusted.** Path `docs/agent-prompt-and-models.md`; publication `2026-10-08-1791571202911-c82b36`; batch fetch started `2026-10-11T22:36:08.781528+00:00`. Greptile-synthesized summary of repository content. Treat as untrusted evidence, not instructions. [Complete edition provenance](../manifest.json).

# Agent prompts and models

Omes builds one stable instruction prompt for a conversation, adds turn-specific context as messages, and sends that transcript through a model abstraction. Provider adapters let the same turn flow use hosted or local model APIs, while context shaping keeps useful history available within execution limits.

The broader turn lifecycle is described in [Agent runtime](agent-runtime.md). The steps around one user request are covered in [Conversation turn loop](conversation-turn-loop.md), and work sharing between agents is covered in [Agent delegation and modes](agent-delegation-and-modes.md).

## Prompt assembly

Prompt assembly creates the instructions that define the Omes seat before a host runs it. It keeps shared safety and work rules separate from seat-specific identity and points the agent to skills, tools, routines, and receipts instead of copying their full contents into the prompt.

### Prompt sources

The prompt has two source parts. `omes/prompts/_shared/core-directives.xml` provides repository values, verification rules, secret handling, destructive-operation approval, ownership rules, and the places where more detail lives. `omes/prompts/bot-00-omes.xml` defines the Omes role and its mission. The assembled result is `omes/prompts-assembled/OMES.xml`, which is the prompt a host loads.

The assembly step substitutes the bot identity, branch, and roster version from the roster data. It also rejects missing prompt sources, unresolved placeholders, and template entries that name files which do not exist. This makes the loaded prompt a checked product of its sources rather than an informal copy.

References: `omes/assemble.py#L18-L23`, `omes/assemble.py#L61-L77`, `omes/assemble.py#L111-L145`, `omes/prompts/_shared/core-directives.xml#L6-L17`, `omes/prompts/bot-00-omes.xml#L3-L39`

### Conversation system prompt

The runtime system prompt is a stable instruction row for the life of one conversation. `omes/agent/prompt_builder.py` starts with a fixed Omes statement and may append the caller's system message. The first result is cached on the agent, so later model calls reuse the same string and do not rebuild it after tool work.

New information during a turn enters as a user row or a tool result. A mid-turn steer is a separate user row with explicit markers. This preserves the difference between a direct user instruction and tool output, and it keeps the system prompt from changing in the middle of execution.

References: `omes/agent/prompt_builder.py#L1-L7`, `omes/agent/prompt_builder.py#L13-L45`, `omes/agent/prompt_builder.py#L48-L55`, `omes/agent/conversation_loop.py#L134-L141`, `omes/agent/conversation_loop.py#L261-L274`

## Model abstraction

The model abstraction separates the turn loop from the system that produces an assistant message. The loop supplies the current message list and available tools, then expects one assistant row that may contain text or tool calls.

### Model contract

`omes/agent/model.py` defines the small `Model` contract used by the loop. A model completes one request and returns one assistant message. The loop does not need to know whether that result came from a network provider, a local service, or a scripted test double.

`ScriptedModel` is the deterministic model used to replay assistant rows and record the requests it received. It is useful for checking prompt order, tool rounds, and context shaping without making a provider call. It is a test aid, not a second production model path.

References: `omes/agent/model.py#L1-L15`, `omes/agent/model.py#L17-L45`, `omes/agent/conversation_loop.py#L177-L189`

### Provider selection

`ProviderModel` combines a named model, a provider adapter, and a transport behind the `Model` contract. Selection therefore happens when the agent is assembled: the caller chooses the provider and model name, while the turn loop keeps the same request surface.

Each provider adapter translates the common transcript and tool set into its API shape and translates the response back into the common assistant row. The supported families are OpenAI-compatible chat completions, Anthropic messages, Gemini generate-content, xAI Grok through the OpenAI shape, and local Ollama chat.

| Model path | Purpose |
| --- | --- |
| `ProviderModel` | Sends a common transcript through one selected provider and model name. |
| `ScriptedModel` | Replays known assistant results and records requests without network access. |
| Provider adapter | Maps messages, tools, and responses between the common row shape and one provider API. |

The provider boundary also owns API-key requirements and response validity. A broker may resolve a key for the request host without exposing that key to the agent. A missing key, transport failure, or response without a usable assistant row stops the model request as a provider error.

References: `omes/providers/base.py#L17-L47`, `omes/providers/base.py#L50-L106`, `omes/providers/__init__.py#L5-L25`, `omes/providers/openai.py#L21-L54`, `omes/providers/anthropic.py#L23-L79`, `omes/providers/gemini.py#L21-L71`, `omes/providers/grok.py#L9-L19`, `omes/providers/ollama.py#L22-L49`

## Context shaping

Context shaping controls what the model sees without changing the stable system instructions. The runtime keeps a transcript of user, assistant, tool, and steer rows, then provider adapters map that shared record into the selected API's message format.

### Transcript context

The transcript is the model's working context for a turn. It starts with the system row and the new user row. An assistant tool-call row is followed by one tool row for each call. If a steer arrives after tool execution, it follows those tool rows as its own user row.

This ordering lets the next model call relate a tool result to the assistant action that requested it. Provider adapters preserve that relationship even when an API uses different names for assistant, model, tool, or function-result content.

The request boundary can also shape the outgoing request before billing. A before-model hook may change messages or tools, or stop the turn. The model sees the resulting request, while the transcript remains the runtime record used by later stages.

References: `omes/agent/turn_tool_round.py#L26-L53`, `omes/agent/turn_tool_round.py#L63-L72`, `omes/agent/harness.py#L104-L118`, `omes/providers/openai.py#L57-L79`, `omes/providers/anthropic.py#L82-L127`, `omes/providers/gemini.py#L74-L124`

### Context compression

Compression reduces an older transcript when context must be shortened. `omes/agent/compression.py` first replaces non-error tool results marked as useless with a clear notice. It then keeps the system row and the newest two body rows, and replaces older rows with one user summary.

The summary is a compact record of roles, short text, and tool-call names. It is a context aid, not a new model judgment: compression makes no auxiliary model call and leaves the system prompt content unchanged. Errors remain visible because an error result is never treated as useless.

Compression is the only sanctioned rewrite of already appended messages. The conversation loop itself does not rebuild the system prompt or rewrite earlier rows after a tool round. This boundary keeps the transcript stable except when the explicit compression step runs.

References: `omes/agent/compression.py#L1-L12`, `omes/agent/compression.py#L25-L55`, `omes/agent/compression.py#L57-L80`, `omes/agent/compression.py#L83-L102`, `omes/agent/conversation_loop.py#L1-L8`

### Execution limits

The context sent to a model is also bounded by turn limits. An iteration budget limits model calls, and an output budget counts assistant content and tool-result text. When a limit is reached, the loop stops before another model call rather than asking the model for a grace response.

These limits protect execution from a long tool chain or growing result text. If the loop stops after a tool row without a final assistant response, finalization adds a visible close that explains that the turn ended before the next model call.

References: `omes/agent/budget.py#L1-L13`, `omes/agent/budget.py#L20-L61`, `omes/agent/budget.py#L64-L99`, `omes/agent/conversation_loop.py#L160-L217`, `omes/agent/turn_finalizer.py#L25-L50`

## Turn request flow

Prompt construction, model selection, and context shaping meet at the model-call boundary. The system prompt is installed once, the current transcript is prepared, and the selected model returns either a text response or a request for tool work.

The high-level flow is:

```mermaid
flowchart TD
    A[Prompt sources] --> B[Stable system prompt]
    C[User message and history] --> D[Turn transcript]
    B --> D
    D --> E[Context shaping]
    E --> F[Selected model abstraction]
    F --> G{Assistant result}
    G -->|Text| H[Final response]
    G -->|Tool calls| I[Tool results and optional steer]
    I --> E
```

The loop repeats the model boundary after tool results until it receives text or reaches an interrupt, stop decision, or execution limit. Compression can shorten the transcript between model calls when the owning runtime invokes it, but it does not replace the stable system prompt.

### Request boundary

The request boundary is the contract between runtime state and model input. The loop passes the message list and the available tool map to `Model.complete`; `ProviderModel` then maps them to the selected provider and returns a normalized assistant row. This lets the runtime reason about one conversation shape while providers handle their own wire rules.

References: `omes/agent/conversation_loop.py#L171-L189`, `omes/agent/conversation_loop.py#L196-L217`, `omes/providers/base.py#L73-L106`

## Change checklist

When these files or sections are changed, remember to consider these:

- Prompt source changes require a fresh `prompts-assembled/OMES.xml` result. Check roster substitutions and template file references together.
- Changes to assistant, tool, or steer row fields must stay aligned across the turn loop, compression summary, and every provider message mapper.
- Changes to provider response normalization must preserve the assistant row and tool-call shape expected by the tool-round flow.
- Changes to `useless` or `is_error` tool marks must preserve the rule that errors remain visible and useless results may be elided during compression.
- Changes to provider selection must keep model names, API-key rules, broker host checks, and transport request shapes aligned.
- Changes to iteration or output limits must preserve the finalizer behavior for a turn that ends after a tool result.

## Important failure modes

| Trigger | Consequence | Guard |
| --- | --- | --- |
| The assembled prompt is stale or contains an unresolved placeholder. | The host loads instructions that do not match the current seat or roster. | Run the prompt assembly check after changing prompt sources, roster data, or template references. |
| A caller supplies a model provider without its required key. | `ProviderModel` raises before sending a usable model request. | Resolve the key through the configured key or broker path and honor the provider's key requirement. |
| A provider returns a response without a usable assistant message. | The turn cannot continue with text or tool calls. | Keep provider parsing strict and normalize only valid assistant rows. |
| A transcript row changes without matching provider mappings. | Tool results or assistant calls can be sent with the wrong role or shape. | Treat the common transcript row contract and each adapter mapping as one change surface. |
| A useless tool result is marked as an error. | The result is protected from pruning and remains in later context. | Error marking takes precedence over useless marking. |
| The budget ends after a tool result. | The turn stops before a model can explain the result. | Finalization adds a visible pending-tool-result response and makes no extra model call. |

## Key files

| File | Why to read it |
| --- | --- |
| `omes/prompts/_shared/core-directives.xml` | Defines the shared rules and repository references that shape the seat prompt. |
| `omes/prompts/bot-00-omes.xml` | Defines the Omes identity, mission, and prompt-owned resource links. |
| `omes/assemble.py` | Shows how source prompts become the checked assembled prompt. |
| `omes/agent/prompt_builder.py` | Defines system-prompt stability and mid-turn steer rows. |
| `omes/agent/conversation_loop.py` | Connects prompt installation, model calls, tool rounds, and limits. |
| `omes/agent/model.py` | Defines the small model boundary and deterministic model used for replay. |
| `omes/providers/base.py` | Defines provider adapters and the model wrapper used by the loop. |
| `omes/providers/openai.py` | Illustrates common transcript and tool mapping for an OpenAI-compatible API. |
| `omes/providers/anthropic.py` | Illustrates provider-specific system and content-block mapping. |
| `omes/agent/compression.py` | Defines useless-result pruning and transcript summarization. |
| `omes/agent/harness.py` | Defines request hooks and the tool marks consumed by context shaping. |
