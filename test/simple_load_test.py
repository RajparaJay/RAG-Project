#!/usr/bin/env python3
"""
Step 8: Lightweight Load Test Runner
====================================
Simple synchronous load test without heavy dependencies.
Tests REST endpoints and measures performance metrics.
"""

import json
import time
import statistics
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import subprocess
import sys

def check_backend():
    """Verify backend is running"""
    print("\n[1/5] Checking backend connectivity...")
    try:
        result = subprocess.run(
            ['curl', '-s', '-m', '2', 'http://localhost:8000/health'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print("✅ Backend is responding")
            return True
        else:
            print(f"⚠️ Backend health check: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Backend check failed: {e}")
        return False

def run_rest_query(query_text: str, limit: int = 5, half_life: int = 3600) -> Tuple[float, bool, Dict]:
    """Run a single REST query and return metrics"""
    try:
        start = time.time()
        result = subprocess.run(
            [
                'curl', '-s',
                '-X', 'POST',
                'http://localhost:8000/query',
                '-H', 'Content-Type: application/json',
                '-d', json.dumps({
                    "query": query_text,
                    "limit": limit,
                    "half_life": half_life
                })
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        elapsed_ms = (time.time() - start) * 1000
        
        if result.returncode == 0 and result.stdout:
            try:
                data = json.loads(result.stdout)
                evidence_count = len(data.get("evidence", []))
                answer_length = len(data.get("answer", ""))
                freshness = data.get("evidence", [{}])[0].get("freshness_score", 0) if data.get("evidence") else 0
                
                return elapsed_ms, True, {
                    "results": evidence_count,
                    "answer_length": answer_length,
                    "freshness": freshness
                }
            except json.JSONDecodeError:
                return elapsed_ms, False, {"error": "Invalid JSON response"}
        else:
            return elapsed_ms, False, {"error": result.stderr or "No response"}
    except subprocess.TimeoutExpired:
        return 30000, False, {"error": "Timeout"}
    except Exception as e:
        return 0, False, {"error": str(e)}

def run_load_test_scenario(scenario: Dict, queries: List[Dict]) -> Dict:
    """Run a test scenario"""
    print(f"\n[SCENARIO] {scenario['name']}")
    print(f"  Mode: {scenario['mode']}")
    print(f"  Queries: {len(scenario.get('queries', []))}")
    
    query_map = {q["id"]: q for q in queries}
    selected_queries = [
        query_map[qid] for qid in scenario.get("queries", [])
        if qid in query_map
    ]
    
    response_times = []
    successful = 0
    failed = 0
    total_results = []
    freshness_scores = []
    
    print("  Running queries...", end="", flush=True)
    start_time = time.time()
    duration = scenario.get("duration_seconds", 60)
    query_idx = 0
    
    while time.time() - start_time < duration:
        query = selected_queries[query_idx % len(selected_queries)]
        elapsed, success, data = run_rest_query(
            query["query"],
            query.get("test_limit", 5),
            query.get("test_half_life", 3600)
        )
        
        response_times.append(elapsed)
        
        if success:
            successful += 1
            total_results.append(data.get("results", 0))
            if data.get("freshness"):
                freshness_scores.append(data["freshness"])
        else:
            failed += 1
        
        query_idx += 1
        if query_idx % 5 == 0:
            print(".", end="", flush=True)
    
    print(" Done!")
    
    # Compute metrics
    total_queries = successful + failed
    error_rate = failed / total_queries if total_queries > 0 else 0
    
    metrics = {
        "scenario": scenario['name'],
        "mode": scenario['mode'],
        "total_queries": total_queries,
        "successful": successful,
        "failed": failed,
        "error_rate": error_rate,
        "response_times": {
            "min_ms": min(response_times) if response_times else 0,
            "max_ms": max(response_times) if response_times else 0,
            "mean_ms": statistics.mean(response_times) if response_times else 0,
            "median_ms": statistics.median(response_times) if response_times else 0,
        }
    }
    
    if len(response_times) > 10:
        sorted_times = sorted(response_times)
        metrics["response_times"]["p95_ms"] = sorted_times[int(len(sorted_times) * 0.95)]
        metrics["response_times"]["p99_ms"] = sorted_times[int(len(sorted_times) * 0.99)]
    
    if total_results:
        metrics["avg_results_per_query"] = statistics.mean(total_results)
    
    if freshness_scores:
        metrics["avg_freshness_score"] = statistics.mean(freshness_scores)
    
    print(f"  Results: {successful} successful, {failed} failed")
    print(f"  Response time: {metrics['response_times']['mean_ms']:.1f} ms (avg)")
    
    return metrics

def load_evaluation_config() -> Dict:
    """Load evaluation queries config"""
    config_path = Path(__file__).parent / "evaluation_queries.json"
    with open(config_path) as f:
        return json.load(f)

def generate_test_plan() -> List[Dict]:
    """Generate simplified test plan"""
    return [
        {
            "name": "REST Query Single (Baseline)",
            "mode": "rest",
            "concurrency": 1,
            "queries": [1, 7],
            "duration_seconds": 30
        },
        {
            "name": "REST Query Multiple",
            "mode": "rest",
            "concurrency": 1,
            "queries": [1, 2, 3, 4, 5, 6, 7, 8],
            "duration_seconds": 45
        },
        {
            "name": "REST LATEST Queries",
            "mode": "rest",
            "concurrency": 1,
            "queries": [1, 2, 3, 4, 5, 6],
            "duration_seconds": 30
        },
        {
            "name": "REST CONCEPTUAL Queries",
            "mode": "rest",
            "concurrency": 1,
            "queries": [7, 8, 9, 10, 11, 12],
            "duration_seconds": 30
        },
    ]

def main():
    """Main entry point"""
    print("=" * 80)
    print("Step 8: Load Testing & Evaluation")
    print("=" * 80)
    
    # Check backend
    if not check_backend():
        print("\n❌ Backend not available. Cannot run load tests.")
        print("   Please ensure backend is running: docker compose up -d")
        return 1
    
    # Load configuration
    print("\n[2/5] Loading test configuration...")
    try:
        config = load_evaluation_config()
        queries = config.get("queries", [])
        print(f"✅ Loaded {len(queries)} evaluation queries")
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        return 1
    
    # Generate test plan
    print("\n[3/5] Generating test plan...")
    test_plan = generate_test_plan()
    print(f"✅ Generated {len(test_plan)} test scenarios")
    
    # Run tests
    print("\n[4/5] Running load tests...")
    all_metrics = []
    for scenario in test_plan:
        metrics = run_load_test_scenario(scenario, queries)
        all_metrics.append(metrics)
    
    # Generate report
    print("\n[5/5] Generating report...")
    report = {
        "timestamp": datetime.now().isoformat(),
        "test_scenarios": all_metrics,
        "summary": {
            "total_scenarios": len(test_plan),
            "total_queries_executed": sum(m["total_queries"] for m in all_metrics),
            "total_successful": sum(m["successful"] for m in all_metrics),
            "total_failed": sum(m["failed"] for m in all_metrics),
            "overall_error_rate": sum(m["failed"] for m in all_metrics) / sum(m["total_queries"] for m in all_metrics) if all_metrics else 0
        }
    }
    
    # Calculate averages by query type
    latest_metrics = [m for m in all_metrics if "LATEST" in m["scenario"]]
    conceptual_metrics = [m for m in all_metrics if "CONCEPTUAL" in m["scenario"]]
    
    if latest_metrics:
        report["summary"]["latest_queries"] = {
            "avg_response_time_ms": statistics.mean([m["response_times"]["mean_ms"] for m in latest_metrics]),
            "avg_freshness": statistics.mean([m.get("avg_freshness_score", 0) for m in latest_metrics if "avg_freshness_score" in m]),
            "total_queries": sum(m["total_queries"] for m in latest_metrics)
        }
    
    if conceptual_metrics:
        report["summary"]["conceptual_queries"] = {
            "avg_response_time_ms": statistics.mean([m["response_times"]["mean_ms"] for m in conceptual_metrics]),
            "avg_freshness": statistics.mean([m.get("avg_freshness_score", 0) for m in conceptual_metrics if "avg_freshness_score" in m]),
            "total_queries": sum(m["total_queries"] for m in conceptual_metrics)
        }
    
    # Save report
    report_file = Path(__file__).parent / f"load_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Print summary
    print("\n" + "=" * 80)
    print("LOAD TEST SUMMARY")
    print("=" * 80)
    print(f"Total Scenarios: {report['summary']['total_scenarios']}")
    print(f"Total Queries: {report['summary']['total_queries_executed']}")
    print(f"Successful: {report['summary']['total_successful']}")
    print(f"Failed: {report['summary']['total_failed']}")
    print(f"Overall Error Rate: {report['summary']['overall_error_rate']*100:.1f}%")
    
    if "latest_queries" in report["summary"]:
        latest = report["summary"]["latest_queries"]
        print(f"\nLATEST Queries:")
        print(f"  Count: {latest['total_queries']}")
        print(f"  Avg Response: {latest['avg_response_time_ms']:.1f} ms")
        print(f"  Avg Freshness: {latest['avg_freshness']:.2f}")
    
    if "conceptual_queries" in report["summary"]:
        conceptual = report["summary"]["conceptual_queries"]
        print(f"\nCONCEPTUAL Queries:")
        print(f"  Count: {conceptual['total_queries']}")
        print(f"  Avg Response: {conceptual['avg_response_time_ms']:.1f} ms")
        print(f"  Avg Freshness: {conceptual['avg_freshness']:.2f}")
    
    print(f"\nReport saved to: {report_file}")
    print("=" * 80)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
