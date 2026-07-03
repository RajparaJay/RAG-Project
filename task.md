# Tasks - Real-Time RAG over Streaming Data

- [x] **Step 1: Project structure + Docker Compose setup**
  - [x] Create `.env.example` and initial `.env` files
  - [x] Create `docker-compose.yml`
  - [x] Scaffold FastAPI backend (Dockerfile, requirements.txt, main.py skeleton)
  - [x] Scaffold Ingestion worker (Dockerfile, requirements.txt, main.py skeleton)
  - [x] Scaffold Angular frontend locally
  - [x] Configure Angular Dockerfile for local development
  - [x] Verify setup via Docker Compose


- [x] **Step 2: Ingestion worker**
  - [x] Write client to poll the GitHub Events API (`https://api.github.com/events`)
  - [x] Implement event filtering and normalization (extract actor, repo, event type, payload summary, timestamp)
  - [x] Implement pushing normalized events to a Redis Stream (`github_events_stream`)
  - [x] Handle unauthenticated rate limits (60/hr) with graceful backoff or token loading (if configured)
  - [x] Add basic error handling for malformed JSON and network failures without crashing



- [x] **Step 3: Embedding + indexing pipeline**
  - [x] Implement Redis Stream consumer in the worker
  - [x] Set up PostgreSQL connection in the worker and initialize database (enable pgvector extension and create `documents` table)
  - [x] Implement near-duplicate detection (e.g. SHA256 of event keys or checking duplicate event IDs) before embedding
  - [x] Implement local embedding generation using `all-MiniLM-L6-v2` in the worker
  - [x] Insert embedded documents and metadata (timestamp, event_id, event_type, payload) into pgvector



- [x] **Step 4: Basic REST /query endpoint**
  - [x] Set up PostgreSQL pgvector connection in the FastAPI backend
  - [x] Implement standard vector similarity search in the backend (retrieving top-K relevant events for a query)
  - [x] Implement LLM integration (Anthropic Claude API + local Mock LLM fallback) to synthesize answers from retrieved documents
  - [x] Create `/query` REST endpoint returning synthesized answer and evidence list
  - [x] Verify the RAG loop works end-to-end via FastAPI swagger docs



- [x] **Step 5: Freshness-aware ranking + query-type classification**
  - [x] Implement query classification (detecting "latest activity" vs "general/conceptual" using heuristic/LLM)
  - [x] Implement postgres/python hybrid ranking combining cosine similarity with exponential time-decay
  - [x] Expose parameter tuning for decay curve (half-life, similarity weight, freshness weight)
  - [x] Update `/query` REST endpoint to use the freshness-aware ranking



- [x] **Step 6: WebSocket /subscribe endpoint + cache invalidation**
  - [x] Create WebSocket subscription manager in FastAPI backend
  - [x] Set up Redis Pub/Sub listener in backend to detect new document insertions
  - [x] Implement targeted cache invalidation: check if new documents are relevant to any active query subscriptions using a similarity threshold
  - [x] Trigger re-evaluation of relevant active subscriptions and push updated answer + evidence diff to WebSocket clients


- [x] **Step 7: Angular frontend development**
  - [x] Create modern, sleek CSS styling theme (dark mode, glassmorphism, responsive grid)
  - [x] Build Search input page with live options to run standard query or subscribe
  - [x] Build dynamic live-updating answer card (displays synthesized answers with smooth CSS transitions)
  - [x] Build evidence timeline component (showing retrieved GitHub events, actor, repo, type, and freshness decay score)
  - [x] Connect frontend to FastAPI REST and WebSocket endpoints

- [x] **Step 8: Load test + evaluation set**
  - [x] Build a test script to simulate bursty stream events and concurrent query clients
  - [x] Create a local evaluation set of 10-15 queries spanning latest activity vs conceptual questions
  - [x] Run evaluation and generate a report comparing standard cosine similarity vs freshness-aware ranking results

