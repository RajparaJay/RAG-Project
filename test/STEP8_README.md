# Step 8: Load Testing & Evaluation

## Objective
Test the RAG system under load, comparing REST and WebSocket modes, and evaluating the impact of freshness-aware ranking.

## Test Infrastructure

### Evaluation Queries
- **File**: `evaluation_queries.json`
- **Total Queries**: 12
  - 6 LATEST activity queries (temporal ranking)
  - 6 CONCEPTUAL semantic queries (similarity ranking)
- **Query Categories**:
  - Latest push events
  - Recent pull request activity
  - New issues created
  - Urgent comments
  - Time-based queries
  - Hot/recent activity
  - Authentication patterns
  - ML/AI experiments
  - Database optimization
  - API design
  - Documentation practices
  - Testing strategies

### Load Test Scenarios

#### 1. Single REST Query (Baseline)
- **Concurrency**: 1
- **Duration**: 30 seconds
- **Queries**: 1 (Latest), 7 (Conceptual)
- **Metrics**: Response time, accuracy, ranking quality

#### 2. Concurrent REST Queries (5 clients)
- **Concurrency**: 5
- **Duration**: 60 seconds
- **Queries**: All 10 mixed
- **Metrics**: Throughput, latency percentiles, error rate

#### 3. High Concurrency REST (20 clients)
- **Concurrency**: 20
- **Duration**: 120 seconds
- **Queries**: All 12 queries
- **Metrics**: P99 latency, error rate, throughput under stress

#### 4. Single WebSocket Subscription (Baseline)
- **Concurrency**: 1
- **Duration**: 60 seconds
- **Queries**: 1 (Latest), 7 (Conceptual)
- **Metrics**: Connection time, update latency, message count

#### 5. Concurrent WebSocket Subscriptions (10 clients)
- **Concurrency**: 10
- **Duration**: 120 seconds
- **Queries**: All 10 mixed
- **Metrics**: Connection stability, update latency, message throughput

#### 6. High Concurrency WebSocket (20 clients)
- **Concurrency**: 20
- **Duration**: 180 seconds
- **Queries**: All 12 queries
- **Metrics**: Connection stability, error rate under stress

#### 7. Ranking Comparison: LATEST Queries
- **Mode**: REST only
- **Concurrency**: 5
- **Duration**: 60 seconds
- **Queries**: Queries 1-6 (LATEST type)
- **Focus**: Compare standard cosine similarity vs freshness-aware ranking
- **Metrics**: Top result freshness, result diversity, ranking quality

#### 8. Ranking Comparison: CONCEPTUAL Queries
- **Mode**: REST only
- **Concurrency**: 5
- **Duration**: 60 seconds
- **Queries**: Queries 7-12 (CONCEPTUAL type)
- **Focus**: Verify conceptual queries rely on similarity, not freshness
- **Metrics**: Result relevance, ranking quality

## Metrics Collected

### Per-Query Metrics
- `status_code`: HTTP status (200 = success)
- `response_time_ms`: Latency in milliseconds
- `results_count`: Number of evidence items returned
- `top_result_freshness`: Freshness score of top result (0-1)
- `answer_length`: Characters in synthesized answer
- `error`: Error message if failed
- `timestamp`: When query executed

### Aggregated Metrics
- **Latency**
  - Mean (average response time)
  - P50 (median)
  - P95 (95th percentile)
  - P99 (99th percentile)
  - Min/Max

- **Throughput**
  - Queries per second (QPS)
  - Total queries executed
  - Successful vs failed

- **Quality**
  - Error rate (%)
  - Average results per query
  - Average answer length
  - Average top result freshness

- **Ranking Analysis**
  - Freshness impact on LATEST queries
  - Similarity impact on CONCEPTUAL queries
  - Freshness advantage comparison

## How to Run

### 1. Setup Test Environment
```bash
cd d:\Project\Demo\GitHub\RAG PROJECT

# Ensure backend services running
docker compose up --build -d

# Wait for services to be ready (30 seconds)
timeout /t 30
```

### 2. Verify Backend is Running
```bash
curl http://localhost:8000/health
# Should return: {"status": "healthy"}
```

### 3. Run Load Tests
```bash
cd test

# Install test dependencies
pip install -r requirements.txt

# Run tests
python load_test.py
```

### 4. View Results
Results saved to: `test/load_test_report_YYYYMMDD_HHMMSS.json`

## Expected Results

### Performance Baselines
- **REST Query** (single): 100-300 ms
- **REST Throughput** (5 clients): 15-25 QPS
- **WebSocket Connect**: 50-150 ms
- **WebSocket Updates**: 100-500 ms latency
- **Error Rate**: < 1%

### Ranking Analysis
- **LATEST Queries**: Average freshness score 0.65-0.85
- **CONCEPTUAL Queries**: Average freshness score 0.20-0.40
- **Freshness Advantage**: 25-45% higher scores for LATEST queries

## Test Report Contents

```json
{
  "timestamp": "2026-07-02T10:30:00",
  "total_queries_executed": 450,
  "successful_queries": 445,
  "failed_queries": 5,
  "by_mode": {
    "rest": {
      "scenario_name": "All REST Queries",
      "total_queries": 300,
      "successful_queries": 298,
      "mean_latency_ms": 185.5,
      "p95_latency_ms": 320.1,
      "p99_latency_ms": 450.2,
      "throughput_qps": 18.3,
      "error_rate": 0.0067,
      "avg_results_per_query": 4.8
    },
    "websocket": {
      "scenario_name": "All WebSocket Queries",
      "total_queries": 150,
      "successful_queries": 147,
      "mean_latency_ms": 250.3,
      "p95_latency_ms": 480.5,
      "p99_latency_ms": 620.1,
      "throughput_qps": 12.5,
      "error_rate": 0.02
    }
  },
  "by_query_type": {
    "LATEST": {
      "count": 225,
      "avg_freshness_score": 0.72,
      "avg_results": 4.9
    },
    "CONCEPTUAL": {
      "count": 225,
      "avg_freshness_score": 0.31,
      "avg_results": 4.7
    }
  },
  "ranking_analysis": {
    "freshness_impact": {
      "latest_advantage": "LATEST queries show 41% higher freshness scores",
      "avg_impact": 0.41
    }
  }
}
```

## Interpreting Results

### Good Performance Indicators
✅ REST latency < 300ms (p95)
✅ WebSocket latency < 500ms (p95)
✅ Error rate < 1%
✅ Throughput > 15 QPS (REST)
✅ LATEST queries show 2-3x higher freshness

### Areas of Concern
⚠️ P99 latency > 1000ms (indicates outliers)
⚠️ Error rate > 5% (reliability issue)
⚠️ Throughput < 10 QPS (performance degradation)
⚠️ Connection failures (WebSocket instability)

## Customizing Tests

### Add More Queries
Edit `evaluation_queries.json` - add to `queries` array:
```json
{
  "id": 13,
  "query": "your custom query",
  "type": "LATEST|CONCEPTUAL",
  "test_limit": 5,
  "test_half_life": 3600
}
```

### Add New Test Scenario
Edit `evaluation_queries.json` - add to `test_scenarios` array:
```json
{
  "name": "My Custom Test",
  "mode": "rest|websocket",
  "concurrency": 10,
  "queries": [1, 2, 3],
  "duration_seconds": 120
}
```

### Adjust Concurrency Levels
Modify concurrency in test scenarios to simulate different load levels.

## Troubleshooting

### Backend not responding
```bash
# Check if services are running
docker compose ps

# View backend logs
docker compose logs backend

# Restart services
docker compose down
docker compose up --build
```

### Tests won't connect
- Verify backend at `http://localhost:8000`
- Ensure WebSocket at `ws://localhost:8000`
- Check firewall rules
- Verify CORS settings in backend

### Memory errors
- Reduce concurrency levels
- Run scenarios sequentially
- Increase system memory
- Check for connection leaks

## Next Steps

After running tests:
1. Review the generated report
2. Identify performance bottlenecks
3. Compare REST vs WebSocket performance
4. Analyze freshness ranking impact
5. Document findings in STEP8_RESULTS.md
6. Optimize backend if needed
7. Run final performance tests

## Files

- `evaluation_queries.json` - Test query configurations
- `load_test.py` - Main load test script
- `requirements.txt` - Python dependencies
- `load_test_report_*.json` - Generated test reports
- `STEP8_README.md` - This file

---

**Status**: Ready to run load tests
**Backend Required**: Yes (must be running)
**Estimated Duration**: 30-45 minutes for all scenarios
