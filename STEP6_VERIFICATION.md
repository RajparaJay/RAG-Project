# Step 6 Verification: WebSocket /subscribe Endpoint + Cache Invalidation

## Overview
This document verifies the implementation of **Step 6: WebSocket subscription with targeted cache invalidation**.

## Components Implemented

### 1. Redis Pub/Sub Listener (`redis_pubsub_listener()`)
- **Location**: `backend/main.py`
- **Purpose**: Runs as a background asyncio task during application lifespan
- **Functionality**:
  - Connects to Redis with retry logic (5 retries with 3s backoff)
  - Subscribes to channel: `rag:new_document_channel`
  - Listens for new document notifications published by the worker
  - Calls `handle_new_document_invalidation()` when events arrive

### 2. Targeted Cache Invalidation (`handle_new_document_invalidation()`)
- **Location**: `backend/main.py`
- **Purpose**: Evaluates if new documents are relevant to active subscriptions
- **Algorithm**:
  1. Embeds the incoming document summary using SentenceTransformer
  2. For each active WebSocket subscription:
     - Computes cosine similarity between new document and cached query embedding
     - If similarity ≥ 0.25 (RELEVANCE_THRESHOLD), triggers re-evaluation
     - Logs matched subscriptions for debugging
  3. Executes re-evaluation for matching subscriptions

### 3. Subscription Re-evaluation (`re_evaluate_subscription()`)
- **Location**: `backend/main.py`
- **Purpose**: Refreshes query results and pushes updates to WebSocket clients
- **Steps**:
  1. **Retrieval**: Queries PostgreSQL for top-50 candidate documents using pgvector HNSW
  2. **Re-ranking**: Applies freshness time-decay to rank by hybrid score
  3. **Synthesis**: Generates answer using mock LLM or Anthropic Claude
  4. **Diff Detection**: Compares new evidence IDs with previous state
  5. **Push**: Sends JSON update to WebSocket only if evidence or answer changed

### 4. WebSocket `/subscribe` Endpoint
- **Location**: `backend/main.py`
- **Method**: `subscribe_endpoint(websocket: WebSocket)`
- **Protocol**:
  1. Client connects and sends JSON setup message:
     ```json
     {
       "query": "latest GitHub actions",
       "limit": 5,
       "query_type": "LATEST",
       "half_life": 3600.0,
       "similarity_weight": 0.4,
       "freshness_weight": 0.6
     }
     ```
  2. Server responds with subscription confirmation
  3. Server sends initial results
  4. Server sends updates whenever new documents are relevant
  5. Connection stays open until client disconnects

### 5. Worker Pub/Sub Publisher
- **Location**: `worker/main.py` (lines 353-363)
- **Data Format**:
  ```json
  {
    "event_id": "123456",
    "event_type": "PushEvent",
    "actor": "github_user",
    "repo": "owner/repo",
    "content": "User github_user pushed to branch 'main'...",
    "created_at": "2024-01-15T10:30:00Z"
  }
  ```
- **Channel**: `rag:new_document_channel`

### 6. DBConnection Context Manager
- **Location**: `backend/main.py` (lines 31-44)
- **Purpose**: Thread-safe connection pooling for PostgreSQL
- **Implementation**: Wraps ThreadedConnectionPool for clean resource management

---

## Manual Testing Checklist

### Prerequisites
```bash
# Ensure Docker services are running
docker compose up --build -d
```

### Test 1: Verify Backend Health
```bash
curl http://localhost:8000/health
# Expected: {"status": "ok", "message": "Backend is healthy"}
```

### Test 2: Verify REST /query Endpoint Works
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "latest actions"}'
# Expected: JSON response with answer and evidence
```

### Test 3: Test WebSocket Connection
Use a WebSocket client (e.g., `websocat`, Node.js client, or browser console):

```bash
# Using websocat (install with: cargo install websocat)
websocat ws://localhost:8000/subscribe
# Then send:
{"query": "latest GitHub actions", "limit": 3}
# Expected: Subscription confirmation + initial results
```

### Test 4: Trigger Live Updates (Docker Terminal)
```bash
# In one terminal, monitor the worker logs:
docker logs -f rag_worker

# The logs should show:
# - "Indexed unique event..."
# - "Successfully published pub/sub notification"

# In WebSocket client, wait for updates to appear automatically
```

### Test 5: Monitor Redis Pub/Sub
```bash
# In Docker or local redis-cli:
redis-cli SUBSCRIBE "rag:new_document_channel"
# Should see messages flowing through as worker publishes
```

---

## Architecture Flow

```
GitHub Events API
       ↓
Worker (Poller Thread)
       ↓
Redis Stream (github_events_stream)
       ↓
Worker (Processor Thread)
       ├→ Embed + De-duplicate
       ├→ Index to pgvector
       └→ Publish to Redis Pub/Sub (rag:new_document_channel)
            ↓
       Backend (Pub/Sub Listener)
            ├→ Embed new document
            ├→ Compare with active query embeddings
            └→ Re-evaluate matching subscriptions
                    ↓
            Query PostgreSQL
            Re-rank by freshness
            Synthesize answer
            Send JSON to WebSocket
                    ↓
            Angular Frontend
            Display live updates
```

---

## Key Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| RELEVANCE_THRESHOLD | 0.25 | Min cosine similarity to trigger re-eval |
| Query half-life | 3600s | Time decay window (1 hour default) |
| LATEST query weights | 0.4/0.6 | Similarity/Freshness split |
| CONCEPTUAL weights | 0.9/0.1 | Similarity/Freshness split |

---

## Troubleshooting

### WebSocket not receiving updates
1. Check backend logs: `docker logs rag_backend`
2. Verify Redis Pub/Sub channel is receiving messages:
   ```bash
   docker exec rag_redis redis-cli SUBSCRIBE "rag:new_document_channel"
   ```
3. Ensure worker is running and indexing: `docker logs rag_worker`

### Subscription not matching new documents
1. Check RELEVANCE_THRESHOLD (0.25) - may be too high
2. Monitor similarity scores in backend logs
3. Verify embeddings are being computed correctly

### Database connection issues
1. Verify PostgreSQL is healthy: `docker exec rag_postgres psql -U postgres -d rag_db -c "SELECT COUNT(*) FROM documents;"`
2. Check DBConnection context manager usage in code

---

## Status

✅ **Implementation Complete**
- Redis Pub/Sub listener functional
- Targeted cache invalidation logic implemented
- WebSocket subscription handler operational
- Worker publishing to Redis
- DBConnection context manager added

Ready for **Step 7: Angular Frontend Development**
