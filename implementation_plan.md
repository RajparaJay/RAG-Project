# Implementation Plan - Real-Time RAG over Streaming Data

This plan outlines the scaffolding of the project structure and Docker Compose services for a Real-Time Retrieval-Augmented Generation (RAG) system using GitHub Events as a live data stream.

## Environment Context

- **GitHub Token**: Optional. System will poll without authentication by default, and use a `GITHUB_TOKEN` from `.env` if provided.
- **Anthropic API Key**: None initially. We will write the LLM integration but provide a toggle/fallback to a Mock LLM generator so the backend works end-to-end without an API key.
- **Node.js/NPM**: Installed locally. We will scaffold the Angular app using `npx @angular/cli`.
- **Model Hardware**: CPU execution for the embedding model (`all-MiniLM-L6-v2`).


---

## Proposed Changes

We will set up a clean, modular structure. Below is the proposed layout:

```
rag-project/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   └── app/
│       ├── __init__.py
│       ├── config.py
│       └── api/
├── worker/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
└── frontend/
    ├── Dockerfile
    └── (Angular project files)
```

### Infrastructure

#### [NEW] [docker-compose.yml](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/docker-compose.yml)
Define services:
- **`postgres`**: `pgvector/pgvector:pg16` image. Persisted to volume.
- **`redis`**: Standard `redis:7-alpine` (running Redis Streams).
- **`backend`**: FastAPI container, port 8000.
- **`worker`**: Python container running the ingestion and vector processing tasks.
- **`frontend`**: Angular app in development mode, port 4200.

#### [NEW] [.env.example](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/.env.example)
Template file for configuring environment variables:
- Database credentials
- Redis URL
- GitHub API Token
- Anthropic API Key

### Backend Skeleton

#### [NEW] [backend/requirements.txt](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/requirements.txt)
FastAPI, Uvicorn, psycopg2-binary, redis, httpx, pydantic, anthropic, sentence-transformers, etc.

#### [NEW] [backend/Dockerfile](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/Dockerfile)
Multi-stage build or development build copying requirements and mounting code for live-reload.

#### [NEW] [backend/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/backend/main.py)
Minimal FastAPI application defining `/health` and skeletal routes for `/query` and `/subscribe`.

### Ingestion & Processing Worker Skeleton

#### [NEW] [worker/requirements.txt](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/requirements.txt)
redis, httpx, sentence-transformers, psycopg2-binary, etc.

#### [NEW] [worker/Dockerfile](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/Dockerfile)
Simple Python runner.

#### [NEW] [worker/main.py](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/worker/main.py)
A skeletal Python loop that will poll the GitHub API and push events to Redis.

### Frontend Skeleton

#### [NEW] [frontend/Dockerfile](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/frontend/Dockerfile)
Node.js build to serve Angular dev server.

#### [NEW] [frontend/](file:///d:/Project/Demo/GitHub/RAG%20PROJECT/frontend)
Scaffolded Angular application structure.

---

## Verification Plan

### Manual Verification
1. Run `docker compose up --build` and verify all containers start up and communicate:
   - Check pgvector connection.
   - Check Redis connectivity.
   - Access FastAPI Swagger docs at `http://localhost:8000/docs`.
   - Access Angular skeleton home page at `http://localhost:4200`.
