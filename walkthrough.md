# Step 1 Walkthrough: Project Structure & Docker Compose Setup

We have successfully scaffolded the project files for Step 1. Here is a summary of the layout and services defined:

## Files Created

- **Config**:
  - [.env.example](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/.env.example) - Template for local configurations (ports, keys, toggle options).
  - [.env](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/.env) - Local configurations copy.
- **Docker Orchestration**:
  - [docker-compose.yml](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/docker-compose.yml) - Configured for 5 services:
    1. `postgres` (with `pgvector/pgvector:pg16`)
    2. `redis` (v7-alpine for Redis Streams)
    3. `backend` (FastAPI with skeleton endpoints)
    4. `worker` (Python loop for ingestion tasks)
    5. `frontend` (Angular dev server bound to port `4200`)
- **Backend (FastAPI)**:
  - [backend/requirements.txt](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/requirements.txt) - Backend python library list.
  - [backend/Dockerfile](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/Dockerfile) - Set up to pre-cache the `all-MiniLM-L6-v2` embedding model.
  - [backend/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/main.py) - Exposes `/health`, POST `/query` stub, and WS `/subscribe` stub.
- **Worker (Python)**:
  - [worker/requirements.txt](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/requirements.txt) - Ingestion worker python library list.
  - [worker/Dockerfile](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/Dockerfile) - Set up to pre-cache the embedding model for ingestion embeddings.
  - [worker/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/main.py) - Skeleton run loop.
- **Frontend (Angular)**:
  - [frontend/](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/frontend) - Full Angular 18 application scaffolded with router configuration.
  - [frontend/Dockerfile](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/frontend/Dockerfile) - Development server setup for containerized execution.

---

## Step 1 Verification Complete

The Docker Compose containers were built and started successfully:
- All 5 services (`postgres`, `redis`, `backend`, `worker`, `frontend`) are up.
- The backend health check was verified and returned: `{"status": "ok", "message": "Backend is healthy"}`.

We are ready to move on to **Step 2: Ingestion worker**.

## Step 2 Walkthrough: Ingestion Worker

We have implemented the live polling client in the ingestion worker:

### Implementation Details
- **Configuration**: [worker/config.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/config.py) manages configurations.
- **Poller Client**: [worker/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/main.py) polls the GitHub Events API (`https://api.github.com/events`) using `httpx`.
- **Deduplication**: Employs a Redis Set (`rag:processed_event_ids`) alongside local memory caches to filter out already-processed events.
- **Normalization**: Formats payload details (Push, PullRequest, Issues, Comments, Watch, Fork) into clean, context-rich summaries for RAG embeddings.
- **Redis Queueing**: Enqueues normalized event records into the `github_events_stream` capping the queue length to 5,000 to manage memory.
- **Rate-limit / Error Management**: Gracefully parses and respects `X-Poll-Interval` and rate limit reset headers. Automatically sleeps on 403s and retries on network dropouts.

### Verification Results
- The worker container starts up, establishes a connection to Redis, and registers the polling process:
  `worker.ingest: Successfully processed 30 new events.`
- Verified the stream content: `redis-cli xlen github_events_stream` returns `30`.
- Inspected a stream item to verify correct normalization.

We are ready to move on to **Step 3: Embedding + indexing pipeline**.

## Step 3 Walkthrough: Embedding & Indexing Pipeline

We have successfully integrated the local embedding generator and pgvector database indexing.

### Implementation Details
- **Database Schema**: Established `documents` table in PostgreSQL with columns `event_id`, `event_type`, `actor`, `repo`, `content` (normalized summary text), `embedding` (`VECTOR(384)`), and `created_at`.
- **HNSW Indexing**: Added an HNSW index using cosine operators (`vector_cosine_ops`) for fast similarity search during RAG retrievals.
- **Local Embedding Ingestion**: The processor thread in [worker/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/main.py) uses `sentence-transformers` (`all-MiniLM-L6-v2`) on CPU to embed each document summary.
- **Double-stage Deduplication**:
  1. *Exact check*: Skips items if `event_id` already exists in PostgreSQL (handled with unique constraints and `ON CONFLICT DO NOTHING`).
  2. *Near-duplicate check*: Queries PostgreSQL for documents by the same actor, repository, and event type in the last 10 minutes. If the cosine similarity of their embeddings is greater than `0.95` (representing near-identical commit pushes or issue comments), the event is skipped.
- **Pub/Sub Notification**: Publishes indexed events to Redis Pub/Sub channel `rag:new_document_channel` for downstream WebSocket notifications.

### Verification Results
- Database successfully initialized: tables created, `pgvector` extension enabled, and HNSW index constructed.
- Worker logs confirm successful model loading, stream consumption, embedding computation, near-duplicate check executions, and vector insertions.
- Direct database inspection confirmed the documents table is populated (e.g. 87 rows inserted) and vectors have exactly 384 dimensions.

We are ready to move on to **Step 4: Basic REST /query endpoint**.

## Step 4 Walkthrough: Basic REST /query Endpoint

We have successfully implemented the backend retrieval and response synthesis API.

### Implementation Details
- **Backend Configuration**: [backend/app/config.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/app/config.py) manages backend configurations.
- **Connection Pool**: [backend/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/main.py) configures a `ThreadedConnectionPool` to safely manage concurrent PostgreSQL queries.
- **Local Embedding**: Computes user query embeddings inside the FastAPI application using the `all-MiniLM-L6-v2` model loaded on CPU startup.
- **Vector Retrieve**: Performs standard cosine similarity searches using pgvector to fetch the top-K matching events from PostgreSQL.
- **Synthesis Engine**: 
  - *Anthropic Claude integration*: Added standard Anthropic client calls to generate synthesized markdown answers using the retrieved events as context.
  - *Context-aware Mock LLM*: Created a simulated fallback synthesizer that extracts, formats, and aggregates the retrieved event data into an answer when the API key is not supplied. This allows testing the RAG loop end-to-end for free.

### Verification Results
- The FastAPI backend starts successfully, connects to PostgreSQL, and caches the embedding model.
- Sent test POST query requests to `http://localhost:8000/query`.
- Verified that the response is returned correctly:
  - Retrieves relevant documents (e.g. matched and fetched events for "actions").
  - Cosine similarity scores are correctly calculated.
  - The synthesized answer correctly aggregates details from the retrieved context.

We are ready to move on to **Step 5: Freshness-aware ranking + query-type classification**.

## Step 5 Walkthrough: Freshness-Aware Ranking & Query-Type Classification

We have successfully implemented the query type classification and exponential freshness-decay re-ranking mechanisms.

### Implementation Details
- **Query Classification**: Added keyword-based heuristics to automatically classify incoming requests:
  - *LATEST*: Queries looking for recent activities (e.g. including terms like "recent", "latest", "now") default to low similarity weight (`0.4`) and high freshness weight (`0.6`).
  - *CONCEPTUAL*: Queries seeking general concepts default to high similarity weight (`0.9`) and low freshness weight (`0.1`).
  - *Overrides*: Allowed users to manually define `query_type`, `half_life`, `similarity_weight`, and `freshness_weight` parameters in the API payload.
- **Candidate Retrieval + Re-ranking Design**: 
  - To preserve database performance, the backend uses PostgreSQL pgvector and the HNSW index to retrieve a larger candidate set (top 50 candidates by cosine similarity).
  - It then computes the exponential decay freshness score and the final hybrid score in Python, bypassing PostgreSQL sequential scans and maintaining speed.
- **Freshness Decay Formula**: Uses a standard exponential time-decay model:
  \[\text{Freshness} = e^{-\lambda \cdot t}\]
  where:
  - \(t\) is the age of the document in seconds.
  - \(\lambda = \frac{\ln(2)}{T_{1/2}}\) (\(T_{1/2}\) represents the half-life parameter in seconds, defaulting to 1 hour).
- **Hybrid Score Formula**: 
  \[\text{HybridScore} = (w_{sim} \cdot \text{Similarity}) + (w_{fresh} \cdot \text{Freshness})\]

### Verification Results
- Tested a conceptual query ("actions"): Auto-detected `CONCEPTUAL` with \(w_{sim} = 0.9\) and \(w_{fresh} = 0.1\). Documents were sorted primarily by similarity.
- Tested a latest-activity query ("latest actions"): Auto-detected `LATEST` with \(w_{sim} = 0.4\) and \(w_{fresh} = 0.6\). 
  - *Re-ranking confirmed*: A newer event (1 minute old, similarity 0.34) ranked higher than an older event (4 minutes old, similarity 0.38) due to the freshness weight boost.
  - Output returned explicit breakdown parameters (`similarity_score`, `freshness_score`, `hybrid_score`) for each evidence item.

We are ready to move on to **Step 6: WebSocket /subscribe endpoint + live re-evaluation trigger + cache invalidation logic**.





