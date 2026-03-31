# Agente de Gestión Interna — Infraestructura V2

Agente conversacional para consultas sobre gestiones territoriales de infraestructura pública en la provincia de Córdoba, Argentina. Expone una **API REST** y un **bot de Telegram**. Consulta datos reales desde **Google BigQuery** usando una arquitectura **ReAct** con 3 herramientas especializadas y memoria multi-turno.

---

## Tabla de contenidos

- [Arquitectura](#arquitectura)
- [Flujo de una consulta](#flujo-de-una-consulta)
- [Las 3 herramientas](#las-3-herramientas)
- [Proveedores LLM](#proveedores-llm)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos previos](#requisitos-previos)
- [Instalación](#instalación)
- [Docker Compose](#docker-compose)
- [Desarrollo local](#desarrollo-local)
- [API Reference](#api-reference)
- [Bot de Telegram](#bot-de-telegram)
- [Variables de entorno](#variables-de-entorno)
- [Seguridad](#seguridad)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)

---

## Arquitectura

```
Usuario (Telegram / API REST)
         │
         ▼
┌────────────────────┐
│  FastAPI            │  ← rate limiting (30 req/min), API key auth
└────────┬───────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│                    run_agent()                       │
│                                                     │
│  1. Pre-NLU      → respuestas cortas sin LLM        │
│  2. NLU          → intent + filtros (ParsedQuery)   │
│  3. DateResolver → "este mes" → ISO dates           │
│  4. QueryRewriter→ sinónimos de dominio             │
│  5. CatalogResolver → fuzzy match contra catálogo   │
│  6. Early exits  → UNKNOWN / needs_clarification    │
│  7. ReAct loop   → LLM ↔ tools (máx 3 rondas)      │
│  8. Synthesis    → respuesta final en español       │
└─────────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐      ┌──────────────────┐
│   LLM Provider   │      │    BigQuery       │
│  (pluggable)     │      │  vw_agent_gestiones│
└─────────────────┘      └──────────────────┘
         │
         ▼
┌─────────────────┐
│     Redis        │  ← contexto conversacional (últimos 5 turnos)
└─────────────────┘
```

### Componentes

| Componente | Archivo | Responsabilidad |
|---|---|---|
| NLU | `app/agent/nlu.py` | Clasifica intent y extrae filtros vía LLM structured output |
| DateResolver | `app/agent/date_resolver.py` | Convierte "este mes", "últimos 90 días" → ISO dates |
| QueryRewriter | `app/agent/query_rewriter.py` | Expande sinónimos de dominio, infiere categoría |
| CatalogResolver | `app/agent/catalog_resolver.py` | Fuzzy match de entidades (SequenceMatcher, umbral 0.80) |
| Executor | `app/agent/executor.py` | Loop ReAct: inyecta filtros → LLM → tools → síntesis |
| LLMProvider | `app/agent/llm_provider.py` | Abstracción multi-proveedor (HF, OpenAI, Gemini, Claude) |
| SQLValidator | `app/agent/sql_validator.py` | Valida SQL generado: solo SELECT, whitelist de tablas |
| MemoryService | `app/services/memory_service.py` | Contexto multi-turno vía Redis (TTL 24h) |
| TelegramService | `app/services/telegram_service.py` | Markdown→HTML, paginación SI/NO, chunks automáticos |

---

## Flujo de una consulta

```
"gestiones de agua en Villa Allende del ministerio de infraestructura"
                              │
              ┌───────────────▼──────────────────┐
              │           NLU (LLM)               │
              │  intent: buscar_listado           │
              │  localidad: "Villa Allende"       │
              │  ministerio: "infraestructura"    │
              │  search_terms: ["agua"]           │
              └───────────────┬──────────────────┘
                              │
              ┌───────────────▼──────────────────┐
              │         QueryRewriter             │
              │  ["agua"] → ["agua","provision",  │
              │   "potable","acueducto",...]      │
              └───────────────┬──────────────────┘
                              │
              ┌───────────────▼──────────────────┐
              │        CatalogResolver            │
              │  "Villa Allende" → VILLA ALLENDE  │
              │  "infraestructura" →              │
              │    MIN_INFRAESTRUCTURA_SP         │
              │  departamento inferido → COLÓN    │
              └───────────────┬──────────────────┘
                              │
              ┌───────────────▼──────────────────┐
              │           ReAct loop              │
              │  LLM llama: buscar_gestiones(     │
              │    localidad="VILLA ALLENDE",     │
              │    departamento="COLÓN",          │
              │    ministerio_agencia_id=...,     │
              │    search_terms=[...]             │
              │  )                                │
              └───────────────┬──────────────────┘
                              │
              ┌───────────────▼──────────────────┐
              │         BigQuery → 16 filas       │
              │  LLM sintetiza respuesta          │
              │  "Mostrando 5 de 16 gestiones.    │
              │   ¿Querés ver todas? Respondé sí" │
              └──────────────────────────────────┘
```

---

## Las 3 herramientas

### `buscar_gestiones`
Listado de gestiones con búsqueda híbrida:
- Filtros exactos: departamento, localidad, ministerio, categoría, estado, canal, fechas
- Búsqueda LIKE en `detalle`, `observaciones` y `tipo_gestion_nombre`
- Paginación automática: por defecto retorna 5 resultados, con `total_count` y `has_more`

### `consultar_estadisticas`
Text-to-SQL con validación:
1. LLM genera una query SELECT basada en el esquema de `vw_agent_gestiones`
2. `SQLValidator` verifica: solo SELECT, whitelist de tablas, sin patrones peligrosos
3. Ejecuta contra BigQuery con parámetros tipados
4. Responde preguntas como "¿cuántas?", "top 5 departamentos", "promedio de días"

### `buscar_por_proximidad`
Búsqueda geográfica con Haversine en BigQuery:
- Resuelve el punto de referencia desde el nombre de localidad (544 localidades con coordenadas)
- Calcula distancias en la query SQL directamente
- Devuelve gestiones ordenadas por distancia en km

---

## Proveedores LLM

| Provider | Variable | Modelo por defecto | Notas |
|---|---|---|---|
| **HuggingFace** (default) | `HUGGINGFACE_API_KEY` | `Qwen/Qwen2.5-72B-Instruct` | Via OpenAI-compatible router |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o-mini` | Strict JSON schema en NLU |
| **Gemini** | `GEMINI_API_KEY` | `gemini-2.5-flash` | Via endpoint OpenAI-compatible |
| **Claude** | `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` | SDK oficial Anthropic |

Se selecciona con `LLM_PROVIDER=huggingface|openai|gemini|claude` en `.env` o en `docker-compose.yml`.

---

## Estructura del proyecto

```
agente_gestionInterna_InfraestructuraV1/
└── backend/
    ├── app/
    │   ├── agent/
    │   │   ├── catalog_resolver.py   # fuzzy match entidades contra catálogo
    │   │   ├── date_resolver.py      # fechas relativas → ISO
    │   │   ├── executor.py           # loop ReAct + inyección de filtros
    │   │   ├── llm_provider.py       # abstracción 4 proveedores LLM
    │   │   ├── nlu.py                # NLU → ParsedQuery (3 intents)
    │   │   ├── query_rewriter.py     # diccionario de sinónimos de dominio
    │   │   ├── sql_validator.py      # validación SQL generado por LLM
    │   │   └── tool_registry.py      # descriptores OpenAI de las 3 tools
    │   ├── api/
    │   │   ├── agent.py              # POST /agent/query
    │   │   ├── health.py             # GET /health
    │   │   └── telegram.py           # POST /telegram/webhook
    │   ├── repositories/
    │   │   └── query_catalog.py      # queries SQL predefinidas (legacy)
    │   ├── schemas/
    │   │   └── agent.py              # Pydantic models: ParsedQuery, Turn, etc.
    │   ├── services/
    │   │   ├── audit_service.py      # log de interacciones a JSONL
    │   │   ├── bigquery_service.py   # cliente BQ con retry y parámetros tipados
    │   │   ├── catalog_service.py    # carga catálogo BQ → snapshot fallback
    │   │   ├── memory_service.py     # contexto Redis + fakeredis fallback
    │   │   └── telegram_service.py   # Markdown→HTML, botones SI/NO, chunks
    │   └── tools/
    │       └── analytics_tools.py    # implementación de las 3 herramientas
    ├── creds/
    │   └── .gitkeep                  # service_account.json va aquí (NO commitear)
    ├── data/
    │   └── catalog_snapshot.json     # cache del catálogo al iniciar
    ├── tests/
    │   ├── eval/                     # dataset de evaluación end-to-end
    │   ├── integration/              # tests contra BigQuery real
    │   └── unit/                     # tests unitarios (sin deps externas)
    ├── .dockerignore
    ├── .env.example                  # plantilla de configuración
    ├── .gitignore
    ├── docker-compose.yml
    ├── Dockerfile
    └── requirements.txt
```

---

## Requisitos previos

- **Docker Desktop** (recomendado para producción)
- **Python 3.11+** (para desarrollo local)
- **Google Cloud** project con BigQuery habilitado
- **Service Account** con roles: `BigQuery Data Viewer` + `BigQuery Job User`
- **API key** de al menos uno de los LLM providers
- **Redis** — incluido en Docker Compose; en dev local se usa `fakeredis` automáticamente si Redis no está disponible

---

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/agente_gestionInterna_InfraestructuraV1.git
cd agente_gestionInterna_InfraestructuraV1/backend
```

### 2. Credenciales de Google Cloud

```bash
# Copiá tu service account al directorio creds/
cp /ruta/a/tu/service_account.json creds/service_account.json
```

> **IMPORTANTE:** `creds/service_account.json` está en `.gitignore` — nunca lo commitees.
>
> Para generarlo: GCP Console → IAM → Cuentas de servicio → tu cuenta → Claves → Crear clave JSON.

### 3. Variables de entorno

```bash
cp .env.example .env
# Editá .env con tus valores reales
```

El archivo `.env` está en `.gitignore`.

### 4. Vista BigQuery requerida

El agente espera una vista `vw_agent_gestiones` en el dataset configurado. Columnas mínimas:

```sql
id_gestion STRING,
fecha_ingreso DATE,
fecha_finalizacion DATE,
anio_ingreso INT64,
mes_ingreso INT64,
estado_nombre STRING,         -- 'ARCHIVADO','DERIVADO A SUAC','FINALIZADA','INGRESADO',...
urgencia_nombre STRING,       -- 'Alta','Media','Baja'
ministerio_agencia_id STRING,
ministerio_agencia_nombre STRING,
categoria_general_nombre STRING,
tipo_gestion_nombre STRING,
canal_origen_nombre STRING,
detalle STRING,
observaciones STRING,
departamento STRING,
localidad STRING,
lat NUMERIC,
lon NUMERIC,
dias_abierta INT64,
dias_resolucion INT64,
es_abierta BOOLEAN,
es_urgente BOOLEAN
```

---

## Docker Compose

```bash
cd backend/

# HuggingFace / Qwen2.5 (default)
docker compose up --build

# Gemini
docker compose up --build gemini

# OpenAI
docker compose up --build openai

# Claude
docker compose up --build claude
```

Redis se levanta automáticamente como servicio dependiente.

```bash
# Verificar
curl http://localhost:8080/health
# {"status":"ok","checks":{"bigquery":true,"redis":true,"openai":true}}
```

---

## Desarrollo local

```bash
cd backend/

# Instalar dependencias
pip install -r requirements.txt
pip install fakeredis  # fallback cuando Redis no está disponible

# Configurar entorno
cp .env.example .env
# Editar .env: cambiar REDIS_URL=redis://localhost:6379/0

# Limpiar pycache (importante en Windows/OneDrive para evitar bytecode stale)
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} +

# Iniciar
PYTHONDONTWRITEBYTECODE=1 python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

> Si Redis no está instalado localmente, el sistema usa `fakeredis` en memoria automáticamente (sin persistencia entre reinicios). Suficiente para desarrollo.

---

## API Reference

### `GET /health`

```json
{
  "status": "ok",
  "checks": {"bigquery": true, "redis": true, "openai": true}
}
```

### `POST /agent/query`

**Headers:**
```
X-API-Key: <API_SECRET_KEY>
Content-Type: application/json
```

**Body:**
```json
{
  "message": "cuántas gestiones hay en el departamento Colón",
  "conversation_id": "opcional-uuid",
  "user_id": "opcional",
  "channel": "api"
}
```

**Response:**
```json
{
  "answer": "Hay 308 gestiones registradas en el departamento de Colón.",
  "intent": "consultar_numerico",
  "entities": {
    "departamento": "COLÓN",
    "localidad": null,
    "ministerio_agencia_id": null,
    "ministerio_nombre": null
  },
  "tools_used": ["consultar_estadisticas"],
  "confidence": 0.95
}
```

### Ejemplos

```bash
API_KEY="tu-api-secret-key"
BASE="http://localhost:8080/agent/query"

# Consulta numérica
curl -X POST $BASE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"message": "top 5 departamentos con más gestiones"}'

# Listado con filtros
curl -X POST $BASE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"message": "gestiones de agua en Villa Allende", "conversation_id": "conv-1"}'

# Paginación — follow-up en la misma conversación
curl -X POST $BASE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"message": "ver todas", "conversation_id": "conv-1"}'

# Búsqueda por proximidad
curl -X POST $BASE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"message": "gestiones cerca de La Falda en un radio de 20km"}'

# Filtro por fecha y categoría
curl -X POST $BASE -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"message": "gestiones de salud en 2025 en el departamento Colón"}'
```

### Intents soportados

| Intent | Cuándo se activa | Tool usada |
|---|---|---|
| `buscar_listado` | "listame", "mostrame", "cuáles son" | `buscar_gestiones` |
| `consultar_numerico` | "cuántas", "ranking", "promedio", "resumen" | `consultar_estadisticas` |
| `buscar_por_proximidad` | "cerca de", "a X km de", "en un radio de" | `buscar_por_proximidad` |

---

## Bot de Telegram

### Configuración

1. Crear bot con [@BotFather](https://t.me/BotFather) → obtener `TELEGRAM_BOT_TOKEN`
2. Generar webhook secret: `openssl rand -hex 32`
3. Agregar al `.env`:
   ```
   TELEGRAM_BOT_TOKEN=tu-token
   TELEGRAM_WEBHOOK_SECRET=tu-secret
   ```
4. Exponer el servidor públicamente (ngrok, cloudflared, VPS, etc.)
5. Registrar el webhook:
   ```bash
   curl "https://api.telegram.org/botTU_TOKEN/setWebhook" \
     -d "url=https://tu-dominio.com/telegram/webhook" \
     -d "secret_token=TU_WEBHOOK_SECRET"
   ```

### Características

- Respuestas en **HTML** con campos en **negrita**
- **Paginación interactiva**: botones SI / NO cuando hay más resultados que los mostrados
- Typing indicator mientras procesa
- `/reset` o `/borrar_memoria` — limpia el contexto conversacional
- Mensajes largos se dividen automáticamente en chunks de 4000 caracteres

---

## Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `LLM_PROVIDER` | Sí | `huggingface` \| `openai` \| `gemini` \| `claude` |
| `HUGGINGFACE_API_KEY` | Si provider=huggingface | Token HuggingFace (requiere cuenta Pro para Qwen 72B) |
| `HUGGINGFACE_MODEL` | No | Default: `Qwen/Qwen2.5-72B-Instruct` |
| `OPENAI_API_KEY` | Si provider=openai | API key OpenAI |
| `OPENAI_MODEL` | No | Default: `gpt-4o-mini` |
| `GEMINI_API_KEY` | Si provider=gemini | API key Google AI Studio |
| `GEMINI_MODEL` | No | Default: `gemini-2.5-flash` |
| `ANTHROPIC_API_KEY` | Si provider=claude | API key Anthropic |
| `ANTHROPIC_MODEL` | No | Default: `claude-haiku-4-5-20251001` |
| `GCP_PROJECT_ID` | Sí | ID del proyecto GCP |
| `BQ_DATASET` | Sí | Dataset BigQuery (default: `infra_gestion`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Sí | Path al service account JSON |
| `REDIS_URL` | Sí | URL Redis. En Docker: `redis://:password@redis:6379/0` |
| `REDIS_PASSWORD` | Sí | Password Redis |
| `API_SECRET_KEY` | Sí | Clave para autenticar `POST /agent/query` |
| `TELEGRAM_BOT_TOKEN` | Solo si usás Telegram | Token del bot |
| `TELEGRAM_WEBHOOK_SECRET` | Recomendado | Valida autenticidad de webhooks de Telegram |
| `LOG_LEVEL` | No | `INFO` (default) \| `WARNING` \| `DEBUG` |

---

## Seguridad

### Archivos que NUNCA deben commitearse

| Archivo | Motivo | Cubierto por |
|---|---|---|
| `creds/service_account.json` | Credenciales GCP con acceso a BigQuery | `.gitignore` |
| `.env` | API keys, tokens, passwords | `.gitignore` |
| `data/agent_interactions.jsonl` | Log de auditoría con consultas reales | `.gitignore` |

Verificar antes de cada push:
```bash
git status
git ls-files creds/     # debe mostrar solo .gitkeep
git ls-files | grep env # no debe mostrar .env
```

### En producción

- Cambiar `API_SECRET_KEY` por un valor fuerte: `openssl rand -hex 32`
- Cambiar `REDIS_PASSWORD` por un valor fuerte
- Usar HTTPS (nginx + certbot, Cloudflare, o equivalente)
- El contenedor Docker corre como usuario no-root (`appuser`, uid 1000)
- Los secrets productivos deberían gestionarse con **GCP Secret Manager** o **Docker Secrets**, no en `.env`

### Si accidentalmente commiteás credenciales

1. **Revocar inmediatamente** la service account en GCP Console → IAM → Claves
2. **Regenerar** todas las API keys expuestas en sus respectivas consolas
3. Purgar el historial de git con [BFG Repo Cleaner](https://rtyley.github.io/bfg-repo-cleaner/):
   ```bash
   bfg --delete-files service_account.json
   git push --force
   ```
4. Notificar a los colaboradores para que reclonen el repositorio

---

## Tests

```bash
cd backend/

# Unit tests (sin dependencias externas)
pytest tests/unit/ -v

# Integration tests (requiere BigQuery configurado)
GCP_PROJECT_ID=tu-project \
GOOGLE_APPLICATION_CREDENTIALS=./creds/service_account.json \
  pytest tests/integration/ -v

# Evaluación end-to-end
python tests/eval/run_eval.py
```

---

## Troubleshooting

### Puerto 8080 ya en uso

```powershell
# Encontrar y matar el proceso
netstat -ano | Select-String ":8080"
Stop-Process -Id <PID> -Force

# O detener todos los contenedores Docker
docker compose down
```

### Redis no disponible en dev local

El sistema usa `fakeredis` automáticamente si Redis no está instalado. La memoria funciona pero no persiste entre reinicios del servidor. Para instalar Redis localmente:
- **Windows**: usar WSL (`sudo apt-get install redis-server`)
- **Mac**: `brew install redis`
- **Docker**: `docker run -d -p 6379:6379 redis:7-alpine`

### Bytecode stale (OneDrive / Windows)

OneDrive puede causar desincronización de timestamps en archivos `.pyc`, haciendo que Python use código viejo. Antes de reiniciar el servidor:

```bash
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} +
# O usar la variable de entorno:
PYTHONDONTWRITEBYTECODE=1 python -m uvicorn ...
```

### El LLM devuelve "unknown" para consultas válidas

Generalmente es rate limiting del proveedor LLM. Esperar unos segundos y reintentar. Si es sostenido, verificar cuotas en la consola del proveedor.
