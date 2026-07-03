#!/usr/bin/env python3
"""
Step 8: Load Testing & Evaluation Script
========================================
Tests REST and WebSocket endpoints under load, compares ranking strategies.
"""

import asyncio
import json
import time
import statistics
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple
import concurrent.futures
import logging

import httpx
import websockets
import numpy as np
from dataclasses import dataclass, field, asdict
from enum import Enum

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class QueryType(Enum):
    LATEST = "LATEST"
    CONCEPTUAL = "CONCEPTUAL"


@dataclass
class QueryResult:
    """Single query result with metrics"""
    query_id: int
    query_text: str
    mode: str
    status_code: int = None
    response_time_ms: float = None
    results_count: int = 0
    top_result_freshness: float = None
    answer_length: int = 0
    error: str = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class MetricsSnapshot:
    """Aggregated metrics for a test scenario"""
    scenario_name: str
    mode: str
    concurrency: int
    total_queries: int
    successful_queries: int
    failed_queries: int
    response_times: List[float] = field(default_factory=list)
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    mean_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    throughput_qps: float = 0.0
    error_rate: float = 0.0
    avg_results_per_query: float = 0.0
    avg_answer_length: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime = field(default_factory=datetime.now)
    duration_seconds: float = 0.0


class RestClient:
    """HTTP REST client for /query endpoint"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=30.0)
    
    async def query(self, query_text: str, query_id: int, limit: int = 5, 
                   half_life: int = 3600) -> QueryResult:
        """Execute a REST query"""
        try:
            start = time.time()
            response = self.client.post(
                f"{self.base_url}/query",
                json={
                    "query": query_text,
                    "limit": limit,
                    "half_life": half_life
                }
            )
            elapsed_ms = (time.time() - start) * 1000
            
            result = QueryResult(
                query_id=query_id,
                query_text=query_text,
                mode="rest",
                status_code=response.status_code,
                response_time_ms=elapsed_ms
            )
            
            if response.status_code == 200:
                data = response.json()
                result.results_count = len(data.get("evidence", []))
                result.answer_length = len(data.get("answer", ""))
                
                # Get top result freshness
                evidence = data.get("evidence", [])
                if evidence:
                    result.top_result_freshness = evidence[0].get("freshness_score", 0.0)
            else:
                result.error = f"HTTP {response.status_code}: {response.text[:100]}"
            
            return result
        except Exception as e:
            return QueryResult(
                query_id=query_id,
                query_text=query_text,
                mode="rest",
                error=str(e)
            )
    
    def close(self):
        self.client.close()


class WebSocketClient:
    """WebSocket client for /subscribe endpoint"""
    
    def __init__(self, base_url: str = "ws://localhost:8000"):
        self.base_url = base_url
        self.updates_received = 0
    
    async def subscribe(self, query_text: str, query_id: int, limit: int = 5,
                       half_life: int = 3600, duration_seconds: int = 30) -> QueryResult:
        """Subscribe to live updates via WebSocket"""
        try:
            uri = f"{self.base_url}/subscribe"
            start = time.time()
            
            async with websockets.connect(uri, ping_interval=None) as websocket:
                connect_time = (time.time() - start) * 1000
                
                # Send setup message
                setup = {
                    "query": query_text,
                    "limit": limit,
                    "half_life": half_life
                }
                await websocket.send(json.dumps(setup))
                
                result = QueryResult(
                    query_id=query_id,
                    query_text=query_text,
                    mode="websocket",
                    response_time_ms=connect_time,
                    status_code=200
                )
                
                # Collect updates for duration
                deadline = time.time() + duration_seconds
                max_answer_length = 0
                total_results = 0
                freshness_scores = []
                
                try:
                    while time.time() < deadline:
                        timeout = deadline - time.time()
                        if timeout <= 0:
                            break
                        
                        message = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=timeout
                        )
                        data = json.loads(message)
                        
                        if data.get("type") == "subscribed":
                            logger.debug(f"WebSocket subscribed to query {query_id}")
                        elif data.get("type") == "update":
                            self.updates_received += 1
                            answer = data.get("answer", "")
                            max_answer_length = max(max_answer_length, len(answer))
                            
                            evidence = data.get("evidence", [])
                            total_results = max(total_results, len(evidence))
                            
                            for item in evidence:
                                if "freshness_score" in item:
                                    freshness_scores.append(item["freshness_score"])
                        elif data.get("type") == "error":
                            logger.warning(f"WebSocket error for query {query_id}: {data.get('message')}")
                
                except asyncio.TimeoutError:
                    pass  # Expected timeout when no more messages
                
                result.results_count = total_results
                result.answer_length = max_answer_length
                if freshness_scores:
                    result.top_result_freshness = statistics.mean(freshness_scores)
                
                return result
        
        except Exception as e:
            return QueryResult(
                query_id=query_id,
                query_text=query_text,
                mode="websocket",
                error=str(e)
            )


class LoadTestRunner:
    """Orchestrates load tests and collects metrics"""
    
    def __init__(self, queries_file: Path):
        self.queries_file = queries_file
        self.config = self._load_config()
        self.rest_client = RestClient()
        self.ws_client = WebSocketClient()
        self.results: List[QueryResult] = []
    
    def _load_config(self) -> Dict:
        """Load evaluation queries configuration"""
        with open(self.queries_file) as f:
            return json.load(f)
    
    async def run_rest_query_test(self, scenario: Dict) -> List[QueryResult]:
        """Run REST query test scenario"""
        logger.info(f"Starting scenario: {scenario['name']}")
        scenario_results = []
        queries = scenario.get("queries", [])
        concurrency = scenario.get("concurrency", 1)
        
        # Map query IDs to query objects
        query_map = {q["id"]: q for q in self.config["queries"]}
        selected_queries = [query_map[qid] for qid in queries if qid in query_map]
        
        # Repeat queries to fill duration
        start_time = time.time()
        duration = scenario.get("duration_seconds", 60)
        query_queue = selected_queries * ((duration // len(selected_queries)) + 2)
        
        # Execute with concurrency
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = []
            
            for query in query_queue:
                if time.time() - start_time > duration:
                    break
                
                future = executor.submit(
                    self._sync_rest_query,
                    query["query"],
                    query["id"],
                    query.get("test_limit", 5),
                    query.get("test_half_life", 3600)
                )
                futures.append(future)
            
            # Collect results
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                scenario_results.append(result)
                self.results.append(result)
        
        logger.info(f"Completed scenario: {scenario['name']} - {len(scenario_results)} queries")
        return scenario_results
    
    def _sync_rest_query(self, query_text: str, query_id: int, limit: int, half_life: int) -> QueryResult:
        """Synchronous wrapper for async REST query"""
        return asyncio.run(self.rest_client.query(query_text, query_id, limit, half_life))
    
    async def run_websocket_test(self, scenario: Dict) -> List[QueryResult]:
        """Run WebSocket subscription test scenario"""
        logger.info(f"Starting scenario: {scenario['name']}")
        scenario_results = []
        queries = scenario.get("queries", [])
        concurrency = scenario.get("concurrency", 1)
        duration = scenario.get("duration_seconds", 60)
        
        # Map query IDs to query objects
        query_map = {q["id"]: q for q in self.config["queries"]}
        selected_queries = [query_map[qid] for qid in queries if qid in query_map]
        
        # Start concurrent subscriptions
        tasks = []
        for i in range(concurrency):
            query = selected_queries[i % len(selected_queries)]
            task = self.ws_client.subscribe(
                query["query"],
                query["id"],
                query.get("test_limit", 5),
                query.get("test_half_life", 3600),
                duration_seconds=duration
            )
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"WebSocket task failed: {result}")
            else:
                scenario_results.append(result)
                self.results.append(result)
        
        logger.info(f"Completed scenario: {scenario['name']} - {len(scenario_results)} subscriptions")
        return scenario_results
    
    def compute_metrics(self, results: List[QueryResult]) -> MetricsSnapshot:
        """Compute aggregated metrics from results"""
        successful = [r for r in results if r.status_code == 200 or (r.error is None and r.mode == "websocket")]
        failed = len(results) - len(successful)
        
        response_times = [r.response_time_ms for r in successful if r.response_time_ms]
        result_counts = [r.results_count for r in successful]
        answer_lengths = [r.answer_length for r in successful]
        
        metrics = MetricsSnapshot(
            scenario_name="",
            mode=results[0].mode if results else "unknown",
            concurrency=1,
            total_queries=len(results),
            successful_queries=len(successful),
            failed_queries=failed,
            response_times=response_times,
            error_rate=failed / len(results) if results else 0.0
        )
        
        if response_times:
            metrics.p50_latency_ms = np.percentile(response_times, 50)
            metrics.p95_latency_ms = np.percentile(response_times, 95)
            metrics.p99_latency_ms = np.percentile(response_times, 99)
            metrics.mean_latency_ms = statistics.mean(response_times)
            metrics.min_latency_ms = min(response_times)
            metrics.max_latency_ms = max(response_times)
            metrics.throughput_qps = len(response_times) / (sum(response_times) / 1000)
        
        if result_counts:
            metrics.avg_results_per_query = statistics.mean(result_counts)
        
        if answer_lengths:
            metrics.avg_answer_length = int(statistics.mean(answer_lengths))
        
        return metrics
    
    async def run_all_scenarios(self):
        """Execute all test scenarios"""
        logger.info("=" * 80)
        logger.info("Starting Step 8: Load Testing & Evaluation")
        logger.info("=" * 80)
        
        scenarios = self.config.get("test_scenarios", [])
        
        for scenario in scenarios:
            try:
                if scenario["mode"] == "rest":
                    await self.run_rest_query_test(scenario)
                elif scenario["mode"] == "websocket":
                    await self.run_websocket_test(scenario)
            except Exception as e:
                logger.error(f"Scenario {scenario['name']} failed: {e}", exc_info=True)
        
        logger.info("=" * 80)
        logger.info("All scenarios completed")
        logger.info("=" * 80)
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive test report"""
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_queries_executed": len(self.results),
            "successful_queries": len([r for r in self.results if r.status_code == 200 or (r.error is None and r.mode == "websocket")]),
            "failed_queries": len([r for r in self.results if r.error is not None]),
            "by_mode": {}
        }
        
        # Group by mode
        by_mode = {}
        for result in self.results:
            if result.mode not in by_mode:
                by_mode[result.mode] = []
            by_mode[result.mode].append(result)
        
        # Compute metrics by mode
        for mode, results in by_mode.items():
            metrics = self.compute_metrics(results)
            metrics.scenario_name = f"All {mode.upper()} Queries"
            metrics.mode = mode
            metrics.concurrency = 1
            report["by_mode"][mode] = asdict(metrics)
        
        # Group by query type
        report["by_query_type"] = {}
        query_map = {q["id"]: q for q in self.config["queries"]}
        
        for query_type in ["LATEST", "CONCEPTUAL"]:
            type_results = [
                r for r in self.results 
                if r.query_id in query_map and query_map[r.query_id]["type"] == query_type
            ]
            if type_results:
                metrics = self.compute_metrics(type_results)
                metrics.scenario_name = f"All {query_type} Queries"
                metrics.mode = "mixed"
                report["by_query_type"][query_type] = asdict(metrics)
        
        # Ranking comparison
        report["ranking_analysis"] = self._analyze_ranking()
        
        return report
    
    def _analyze_ranking(self) -> Dict:
        """Analyze ranking quality and freshness impact"""
        query_map = {q["id"]: q for q in self.config["queries"]}
        
        latest_results = [r for r in self.results if r.query_id in query_map and query_map[r.query_id]["type"] == "LATEST"]
        conceptual_results = [r for r in self.results if r.query_id in query_map and query_map[r.query_id]["type"] == "CONCEPTUAL"]
        
        latest_freshness = [r.top_result_freshness for r in latest_results if r.top_result_freshness]
        conceptual_freshness = [r.top_result_freshness for r in conceptual_results if r.top_result_freshness]
        
        return {
            "latest_queries": {
                "count": len(latest_results),
                "avg_freshness_score": statistics.mean(latest_freshness) if latest_freshness else 0.0,
                "avg_results": statistics.mean([r.results_count for r in latest_results if r.results_count]) if latest_results else 0.0
            },
            "conceptual_queries": {
                "count": len(conceptual_results),
                "avg_freshness_score": statistics.mean(conceptual_freshness) if conceptual_freshness else 0.0,
                "avg_results": statistics.mean([r.results_count for r in conceptual_results if r.results_count]) if conceptual_results else 0.0
            },
            "freshness_impact": {
                "latest_advantage": ("LATEST queries show higher freshness scores for recent events" if latest_freshness and conceptual_freshness and statistics.mean(latest_freshness) > statistics.mean(conceptual_freshness) else "Results depend on available data"),
                "avg_impact": abs(statistics.mean(latest_freshness) - statistics.mean(conceptual_freshness)) if latest_freshness and conceptual_freshness else 0.0
            }
        }
    
    def save_report(self, report: Dict, filename: Path):
        """Save report to JSON file"""
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Report saved to {filename}")
    
    def cleanup(self):
        """Cleanup resources"""
        self.rest_client.close()


async def main():
    """Main entry point"""
    test_dir = Path(__file__).parent
    queries_file = test_dir / "evaluation_queries.json"
    
    if not queries_file.exists():
        logger.error(f"Queries file not found: {queries_file}")
        return
    
    runner = LoadTestRunner(queries_file)
    
    try:
        # Run all test scenarios
        await runner.run_all_scenarios()
        
        # Generate and save report
        report = runner.generate_report()
        report_file = test_dir / f"load_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        runner.save_report(report, report_file)
        
        # Print summary
        print("\n" + "=" * 80)
        print("LOAD TEST SUMMARY")
        print("=" * 80)
        print(f"Total Queries Executed: {report['total_queries_executed']}")
        print(f"Successful: {report['successful_queries']}")
        print(f"Failed: {report['failed_queries']}")
        
        for mode, metrics in report["by_mode"].items():
            print(f"\n{mode.upper()} Mode:")
            print(f"  Mean Latency: {metrics['mean_latency_ms']:.2f} ms")
            print(f"  P95 Latency: {metrics['p95_latency_ms']:.2f} ms")
            print(f"  P99 Latency: {metrics['p99_latency_ms']:.2f} ms")
            print(f"  Throughput: {metrics['throughput_qps']:.2f} queries/sec")
            print(f"  Error Rate: {metrics['error_rate']*100:.1f}%")
        
        print(f"\nReport saved to: {report_file}")
        print("=" * 80)
    
    finally:
        runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
