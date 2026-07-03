import time
import json
import logging
import httpx
import redis
import psycopg2
import threading
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s"
)
logger = logging.getLogger("worker")

def get_redis_client():
    """Attempts to connect to Redis with retries."""
    retries = 5
    client = None
    while retries > 0:
        try:
            client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=0,
                decode_responses=True
            )
            client.ping()
            return client
        except redis.ConnectionError as e:
            logger.warning(f"Redis connection failed: {e}. Retrying in 3 seconds... ({retries} left)")
            time.sleep(3)
            retries -= 1
    
    logger.error("Could not connect to Redis. Exiting.")
    raise SystemExit("Redis connection error.")

def init_db():
    """Initializes PostgreSQL with the pgvector extension and the documents schema."""
    db_url = f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    retries = 10
    conn = None
    while retries > 0:
        try:
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            cursor = conn.cursor()
            
            # Enable the pgvector extension
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
            # Create documents table with unique event_id constraint
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    event_id VARCHAR(50) UNIQUE NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    actor VARCHAR(100) NOT NULL,
                    repo VARCHAR(150) NOT NULL,
                    content TEXT NOT NULL,
                    embedding VECTOR(384) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    raw_payload JSONB,
                    inserted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Create HNSW index for cosine similarity searches
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS documents_embedding_cosine_idx 
                ON documents USING hnsw (embedding vector_cosine_ops);
            """)
            
            logger.info("Database initialized successfully with pgvector and HNSW index.")
            conn.close()
            return
        except Exception as e:
            logger.warning(f"Database connection failed: {e}. Retrying in 3 seconds... ({retries} left)")
            time.sleep(3)
            retries -= 1
            
    raise SystemExit("Could not connect to database.")

def normalize_event(event: dict) -> dict:
    """Normalizes raw GitHub events into a structured document for embedding."""
    event_id = event.get("id")
    event_type = event.get("type", "UnknownEvent")
    actor = event.get("actor", {}).get("login", "unknown_user")
    repo = event.get("repo", {}).get("name", "unknown_repo")
    created_at = event.get("created_at")
    payload = event.get("payload", {})
    
    summary = ""
    
    if event_type == "PushEvent":
        commits = payload.get("commits", [])
        commit_messages = [c.get("message", "").strip() for c in commits if c.get("message")]
        ref = payload.get("ref", "").replace("refs/heads/", "")
        summary = f"User {actor} pushed to branch '{ref}' in repository {repo}. "
        if commit_messages:
            summary += f"Commits: {'; '.join(commit_messages)}"
        else:
            summary += "No commit details available."
            
    elif event_type == "PullRequestEvent":
        action = payload.get("action", "interacted with")
        pr = payload.get("pull_request", {})
        pr_title = pr.get("title", "")
        pr_body = pr.get("body", "") or "No description provided."
        summary = f"User {actor} {action} pull request '{pr_title}' in repository {repo}. Description: {pr_body}"
        
    elif event_type == "IssuesEvent":
        action = payload.get("action", "interacted with")
        issue = payload.get("issue", {})
        issue_title = issue.get("title", "")
        issue_body = issue.get("body", "") or "No description provided."
        summary = f"User {actor} {action} issue '{issue_title}' in repository {repo}. Description: {issue_body}"
        
    elif event_type == "IssueCommentEvent":
        action = payload.get("action", "created")
        issue = payload.get("issue", {})
        issue_title = issue.get("title", "")
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")
        summary = f"User {actor} {action} comment on issue '{issue_title}' in repository {repo}. Comment: {comment_body}"
        
    elif event_type == "PullRequestReviewCommentEvent":
        action = payload.get("action", "created")
        pr = payload.get("pull_request", {})
        pr_title = pr.get("title", "")
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")
        summary = f"User {actor} {action} review comment on pull request '{pr_title}' in repository {repo}. Comment: {comment_body}"
        
    elif event_type == "WatchEvent":
        summary = f"User {actor} starred repository {repo}."
        
    elif event_type == "ForkEvent":
        forkee = payload.get("forkee", {}).get("full_name", "unknown_forkee")
        summary = f"User {actor} forked repository {repo} to {forkee}."
        
    else:
        summary = f"User {actor} triggered {event_type} in repository {repo}."
        
    return {
        "event_id": event_id,
        "event_type": event_type,
        "actor": actor,
        "repo": repo,
        "created_at": created_at,
        "summary": summary,
        "raw_payload": json.dumps(payload)
    }

def poll_github_events_loop():
    """Loop to poll GitHub API and push events to Redis Stream."""
    logger.info("Starting Poller Loop thread...")
    r = get_redis_client()
    
    headers = {
        "User-Agent": "RealTimeRAGWorker/1.0",
        "Accept": "application/vnd.github.v3+json",
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"
        logger.info("Configured GitHub poller with token.")
    
    processed_events_key = "rag:processed_event_ids"
    try:
        local_processed_ids = set(r.smembers(processed_events_key))
        logger.info(f"Poller loaded {len(local_processed_ids)} historical event IDs.")
    except Exception as e:
        logger.error(f"Failed to load processed IDs: {e}")
        local_processed_ids = set()

    poll_interval = settings.POLL_INTERVAL
    client = httpx.Client(headers=headers, timeout=10.0)

    while True:
        try:
            logger.info("Polling GitHub Events...")
            response = client.get("https://api.github.com/events")
            
            rate_remaining = response.headers.get("X-RateLimit-Remaining")
            rate_reset = response.headers.get("X-RateLimit-Reset")
            poll_interval_header = response.headers.get("X-Poll-Interval")
            
            if poll_interval_header:
                try:
                    poll_interval = max(int(poll_interval_header), settings.POLL_INTERVAL)
                except ValueError:
                    pass
            
            if response.status_code == 200:
                events = response.json()
                if not isinstance(events, list):
                    time.sleep(poll_interval)
                    continue
                
                new_events_count = 0
                for event in reversed(events):
                    event_id = event.get("id")
                    if not event_id or event_id in local_processed_ids:
                        continue
                    
                    try:
                        normalized = normalize_event(event)
                        r.xadd(
                            name=settings.STREAM_NAME,
                            fields={
                                "event_id": event_id,
                                "event_type": normalized["event_type"],
                                "data": json.dumps(normalized)
                            },
                            maxlen=settings.STREAM_MAX_LEN,
                            approximate=True
                        )
                        r.sadd(processed_events_key, event_id)
                        local_processed_ids.add(event_id)
                        new_events_count += 1
                    except Exception as e:
                        logger.error(f"Error enqueuing event {event_id}: {e}")
                
                if new_events_count > 0:
                    logger.info(f"Enqueued {new_events_count} new events to stream.")
                
                # Cleanup event ID cache if it grows too large
                try:
                    if len(local_processed_ids) > 10000:
                        logger.info("Cleaning up historical event ID cache in Redis...")
                        r.delete(processed_events_key)
                        local_processed_list = list(local_processed_ids)
                        local_processed_ids = set(local_processed_list[-5000:])
                        if local_processed_ids:
                            r.sadd(processed_events_key, *local_processed_ids)
                except Exception as ce:
                    logger.warning(f"Error cleaning cache: {ce}")
                    
            elif response.status_code == 403:
                logger.warning(f"Rate limited (403). Remaining limit: {rate_remaining}.")
                if rate_reset:
                    try:
                        sleep_time = max(0.0, float(rate_reset) - time.time()) + 5.0
                        logger.warning(f"Sleeping for {sleep_time:.1f}s until reset.")
                        time.sleep(sleep_time)
                        continue
                    except ValueError:
                        pass
                time.sleep(120)
            else:
                logger.error(f"Failed to poll: {response.status_code} - {response.text}")
                time.sleep(poll_interval * 2)
                
        except httpx.NetworkError as ne:
            logger.error(f"Network error in poller: {ne}. Retrying in 30s.")
            time.sleep(30)
        except Exception as e:
            logger.error(f"Error in poller thread: {e}", exc_info=True)
            time.sleep(poll_interval)
            
        time.sleep(poll_interval)

def process_stream_events_loop():
    """Loop to consume events from Redis Stream, embed, deduplicate, and index to pgvector."""
    logger.info("Starting Processor (Main Thread)...")
    
    # Establish DB connection
    db_url = f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    register_vector(conn)
    cursor = conn.cursor()
    
    # Establish Redis connection
    r = get_redis_client()
    
    # Load embedding model
    logger.info("Loading sentence-transformers (all-MiniLM-L6-v2) on CPU...")
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    logger.info("Model loaded successfully!")
    
    last_id_key = "rag:last_processed_stream_id"
    last_id = r.get(last_id_key) or "0"
    logger.info(f"Processor standing by. Starting from stream position: {last_id}")
    
    while True:
        try:
            # Read batch of messages from stream (block up to 2 seconds)
            streams_data = r.xread(streams={settings.STREAM_NAME: last_id}, count=10, block=2000)
            if not streams_data:
                continue
                
            for _, messages in streams_data:
                for msg_id, fields in messages:
                    event_id = fields.get("event_id")
                    event_type = fields.get("event_type")
                    data_str = fields.get("data")
                    
                    if not data_str:
                        # Skip corrupted entry, advance pointer
                        last_id = msg_id
                        r.set(last_id_key, last_id)
                        continue
                        
                    event_data = json.loads(data_str)
                    summary = event_data.get("summary", "")
                    actor = event_data.get("actor", "unknown")
                    repo = event_data.get("repo", "unknown")
                    created_at = event_data.get("created_at")
                    raw_payload = event_data.get("raw_payload", "{}")
                    
                    # 1. Exact DB check (skip if already in pgvector database)
                    cursor.execute("SELECT 1 FROM documents WHERE event_id = %s", (event_id,))
                    if cursor.fetchone():
                        last_id = msg_id
                        r.set(last_id_key, last_id)
                        continue
                    
                    # Compute embedding list (384 float dimensions)
                    embedding = model.encode(summary).tolist()
                    
                    # 2. Near-Duplicate Check using pgvector cosine distance
                    # Find similar events from the same actor, repo, and event type in the last 10 minutes.
                    # Cosine distance is calculated as (embedding <=> vector).
                    # Cosine similarity = 1 - Cosine distance.
                    cursor.execute("""
                        SELECT 1 - (embedding <=> %s::vector) as similarity
                        FROM documents
                        WHERE actor = %s AND repo = %s AND event_type = %s
                          AND created_at > %s::timestamptz - INTERVAL '10 minutes'
                        ORDER BY similarity DESC
                        LIMIT 1;
                    """, (embedding, actor, repo, event_type, created_at))
                    
                    row = cursor.fetchone()
                    if row and row[0] > 0.95:
                        logger.info(f"Skipping near-duplicate event {event_id} ({event_type}) from {actor} on {repo} (Similarity: {row[0]:.4f})")
                        last_id = msg_id
                        r.set(last_id_key, last_id)
                        continue
                    
                    # 3. Insert unique event into pgvector table
                    cursor.execute("""
                        INSERT INTO documents (event_id, event_type, actor, repo, content, embedding, created_at, raw_payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_id) DO NOTHING;
                    """, (event_id, event_type, actor, repo, summary, embedding, created_at, raw_payload))
                    
                    logger.info(f"Indexed unique event: {event_id} | Type: {event_type} | Actor: {actor} | Repo: {repo}")
                    
                    # Keep track of insertion for subscriptions in Step 6
                    # (We will publish new document notification to Redis Pub/Sub)
                    try:
                        r.publish("rag:new_document_channel", json.dumps({
                            "event_id": event_id,
                            "event_type": event_type,
                            "actor": actor,
                            "repo": repo,
                            "content": summary,
                            "created_at": created_at
                        }))
                    except Exception as pe:
                        logger.warning(f"Failed to publish pub/sub notification: {pe}")
                    
                    # Advance offset
                    last_id = msg_id
                    r.set(last_id_key, last_id)
                    
        except Exception as e:
            logger.error(f"Error in processor loop: {e}", exc_info=True)
            time.sleep(5)

def main():
    # Step 1: Initialize Database schema
    init_db()
    
    # Step 2: Start GitHub Ingestion Poller in a daemon thread
    poller_thread = threading.Thread(
        target=poll_github_events_loop, 
        daemon=True, 
        name="PollerThread"
    )
    poller_thread.start()
    
    # Step 3: Run the processor loop in the main thread
    process_stream_events_loop()

if __name__ == "__main__":
    main()
