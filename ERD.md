# A2A Protocol - Detailed Data Dictionary & Schema Definitions

This document contains a micro-detailed, spreadsheet-ready breakdown of all entities and fields in the `fastapi-a2a` protocol schema. It is designed to be easily copied and pasted directly into a Google Sheet or Excel workbook for professional documentation and demo purposes.

## 1. Agent Identity & Configuration Entities

### `AgentProvider`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `organization` | `str` | `string` | **Required** | None | The name of the organization providing the agent. |
| `url` | `str` | `string` | Optional | Valid URL | URL to the organization's website or portal. |

### `AgentCapabilities`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `streaming` | `bool` | `boolean` | Optional | None | Indicates if the agent supports streaming content parts. |
| `pushNotifications` | `bool` | `boolean` | Optional | None | Indicates if the agent supports server-to-server task event pushes. |
| `stateTransitionHistory` | `bool` | `boolean` | Optional | None | Indicates if the agent retains full state transition logs. |

### `AgentSkill`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `id` | `str` | `string` | **Required** | Unique string | Unique identifier for the skill. |
| `name` | `str` | `string` | **Required** | None | Human-readable name of the skill. |
| `description` | `str` | `string` | **Required** | None | Detailed description of what the skill does and its limitations. |
| `inputModes` | `list[str]` | `array` | **Required** | Array of MIME types | List of MIME types supported for inputs (e.g., `["application/json"]`). |
| `outputModes` | `list[str]` | `array` | **Required** | Array of MIME types | List of MIME types supported for outputs. |
| `tags` | `list[str]` | `array` | Optional | None | List of tags used for discoverability and cataloging. |
| `examples` | `list[str]` | `array` | Optional | None | Example prompts or usage descriptions for the skill. |
| `endpoint` | `str` | N/A | Optional | Valid path | **Internal routing only.** Stripped prior to wire serialization to protect application architecture. |

### `SecurityScheme` (**FIX E1**: Fully strictly typed)
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `type` | `Literal` | `string` | **Required** | `"apiKey"`, `"http"`, `"oauth2"`, `"openIdConnect"` | The overarching category of the security scheme. |
| `description` | `str` | `string` | Optional | None | Human-readable description of how to authenticate. |
| `name` | `str` | `string` | Optional | For `apiKey` | The name of the header, query, or cookie parameter. |
| `in_` | `Literal` | `string` | Optional | For `apiKey`: `"query"`, `"header"`, `"cookie"` | Wire location of the API key. |
| `scheme` | `str` | `string` | Optional | For `http` (e.g., `"bearer"`) | HTTP Authorization scheme string. |
| `bearerFormat` | `str` | `string` | Optional | For `http` | Format hint (e.g., `"JWT"`). |
| `flows` | `OAuthFlows` | `object` | Optional | For `oauth2` | Detailed OAuth2 flow configurations. |
| `openIdConnectUrl` | `str` | `string` | Optional | Valid URL | Discovery URL for OpenID Connect configurations. |

### `OAuthFlows` (**FIX E1**: Fully strictly typed)
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `implicit` | `dict` | `object` | Optional | Must contain `authorizationUrl`, `scopes` | Implicit OAuth flow configuration. |
| `password` | `dict` | `object` | Optional | Must contain `tokenUrl`, `scopes` | Resource Owner Password flow. |
| `clientCredentials`| `dict` | `object` | Optional | Must contain `tokenUrl`, `scopes` | Client Credentials flow. |
| `authorizationCode`| `dict` | `object` | Optional | Must contain `authorizationUrl`, `tokenUrl`, `scopes` | Authorization code web flow. |

### `AgentCard`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `name` | `str` | `string` | **Required** | None | Agent's overall display name. |
| `url` | `str` | `string` | **Required** | Valid Base URL | The base URL via which the agent is accessible. |
| `version` | `str` | `string` | **Required** | SemVer recommended | The software version of the agent. |
| `protocolVersion` | `str` | `string` | **Required** | `"1.0.0"` (from constants) | The iteration of the A2A spec this agent adheres to. |
| `capabilities` | `AgentCapabilities` | `object` | **Required** | None | Agent capability toggles block. |
| `skills` | `list[AgentSkill]` | `array` | **Required** | None | Complete list of capabilities and interfaces the agent offers. |
| `defaultInputModes` | `list[str]` | `array` | **Required** | Default: `["application/json"]` | Fallback MIME types for input. |
| `defaultOutputModes`| `list[str]` | `array` | **Required** | Default: `["application/json"]` | Fallback MIME types for output. |
| `description` | `str` | `string` | Optional | None | Overall architectural description of the agent. |
| `provider` | `AgentProvider` | `object` | Optional | None | Corporate/Organizational provider details. |
| `documentationUrl` | `str` | `string` | Optional | Valid URL | Link to external human documentation. |
| `iconUrl` | `str` | `string` | Optional | Valid URL | Branding or profile image. |
| `security` | `list[dict]` | `array` | Optional | array of `{scheme_name: [scopes]}` | Global security requirements. |
| `securitySchemes` | `dict[str, SecurityScheme]` | `object` | Optional | None | Dictionary mapping scheme names to settings. |


## 2. Content Part Entities

### `FileWithBytes` (**FIX A4**: Replaced built-in `bytes` field name)
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `content` | `str` | `string` | **Required** | Base64 Encoded usually | The raw file payload. (Renamed from `bytes` to prevent python built-in shadowing). |
| `mimeType` | `str` | `string` | Optional | Valid MIME type | Media type of the encoded content. |

### `FileWithUri`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `uri` | `str` | `string` | **Required** | Valid URI | External or internal system reference link to file content. |
| `mimeType` | `str` | `string` | Optional | Valid MIME type | Media type of the remote file content. |

### `TextPart`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `kind` | `Literal` | `string` | **Required** | Exact: `"text"` | Discriminator field for union typing. |
| `text` | `str` | `string` | **Required** | None | The actual natural language or markdown string content. |
| `metadata` | `dict[str, Any]`| `object` | Optional | None | Extensibility block for custom UI parsing hints or app state. |

### `FilePart`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `kind` | `Literal` | `string` | **Required** | Exact: `"file"` | Discriminator field for union typing. |
| `file` | `FileWithBytes` / `FileWithUri` | `object` | **Required** | None | The file payload or reference object. |
| `metadata` | `dict[str, Any]`| `object` | Optional | None | Custom properties associated with the file part. |

### `DataPart`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `kind` | `Literal` | `string` | **Required** | Exact: `"data"` | Discriminator field for union typing. |
| `data` | `dict[str, Any]`| `object` | **Required** | None | Highly structured JSON payload content. |
| `metadata` | `dict[str, Any]`| `object` | Optional | None | Custom properties mapping. |


## 3. Communication Artifacts

### `Message`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `role` | `Literal` | `string` | **Required** | `"user"`, `"agent"` | Direct indication of part ownership. |
| `kind` | `Literal` | `string` | **Required** | Exact: `"message"` | Literal identifier. |
| `parts` | `list[Part]` | `array` | **Required** | Valid Part Unions | Multimodal content containers making up the overall message. |
| `messageId` | `str` | `string` | **Required** | UUID4 standard | Uniquely tracks identical payloads. |
| `contextId` | `str` | `string` | Optional | Matches `Task.contextId` | Used for session streaming and multi-turn conversational tie-ins. |
| `taskId` | `str` | `string` | Optional | Matches `Task.id` | Back reference pointing exactly to the specific job assignment. |
| `metadata` | `dict[str, Any]`| `object` | Optional | None | Injectable tracking. Overrides standard data parsing where handled. |

### `Artifact`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `artifactId` | `str` | `string` | **Required** | UUID4 standard | Output manifest tracker. |
| `parts` | `list[Part]` | `array` | **Required** | Valid Part Unions | Raw generated file elements. (**FIX B6**: Shielded by runtime checks preventing terminal append). |
| `name` | `str` | `string` | Optional | None | Human readable artifact identifier. |
| `description` | `str` | `string` | Optional | None | Purpose descriptor. |
| `index` | `int` | `integer` | Optional | `>= 0` | Explicit ordering hint for multi-artifact pipelines. |
| `append` | `bool` | `boolean` | Optional | None | Suggests this artifact merges against a prior ID element. |
| `lastChunk` | `bool` | `boolean` | Optional | None | Stream termination signal. |
| `metadata` | `dict[str, Any]`| `object` | Optional | None | Supplementary trace mapping. |


## 4. Workload State Models

### `TaskStatus`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `state` | `TaskState` | `string` | **Required** | `"submitted"`, `"processing"`, `"completed"`, `"failed"`, etc. | Strict state machine ENUM mapping. Validated on mutation. |
| `timestamp` | `str` | `string` | **Required** | ISO 8601 UTC | Temporal marker assigned during transition via `_internal.utils.utcnow()`. |
| `message` | `Message` | `object` | Optional | None | (**FIX B5**): If provided in transition, strictly auto-flushed to Task history. |

### `Task`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `id` | `str` | `string` | **Required** | UUID4 standard | Job identifier generated strictly on `TaskStore.create()` to avoid collision. |
| `contextId` | `str` | `string` | **Required** | Thread / Session ID | Groups discrete tasks into holistic flows. |
| `kind` | `Literal` | `string` | **Required** | Exact: `"task"` | Literal structure marker. |
| `status` | `TaskStatus` | `object` | **Required** | Complex nested validation | Present state pointer of execution machine. |
| `history` | `list[Message]` | `array` | **Required** | None | Mutable ledger of inter-agent dialog. System managed on transitions. |
| `artifacts` | `list[Artifact]` | `array` | **Required** | State validation pre-checks | Executed outputs. System verifies Task is non-terminal prior to appending. |
| `createdAt` | `str` | `string` | **Required** | ISO 8601 UTC | Static creation marker heavily utilized by pagination/sort queries. |
| `updatedAt` | `str` | `string` | **Required** | ISO 8601 UTC | Atomic monotonic incrementer on mutation. |
| `metadata` | `dict[str, Any]`| `object` | Optional | None | Job execution configurations, parameters mapping or traceability tags. |

### `TaskListResult`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `tasks` | `list[Task]` | `array` | **Required** | Up to pagination `limit` | Result set strictly sorted ascending by `createdAt`. |
| `nextCursor` | `str` | `string` | Optional | Opaque structure | Handled exclusively by specific TaskStore; caller must not mutate or parse. |


## 5. RPC Protocol Wrappers

### `JSONRPCError`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `code` | `int` | `integer` | **Required** | Defined standard error blocks | Fault severity mapping (eg validation drop vs internal fault). |
| `message` | `str` | `string` | **Required** | None | Safe display description. |
| `data` | `Any` | `any` | Optional | None | Stack traces or diagnostic context dictionaries. |

### `JSONRPCRequest`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `jsonrpc` | `Literal` | `string` | **Required** | Exact: `"2.0"` | Constant protocol signature. |
| `id` | `int / str / None`| `integer/string/null` | **Required** | Typically UUIDv4 str | RPC ID echoing for resolution matching. |
| `method` | `str` | `string` | **Required** | RPC command (e.g. `message/send`) | Instruction invocation. |
| `params` | `dict[str, Any]`| `object` | Optional | Maps kwargs | Task inputs. Wrapped identically via TypeAdapter validation before invocation. |

### `JSONRPCSuccessResponse` & `JSONRPCErrorResponse`
| Field | Python Type | JSON Type | Requirement | Constraints | Description & Micro-Details |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `jsonrpc` | `Literal` | `string` | **Required** | Exact: `"2.0"` | Constant protocol signature. |
| `id` | `int / str / None`| `integer/string/null` | **Required** | Matches request `id` | RPC response matching tie-in marker. |
| `result` | `Any` | `any` | Required | Present on Success | Validated response payload. Exchanged safely. |
| `error` | `JSONRPCError` | `object` | Required | Present on Error | Wrapped descriptive context sent on transaction failure. |

---

## 6. Lifecycle & Architectural Micro-Operations (For Arch Review Sheets)

| Component Area | Micro-detail Additions | Benefit | Description for Sheets |
| :--- | :--- | :--- | :--- |
| Task Store Lifecycle (`FIX B1/B2`) | `start()` and `stop()` bindings added | System Stability | Core persistence adapters now enforce lifecycle hooks driven by FastAPI shutdown/startup events, guaranteeing evictions logic triggers seamlessly preventing pool leakage. |
| Execution Polling (`FIX A3/B7`)   | `poll_timeout_seconds` bounding | Thread Safety | Remote client executions inside `_poll_until_done` are now bounded rigidly, solving infinite loop deadlocks on hung tasks. Also pushed asyncio imports outside the hot loop. |
| Serialization Engine | Module-level Pydantic TypeAdapters | Micro-Latency | Serialization instances instantiated once statically preventing inline instantiation overhead (Zero cost runtime allocations per RPC frame). |
| History Integrity (`FIX B5/B6`) | Transitional checks within Store implementations | Data Assurance | Explicit blocks added resolving bugs on artifacts appending to dead/terminal tasks, plus auto-injection of messages strictly to the historical trace array to ensure payload alignment. |
