import logging
import json
import math
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
import anthropic
import redis
import numpy as np
import httpx
from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("backend")

# Global variables to hold model, database pool, and active subscriptions
embedding_model = None
db_pool = None
active_subscriptions = {}  # dict: WebSocket -> subscription metadata dict

class DBConnection:
    """Context manager for managing database connections from the thread pool."""
    def __init__(self, pool):
        self.pool = pool
        self.conn = None
    
    def __enter__(self):
        self.conn = self.pool.getconn()
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.pool.putconn(self.conn)
        return False

async def redis_pubsub_listener():
    """Background task listening to Redis Pub/Sub for new document notifications."""
    logger.info("Initializing Redis Pub/Sub Listener...")
    retries = 5
    r = None
    while retries > 0:
        try:
            r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True)
            r.ping()
            break
        except redis.ConnectionError as e:
            logger.warning(f"Pub/Sub Redis connection failed: {e}. Retrying in 3s... ({retries} left)")
            await asyncio.sleep(3)
            retries -= 1
            
    if not r:
        logger.error("Pub/Sub listener could not connect to Redis. Disabling live subscriptions.")
        return

    pubsub = r.pubsub()
    # Subscribe to new document updates published by the worker
    await asyncio.to_thread(pubsub.subscribe, "rag:new_document_channel")
    logger.info("Subscribed to Redis channel 'rag:new_document_channel' for targeted cache invalidation.")
    
    while True:
        try:
            # Check for message without blocking indefinitely (allow loop to yield/shutdown cleanly)
            message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
            if not message:
                await asyncio.sleep(0.1)
                continue
                
            data_str = message.get("data")
            if not data_str:
                continue
                
            event_data = json.loads(data_str)
            summary = event_data.get("content", "")
            
            # Handle targeted cache invalidation
            await handle_new_document_invalidation(event_data, summary)
            
        except asyncio.CancelledError:
            logger.info("Redis Pub/Sub listener task cancelled. Shutting down.")
            break
        except Exception as e:
            logger.error(f"Error in Redis Pub/Sub listener: {e}", exc_info=True)
            await asyncio.sleep(2)

async def handle_new_document_invalidation(event_data: dict, summary: str):
    """Calculates cosine similarity between incoming document and queries to trigger targeted re-evaluations."""
    global active_subscriptions, embedding_model
    if not active_subscriptions or not embedding_model:
        return
        
    # 1. Embed the new document text once in-process
    try:
        # Run embedding in a thread pool to avoid blocking the asyncio event loop
        doc_vector = await asyncio.to_thread(embedding_model.encode, summary)
    except Exception as e:
        logger.error(f"Failed to embed new document for targeted invalidation: {e}")
        return
        
    doc_vector_np = np.array(doc_vector)
    
    # 2. Match against active queries using fast local NumPy vector dot product
    for ws, sub in list(active_subscriptions.items()):
        try:
            query_vector_np = np.array(sub["query_vector"])
            # Sentence-transformer embeddings are normalized, so cosine similarity is the dot product
            similarity = float(np.dot(doc_vector_np, query_vector_np))
            
            # Define relevance threshold for targeted cache invalidation
            RELEVANCE_THRESHOLD = 0.25
            if similarity >= RELEVANCE_THRESHOLD:
                logger.info(
                    f"Targeted Cache Invalidation triggered! "
                    f"New event {event_data['event_id']} is relevant to query '{sub['query']}' (Sim: {similarity:.4f}). "
                    f"Re-evaluating client subscription..."
                )
                # Run RAG re-evaluation and push live updates
                await re_evaluate_subscription(ws, sub)
            else:
                logger.debug(f"Skipping update for query '{sub['query']}' - event not relevant (Sim: {similarity:.4f})")
        except Exception as se:
            logger.error(f"Error checking relevance for query '{sub['query']}': {se}")

async def re_evaluate_subscription(websocket: WebSocket, sub: dict):
    """Executes search retrieval, re-ranking, and response generation, pushing results if evidence changed."""
    global db_pool
    
    query = sub["query"]
    limit = sub["limit"]
    query_type = sub["query_type"]
    half_life = sub["half_life"]
    sim_weight = sub["similarity_weight"]
    fresh_weight = sub["freshness_weight"]
    query_vector = sub["query_vector"]
    last_evidence_ids = sub["last_evidence_ids"]
    
    # 1. Fetch candidates from PostgreSQL using pgvector HNSW search
    candidate_limit = max(50, limit * 3)
    candidates = []
    try:
        with DBConnection(db_pool) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, event_id, event_type, actor, repo, content, created_at,
                           1 - (embedding <=> %s::vector) as similarity
                    FROM documents
                    ORDER BY similarity DESC
                    LIMIT %s;
                """, (query_vector, candidate_limit))
                
                rows = cursor.fetchall()
                for row in rows:
                    candidates.append({
                        "id": row[0],
                        "event_id": row[1],
                        "event_type": row[2],
                        "actor": row[3],
                        "repo": row[4],
                        "content": row[5],
                        "created_at": row[6],
                        "similarity_score": float(row[7])
                    })
    except Exception as e:
        logger.error(f"DB query in re-evaluation failed: {e}")
        return
        
    # 2. Re-rank with freshness time-decay in Python
    decay_rate = math.log(2.0) / half_life
    current_time = datetime.now(timezone.utc)
    
    ranked = []
    for doc in candidates:
        created_at_dt = doc["created_at"]
        if created_at_dt.tzinfo is None:
            created_at_dt = created_at_dt.replace(tzinfo=timezone.utc)
            
        age_seconds = max(0.0, (current_time - created_at_dt).total_seconds())
        freshness_score = math.exp(-decay_rate * age_seconds)
        hybrid_score = (sim_weight * doc["similarity_score"]) + (fresh_weight * freshness_score)
        
        ranked.append({
            **doc,
            "created_at": str(doc["created_at"]),
            "freshness_score": freshness_score,
            "hybrid_score": hybrid_score
        })
        
    ranked.sort(key=lambda x: x["hybrid_score"], reverse=True)
    final_evidence = ranked[:limit]
    
    # 3. Synthesize answer
    if settings.USE_MOCK_LLM:
        answer = mock_llm_synthesize(query, final_evidence)
    elif settings.LLM_PROVIDER.lower() == "ollama":
        answer = call_ollama(query, final_evidence)
    else:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            answer = mock_llm_synthesize(query, final_evidence)
        else:
            answer = call_anthropic_claude(query, final_evidence, api_key)
        
    # 4. Calculate evidence list diff
    new_ids = {doc["event_id"] for doc in final_evidence}
    added_ids = new_ids - last_evidence_ids
    removed_ids = last_evidence_ids - new_ids
    
    added_items = [doc for doc in final_evidence if doc["event_id"] in added_ids]
    
    # 5. Push updates to WebSocket if diff detected
    if added_ids or removed_ids or sub.get("last_answer") != answer:
        sub["last_evidence_ids"] = new_ids
        sub["last_answer"] = answer
        
        logger.info(f"Pushing live update to WS client for query '{query}' (Added: {len(added_ids)} items)")
        try:
            await websocket.send_json({
                "type": "update",
                "query": query,
                "query_type_detected": query_type,
                "similarity_weight": sim_weight,
                "freshness_weight": fresh_weight,
                "answer": answer,
                "evidence": final_evidence,
                "diff": {
                    "added": added_items,
                    "removed_ids": list(removed_ids)
                }
            })
        except Exception as we:
            logger.warning(f"Failed to push update (client disconnected): {we}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Context manager for startup and shutdown events."""
    global embedding_model, db_pool
    
    # 1. Initialize PostgreSQL Connection Pool
    logger.info("Initializing PostgreSQL Connection Pool...")
    db_url = f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    try:
        db_pool = ThreadedConnectionPool(1, 10, dsn=db_url)
        conn = db_pool.getconn()
        register_vector(conn)
        db_pool.putconn(conn)
        logger.info("PostgreSQL pool initialized successfully!")
    except Exception as e:
        logger.critical(f"Failed to initialize PostgreSQL pool: {e}")
        raise SystemExit(e)
        
    # 2. Load SentenceTransformer model
    logger.info("Loading SentenceTransformer model (all-MiniLM-L6-v2) on CPU...")
    try:
        embedding_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        logger.info("Embedding model loaded successfully!")
    except Exception as e:
        logger.critical(f"Failed to load embedding model: {e}")
        raise SystemExit(e)
        
    # 3. Spawn Redis Pub/Sub Listener background task
    pubsub_task = asyncio.create_task(redis_pubsub_listener())
    
    yield
    
    # Shutdown events
    logger.info("Lifespan shutting down: cancelling Pub/Sub listener...")
    pubsub_task.cancel()
    try:
        await pubsub_task
    except asyncio.CancelledError:
        pass
        
    if db_pool:
        logger.info("Closing PostgreSQL Connection Pool...")
        db_pool.closeall()

app = FastAPI(title="Real-Time Streaming RAG Backend", lifespan=lifespan)

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for request/response
class QueryRequest(BaseModel):
    query: str
    limit: int = 5
    query_type: str | None = Field(default=None, description="LATEST, CONCEPTUAL, or None (auto-detect)")
    half_life: float = Field(default=3600.0, description="Decay half-life in seconds (default 1 hour)")
    similarity_weight: float | None = Field(default=None, description="Weight for similarity score (0.0 to 1.0)")
    freshness_weight: float | None = Field(default=None, description="Weight for freshness decay factor (0.0 to 1.0)")

class EvidenceItem(BaseModel):
    id: int
    event_id: str
    event_type: str
    actor: str
    repo: str
    content: str
    created_at: str
    similarity_score: float
    freshness_score: float
    hybrid_score: float

class QueryResponse(BaseModel):
    query: str
    query_type_detected: str
    similarity_weight: float
    freshness_weight: float
    answer: str
    evidence: list[EvidenceItem]

def classify_query_type(query: str) -> str:
    """Classifies a query into LATEST or CONCEPTUAL using keywords."""
    query_lower = query.lower()
    latest_keywords = [
        "latest", "recent", "now", "today", "newest", "current", "active", 
        "last hour", "last minute", "recently", "just", "happening", "live",
        "who pushed", "what pushed", "last commit", "last pr", "latest activity",
        "what is new", "what happened"
    ]
    for kw in latest_keywords:
        if kw in query_lower:
            return "LATEST"
    return "CONCEPTUAL"

def mock_llm_synthesize(query: str, evidence: list[dict]) -> str:
    """Helper to generate a rich markdown summary answer using retrieved evidence."""
    if not evidence:
        return f"I couldn't find any relevant events in the database for the query: '{query}'."
    
    summary_lines = []
    for doc in evidence:
        actor = doc["actor"]
        repo = doc["repo"]
        event_type = doc["event_type"]
        content = doc["content"]
        hybrid = doc["hybrid_score"]
        sim = doc["similarity_score"]
        fresh = doc["freshness_score"]
        summary_lines.append(
            f"- **[{event_type}]** {actor} in `{repo}`: *{content}*\n"
            f"  *(Scores -> Hybrid: {hybrid:.2f} | Similarity: {sim:.2f} | Freshness: {fresh:.2f})*"
        )
    
    events_list_str = "\n".join(summary_lines)
    
    return f"### Summary Answer (MOCK LLM)\n\n" \
           f"I found {len(evidence)} events matching your query **'{query}'** in the streaming indexing system. " \
           f"Here is a summary of the activity:\n\n" \
           f"Based on the most relevant events:\n" \
           f"{events_list_str}\n\n" \
           f"**Conceptual Synthesis:**\n" \
           f"This is a simulated response synthesizing the above activity. " \
           f"To use real AI synthesis, set `USE_MOCK_LLM=false` and configure `ANTHROPIC_API_KEY` in your `.env` file."

def call_anthropic_claude(query: str, evidence: list[dict], api_key: str) -> str:
    """Helper to synthesize response using Anthropic API."""
    if not api_key:
        raise ValueError("Anthropic API key is not configured.")
        
    client = anthropic.Anthropic(api_key=api_key)
    
    context = ""
    for doc in evidence:
        context += f"Event Type: {doc['event_type']}\nActor: {doc['actor']}\nRepo: {doc['repo']}\nTime: {doc['created_at']}\nContent: {doc['content']}\n---\n"
        
    prompt = f"""You are a real-time streaming RAG assistant. You are analyzing a live stream of GitHub activity.
Here is the retrieved context (relevant events):
{context}

Based on this context, answer the user's query: "{query}"
Keep the answer concise and focus on summarizing the events. If there is no relevant information, explain that you don't see any recent activity on that topic.
"""
    
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1000,
            temperature=0.0,
            system="You are a helpful, professional assistant analyzing live GitHub stream data.",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Anthropic API call failed: {e}")
        return f"Error contacting Anthropic Claude API: {e}. Falling back to evidence list."

def call_ollama(query: str, evidence: list[dict]) -> str:
    """Helper to synthesize response using a local Ollama model."""
    context = ""
    for doc in evidence:
        context += f"Event Type: {doc['event_type']}\nActor: {doc['actor']}\nRepo: {doc['repo']}\nTime: {doc['created_at']}\nContent: {doc['content']}\n---\n"

    prompt = f"""You are a real-time streaming RAG assistant. You are analyzing a live stream of GitHub activity.
Here is the retrieved context (relevant events):
{context}

User query: {query}

Answer concisely using only the context above. Mention important repositories, actors, event types, and timestamps when useful.
"""

    try:
        response = httpx.post(
            f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_gpu": settings.OLLAMA_NUM_GPU
                }
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip() or "Ollama returned an empty response."
    except Exception as e:
        logger.error(f"Ollama API call failed: {e}")
        return f"Error contacting Ollama at {settings.OLLAMA_BASE_URL}: {e}. Falling back to evidence list."

@app.post("/query", response_model=QueryResponse)
async def query_endpoint(req: QueryRequest):
    global embedding_model, db_pool
    if not embedding_model or not db_pool:
        raise HTTPException(status_code=500, detail="Models or database connections not initialized.")
        
    logger.info(f"Processing REST query: '{req.query}'")
    
    # 1. Classify query type
    query_type = req.query_type or classify_query_type(req.query)
    
    # 2. Determine weights
    if query_type == "LATEST":
        sim_weight = 0.4 if req.similarity_weight is None else req.similarity_weight
        fresh_weight = 0.6 if req.freshness_weight is None else req.freshness_weight
    else:  # CONCEPTUAL
        sim_weight = 0.9 if req.similarity_weight is None else req.similarity_weight
        fresh_weight = 0.1 if req.freshness_weight is None else req.freshness_weight
        
    # 3. Embed user query
    try:
        query_vector = embedding_model.encode(req.query).tolist()
    except Exception as e:
        logger.error(f"Failed to generate embedding for query: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate embedding.")
        
    # 4. Retrieve candidate set from pgvector
    candidate_limit = max(50, req.limit * 3)
    candidates = []
    try:
        with DBConnection(db_pool) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, event_id, event_type, actor, repo, content, created_at,
                           1 - (embedding <=> %s::vector) as similarity
                    FROM documents
                    ORDER BY similarity DESC
                    LIMIT %s;
                """, (query_vector, candidate_limit))
                
                rows = cursor.fetchall()
                for row in rows:
                    candidates.append({
                        "id": row[0],
                        "event_id": row[1],
                        "event_type": row[2],
                        "actor": row[3],
                        "repo": row[4],
                        "content": row[5],
                        "created_at": row[6],
                        "similarity_score": float(row[7])
                    })
    except Exception as e:
        logger.error(f"Database query failed: {e}")
        raise HTTPException(status_code=500, detail=f"Database query error: {e}")
        
    # 5. Apply time-decay re-ranking in Python
    decay_rate = math.log(2.0) / req.half_life
    current_time = datetime.now(timezone.utc)
    
    ranked_candidates = []
    for doc in candidates:
        created_at_dt = doc["created_at"]
        if created_at_dt.tzinfo is None:
            created_at_dt = created_at_dt.replace(tzinfo=timezone.utc)
            
        age_seconds = max(0.0, (current_time - created_at_dt).total_seconds())
        freshness_score = math.exp(-decay_rate * age_seconds)
        hybrid_score = (sim_weight * doc["similarity_score"]) + (fresh_weight * freshness_score)
        
        ranked_candidates.append({
            **doc,
            "created_at": str(doc["created_at"]),
            "freshness_score": freshness_score,
            "hybrid_score": hybrid_score
        })
        
    ranked_candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)
    final_evidence = ranked_candidates[:req.limit]
    
    # 6. Synthesize answer
    if settings.USE_MOCK_LLM:
        answer = mock_llm_synthesize(req.query, final_evidence)
    elif settings.LLM_PROVIDER.lower() == "ollama":
        answer = call_ollama(req.query, final_evidence)
    else:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            answer = mock_llm_synthesize(req.query, final_evidence)
        else:
            answer = call_anthropic_claude(req.query, final_evidence, api_key)
        
    return QueryResponse(
        query=req.query,
        query_type_detected=query_type,
        similarity_weight=sim_weight,
        freshness_weight=fresh_weight,
        answer=answer,
        evidence=[EvidenceItem(**item) for item in final_evidence]
    )

@app.websocket("/subscribe")
async def subscribe_endpoint(websocket: WebSocket):
    """WebSocket endpoint allowing clients to subscribe to continuous updates for a specific query."""
    await websocket.accept()
    logger.info("New WebSocket connection established.")
    
    global active_subscriptions, embedding_model
    sub_key = websocket
    
    try:
        # 1. Listen for the initial subscription setup message
        setup_data_str = await websocket.receive_text()
        setup_data = json.loads(setup_data_str)
        
        query = setup_data.get("query")
        if not query:
            await websocket.send_json({"type": "error", "message": "Query parameter is required."})
            await websocket.close()
            return
            
        limit = int(setup_data.get("limit", 5))
        query_type = setup_data.get("query_type")
        half_life = float(setup_data.get("half_life", 3600.0))
        sim_override = setup_data.get("similarity_weight")
        fresh_override = setup_data.get("freshness_weight")
        
        # 2. Auto-detect type and weights
        detected_type = query_type or classify_query_type(query)
        if detected_type == "LATEST":
            sim_weight = 0.4 if sim_override is None else float(sim_override)
            fresh_weight = 0.6 if fresh_override is None else float(fresh_override)
        else:
            sim_weight = 0.9 if sim_override is None else float(sim_override)
            fresh_weight = 0.1 if fresh_override is None else float(fresh_override)
            
        # 3. Generate query embedding and cache it in the subscription
        logger.info(f"Generating query embedding for WebSocket subscription: '{query}'")
        query_vector = embedding_model.encode(query).tolist()
        
        # 4. Save subscription metadata
        active_subscriptions[sub_key] = {
            "query": query,
            "limit": limit,
            "query_type": detected_type,
            "half_life": half_life,
            "similarity_weight": sim_weight,
            "freshness_weight": fresh_weight,
            "query_vector": query_vector,
            "last_evidence_ids": set(),
            "last_answer": ""
        }
        
        # 5. Confirm subscription and run initial re-evaluation to push first result
        await websocket.send_json({
            "type": "subscribed",
            "query": query,
            "message": f"Successfully subscribed to query: '{query}'"
        })
        
        logger.info(f"Registered live subscription for query '{query}'")
        await re_evaluate_subscription(websocket, active_subscriptions[sub_key])
        
        # Keep connection open. Standby for client commands or connection drop.
        while True:
            # We can optionally listen for keep-alive heartbeats or unsubscribes from the client
            client_msg = await websocket.receive_text()
            logger.debug(f"Received WS message from client: {client_msg}")
            
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"Error in WebSocket subscription handler: {e}", exc_info=True)
    finally:
        # Clean up subscription to prevent memory leaks
        if sub_key in active_subscriptions:
            logger.info(f"Unregistering subscription for query '{active_subscriptions[sub_key]['query']}'")
            del active_subscriptions[sub_key]
