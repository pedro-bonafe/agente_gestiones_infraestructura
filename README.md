# Infrastructure Management Agent V1

Conversational agent for querying infrastructure management data (`gestiones territoriales`) using natural language. Powered by **OpenAI function calling + BigQuery**.

---

## Architecture

```
User (API / Telegram)
        │
        ▼
  FastAPI Router
        │
        ▼
  Agent Executor  ◄──── NLU (OpenAI structured output)
        │                     │
        │              Catalog Resolver
        │                (fuzzy matching)
        ▼
  ReAct Loop (OpenAI function calling)
        │
        ├── get_territory_metrics ──────────┐
        ├── get_open_and_delay_metrics      │
        ├── get_ministry_rankings           ├──► BigQuery
        ├── get_gestiones_listing          │
        ├── get_ranking_* (5 tools)        │
        └── get_general_summary ───────────┘
        │
        ▼
  LLM generates final answer from tool results
        │
        ▼
  Redis (conversation memory, TTL 24h)
  JSONL (interaction audit log)
```

**Key design decisions:**
- Single LLM call for NLU with `strict: true` JSON schema — no heuristics
- LLM decides which tools to call via function calling (ReAct pattern)
- LLM generates the final natural language response from data
- All SQL is parameterized, pre-defined in `query_catalog.py` — no NL2SQL
- Redis for conversation memory — thread-safe, scalable
- Graceful degradation: if Redis or BigQuery unavailable, system still responds

---

## Setup

### Prerequisites
- Python 3.11+
- Docker + Docker Compose
- Google Cloud project with BigQuery dataset `infra_gestion`
- Service account JSON with BigQuery read access
- OpenAI API key

### Local setup

```bash
cd backend

# Copy and fill env vars
cp .env.example .env
# Edit .env: set GCP_PROJECT_ID, OPENAI_API_KEY, API_SECRET_KEY

# Place credentials
cp /path/to/your/service_account.json creds/service_account.json

# Install dependencies
pip install -r requirements.txt

# Run
uvicorn app.main:app --reload --port 8080
```

### Docker setup

```bash
cd backend

cp .env.example .env
# Edit .env — set the API key for the provider you want to use
cp /path/to/your/service_account.json creds/service_account.json

# Default: HuggingFace / Qwen2.5
docker compose up --build

# Or pick a specific provider:
docker compose up --build gemini
docker compose up --build openai
docker compose up --build claude
```

The API will be available at `http://localhost:8080`.

### Troubleshooting: port already allocated

`Bind for 0.0.0.0:8080 failed: port is already allocated` means another process (often a previous Docker container) is holding port 8080. Fix:

**Option 1 — stop all containers using port 8080 (Windows PowerShell):**
```powershell
# Find the container holding the port
docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Ports}}" | Select-String "8080"

# Stop it (replace <CONTAINER_ID> with the ID from above)
docker stop <CONTAINER_ID>
```

**Option 2 — nuclear: stop all running containers:**
```powershell
docker stop $(docker ps -q)
```

**Option 3 — find and kill the Windows process on port 8080:**
```powershell
# Find PID
netstat -ano | Select-String ":8080"

# Kill it (replace <PID> with the number in the last column)
Stop-Process -Id <PID> -Force
```

**Option 4 — clean up orphan Compose stacks:**
```powershell
# From the backend directory — tears down containers, networks, and volumes
docker compose down

# Then bring it back up
docker compose up --build
```

> This is a recurring issue when switching between the V0 and V1 projects. Both use port 8080 — always `docker compose down` the old stack before starting the other.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GCP_PROJECT_ID` | Yes | Google Cloud project ID |
| `BQ_DATASET` | Yes | BigQuery dataset name (default: `infra_gestion`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes | Path to service account JSON |
| `LLM_PROVIDER` | No | Provider to use: `huggingface` (default) \| `openai` \| `gemini` \| `claude` |
| `HUGGINGFACE_API_KEY` | If using HF | HuggingFace Inference API token |
| `HUGGINGFACE_MODEL` | No | Model (default: `Qwen/Qwen2.5-72B-Instruct`) |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key |
| `OPENAI_MODEL` | No | Model (default: `gpt-4o-mini`) |
| `GEMINI_API_KEY` | If using Gemini | Google AI Studio API key |
| `GEMINI_MODEL` | No | Model (default: `gemini-1.5-flash`) |
| `ANTHROPIC_API_KEY` | If using Claude | Anthropic API key |
| `ANTHROPIC_MODEL` | No | Model (default: `claude-haiku-4-5-20251001`) |
| `REDIS_URL` | No | Redis connection URL (default: `redis://localhost:6379/0`) |
| `REDIS_PASSWORD` | No | Redis password |
| `API_SECRET_KEY` | Yes | API key for `/agent/query` endpoint |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token |
| `TELEGRAM_WEBHOOK_SECRET` | No | Telegram webhook secret for signature verification |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

---

## API Usage

### Health check
```bash
curl http://localhost:8080/health
```
Response:
```json
{"status": "ok", "checks": {"bigquery": true, "redis": true, "openai": true}}
```

### Query the agent
```bash
curl -X POST http://localhost:8080/agent/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: changeme-in-production" \
  -d '{
    "message": "Cuáles gestiones tiene el departamento Río Segundo?",
    "conversation_id": "user-session-001"
  }'
```

Response:
```json
{
  "answer": "En RIO SEGUNDO se registran 47 gestiones: 32 abiertas, 15 finalizadas y 8 urgentes (17%)...",
  "intent": "territorial_listing",
  "entities": {"departamento": "RIO SEGUNDO", "localidad": null, "ministerio_agencia_id": null},
  "tools_used": ["get_territory_metrics", "get_gestiones_listing"],
  "confidence": 0.95
}
```

### Multi-turn conversation (followup)
```bash
# First turn
curl -X POST http://localhost:8080/agent/query \
  -H "X-API-Key: changeme" \
  -d '{"message": "Gestiones de Cruz del Eje", "conversation_id": "conv-001"}'

# Follow-up — the agent reuses the territory from context
curl -X POST http://localhost:8080/agent/query \
  -H "X-API-Key: changeme" \
  -d '{"message": "Y cuáles son las métricas de demora?", "conversation_id": "conv-001"}'
```

### Example queries supported

| Query | Intent |
|---|---|
| "Cuáles gestiones tiene el departamento Río Segundo" | `territorial_listing` |
| "Gestiones del Ministerio de Obras Públicas en Calmayo, Calamuchita" | `ministry_territorial_listing` |
| "Cuántas gestiones abiertas hay en Cruz del Eje? Cuál es la demora promedio?" | `open_and_delay_metrics` |
| "Cuál es el ministerio con más gestiones en Cruz del Eje?" | `department_ministry_rankings` |
| "Dame el ranking de localidades con más gestiones" | `ranking_localidades` |
| "Qué departamento tiene más urgencias?" | `ranking_departamentos` |
| "Resumen general de gestiones" | `resumen_general` |

---

## Telegram Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and get the token
2. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in `.env`
3. Expose the service publicly (e.g. with [ngrok](https://ngrok.com/) or [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/))
4. Register the webhook:
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://your-domain.com/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```
5. Send `/start` or any natural language message to the bot

**Telegram commands:**
- `/reset` or `/borrar_memoria` — clears conversation context

---

## Running Tests

### Unit tests (no external dependencies)
```bash
cd backend
pytest tests/unit/ -v
```

### Integration tests (requires BigQuery credentials)
```bash
cd backend
GCP_PROJECT_ID=your-project GOOGLE_APPLICATION_CREDENTIALS=./creds/service_account.json \
  pytest tests/integration/ -v
```

### Evaluation (requires OpenAI key + BigQuery)
```bash
cd backend
python tests/eval/run_eval.py
```

The eval script runs all 15 test cases and reports:
- **Intent accuracy** (must be ≥ 90% to pass)
- **Entity extraction accuracy**
- **Clarification accuracy**

---

## Project Structure

```
backend/
├── app/
│   ├── agent/
│   │   ├── nlu.py              # OpenAI structured output NLU
│   │   ├── executor.py         # ReAct loop with function calling
│   │   ├── tool_registry.py    # OpenAI tool descriptors + registry
│   │   └── catalog_resolver.py # Entity fuzzy matching against catalog
│   ├── tools/
│   │   └── analytics_tools.py  # Async BigQuery tool functions
│   ├── services/
│   │   ├── bigquery_service.py # BigQuery client with retry
│   │   ├── catalog_service.py  # Catalog loading (BQ → snapshot fallback)
│   │   ├── memory_service.py   # Redis conversation memory
│   │   ├── audit_service.py    # Interaction logging
│   │   └── telegram_service.py # Telegram Bot API
│   ├── repositories/
│   │   └── query_catalog.py    # All SQL queries (parameterized)
│   ├── schemas/
│   │   └── agent.py            # Pydantic models (Intent, ParsedQuery, etc.)
│   ├── api/
│   │   ├── agent.py            # POST /agent/query
│   │   ├── telegram.py         # POST /telegram/webhook
│   │   └── health.py           # GET /health
│   ├── config.py               # Settings (pydantic-settings)
│   └── main.py                 # FastAPI app + lifespan
├── tests/
│   ├── unit/                   # Fast tests, no external deps
│   ├── integration/            # Tests against real BigQuery
│   └── eval/                   # NLU evaluation dataset + runner
├── data/
│   ├── catalog_snapshot.json   # Fallback catalog
│   └── agent_interactions.jsonl  # Interaction audit log (generated)
├── creds/                      # GCP service account (gitignored)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```
