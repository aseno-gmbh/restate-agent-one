# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

Two Python AI agents that use [Restate](https://restate.dev/) as a durable task orchestrator. Each agent lives in `container/` and has a corresponding Helm chart in `modules/`.

| Component | Path | Purpose |
|---|---|---|
| `agent-one` | `container/agent-one/` | Interrupt & regenerate coding agent |
| `agent-a2a` | `container/agent-a2a/` | A2A-protocol agent with reimbursement workflow |

## Running locally

Both agents require a running Restate server and a `.env` file with LLM credentials:

```bash
# .env (required for both agents)
LLM_API_BASE=https://...
LLM_API_KEY=sk-...
LLM_MODEL_NAME=litellm_proxy/xxx-model
```

**Start Restate:**
```bash
restate-server
```

**agent-one:**
```bash
cd container/agent-one
uv run --env-file .env .
# Register with Restate:
restate -y deployments register http://localhost:9080 --force
```

**agent-a2a (reimbursement):**
```bash
cd container/agent-a2a
uv run --env-file .env app/reimbursement
# Then register via Restate UI at http://localhost:9070 or:
restate -y deployments register http://localhost:9080/restate/v1 --force
```

Restate UI is at `http://localhost:9070`.

## Architecture

### agent-one

Two Restate services form a cancellable multi-step pipeline:

- **`CodingAgent`** (VirtualObject, keyed by agent ID) — stores conversation history in Restate K/V, handles interruptions by calling `cancel_and_wait()` on the running task before dispatching a new one.
- **`CodingTask`** (Service) — runs three sequential LLM steps (plan → draft → polish) via `ctx.run_typed`. Cancellation surfaces as `TerminalError(status_code=409)` at the next `await`, where the handler runs cleanup and re-raises.

LLM calls go through `util/litellm_call.py`, which wraps LiteLLM's `acompletion` using the env vars above.

### agent-a2a

Implements the [Agent2Agent (A2A) protocol](https://github.com/a2aproject/A2A) on top of Restate.

**Core abstraction — `RestateA2AMiddleware`** (`app/common/a2a/a2a_middleware.py`):

Takes an `AgentCard` and an `A2AAgent` implementation and dynamically creates two Restate services:
- **`{AgentName}A2AServer`** (Service) — receives JSON-RPC requests (`message/send`, `tasks/get`, `tasks/cancel`), dispatches to the task object.
- **`{AgentName}TaskObject`** (VirtualObject, keyed by task ID) — persists task state in Restate K/V, manages the task lifecycle, and calls the agent's `invoke` method.

**ADK integration** (`app/common/adk/`):

- `RestateSessionService` — implements Google ADK's `BaseSessionService` using Restate K/V as the backing store; events are persisted via `ctx.run_typed` to guarantee determinism on replay.
- `RestatePlugin` — ADK plugin that injects the current `restate.ObjectContext` into `session.state["restate_context"]` so tools can call durable Restate APIs.
- `restate_utils.py` — context-var based mechanism (`restate_overrides`) to pass the Restate context into the ADK runner without threading it explicitly.

**Reimbursement example** (`app/reimbursement/`):

A stateful, human-in-the-loop workflow: amounts > $1000 USD block on an awakeable (Restate's callback primitive) until a human POSTs to `/awakeables/{id}/resolve`. Payment is scheduled at end-of-month via `service_send(..., send_delay=...)`.

The FastAPI app serves `/.well-known/agent.json` (agent card) and mounts the Restate ASGI app at `/restate/v1`.

### Helm charts

`modules/agent-one/helmcharts/` and `modules/agent-a2a/helmcharts/` deploy each agent. Key `values.yaml` fields:

```yaml
llm:
  apiBase: ...
  apiKey: ...
  modelName: ...
restate:
  host: "http://restate:8080"
```

### CI/CD

GitHub Actions (`.github/workflows/`) build and push Docker images to Docker Hub (`aseno/restate-agent-a2a`, `aseno/restate-agent-one`) on every push to `main`. Build context is `./container`; Dockerfiles are `Dockerfile.a2a` and `Dockerfile.one`.

## Key Restate concepts used

- **VirtualObject** — single-writer, keyed entity with durable K/V state; handlers run serially per key.
- **`ctx.run_typed`** — wraps a closure so its result is persisted; re-runs are skipped on replay (idempotent side-effects).
- **`ctx.cancel_invocation` + `ctx.attach_invocation`** — cancel a running handler and wait for it to finish cleanup.
- **Awakeables** — `ctx.awakeable()` returns a `(id, promise)` pair; resolved externally by POSTing to the Restate admin API.
- **`ctx.service_send(..., send_delay=...)`** — fire-and-forget with optional delay, used for scheduling end-of-month payments.
