#!/usr/bin/env python3
"""
Test script for ToolsDNS multi-agent token-saving features.

Run this after starting the ToolsDNS server:
    python3 -m tooldns.cli serve

Then:
    python3 test_multi_agent_features.py

This tests:
1. Minimal schema mode (~70% token reduction)
2. Agent sessions with schema dedup
3. Batch search (multiple queries, one HTTP call)
4. Tool profiles (scoped tool subsets)
5. Cost report endpoint
"""

import httpx
import time
import sys

API_KEY = "td_dev_key"  # Change if your key is different
BASE = "http://localhost:8787"
headers = {"Authorization": f"Bearer {API_KEY}"}


def test_health():
    """Test 1: Health check"""
    print("=== Test 1: Health Check ===")
    resp = httpx.get(f"{BASE}/health", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    print(f"✓ Server healthy: {data['tools_indexed']} tools indexed")
    return True


def test_basic_search():
    """Test 2: Basic search without new features"""
    print("\n=== Test 2: Basic Search ===")
    resp = httpx.post(
        f"{BASE}/v1/search",
        headers=headers,
        json={"query": "send email", "top_k": 2},
        timeout=30
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) > 0
    assert "tokens_saved" in data
    print(f"✓ Found {len(data['results'])} tools")
    print(f"✓ Tokens saved: {data['tokens_saved']:,}")
    return data["results"][0]["id"] if data["results"] else None


def test_minimal_mode():
    """Test 3: Minimal schema mode - strips optional fields"""
    print("\n=== Test 3: Minimal Schema Mode ===")
    
    # Full schema
    resp_full = httpx.post(
        f"{BASE}/v1/search",
        headers=headers,
        json={"query": "send email", "top_k": 1, "minimal": False},
        timeout=30
    )
    full_data = resp_full.json()
    full_schema = full_data["results"][0].get("input_schema", {})
    full_keys = set(full_schema.get("properties", {}).keys())
    
    # Minimal schema
    resp_min = httpx.post(
        f"{BASE}/v1/search",
        headers=headers,
        json={"query": "send email", "top_k": 1, "minimal": True},
        timeout=30
    )
    min_data = resp_min.json()
    min_schema = min_data["results"][0].get("input_schema", {})
    min_keys = set(min_schema.get("properties", {}).keys())
    
    print(f"Full schema fields: {len(full_keys)} - {sorted(full_keys)[:5]}...")
    print(f"Minimal schema fields: {len(min_keys)} - {sorted(min_keys)}")
    print(f"_minimal flag: {min_schema.get('_minimal')}")
    
    # Minimal should have fewer or equal fields
    assert len(min_keys) <= len(full_keys), "Minimal should strip optional fields"
    assert min_schema.get("_minimal") == True, "Should have _minimal flag"
    print("✓ Minimal mode reduces schema size")
    return True


def test_sessions():
    """Test 4: Agent sessions with schema dedup"""
    print("\n=== Test 4: Agent Sessions ===")
    
    # Create a session
    resp = httpx.post(
        f"{BASE}/v1/sessions",
        headers=headers,
        json={"agent_id": "test-agent-1", "ttl_seconds": 300}
    )
    assert resp.status_code == 200
    session = resp.json()
    session_id = session["session_id"]
    print(f"✓ Created session: {session_id}")
    
    # First search - should return full schema
    resp1 = httpx.post(
        f"{BASE}/v1/search",
        headers=headers,
        json={"query": "send email", "top_k": 1, "session_id": session_id},
        timeout=30
    )
    data1 = resp1.json()
    first_tool = data1["results"][0]
    first_tool_id = first_tool["id"]
    print(f"✓ First search returned: {first_tool_id}")
    print(f"  Schema empty? {first_tool['input_schema'] == {}}")
    print(f"  Already seen? {first_tool.get('already_seen', False)}")
    
    # Second search for same tool - should mark as already_seen
    resp2 = httpx.post(
        f"{BASE}/v1/search",
        headers=headers,
        json={"query": "email gmail send", "top_k": 1, "session_id": session_id},
        timeout=30
    )
    data2 = resp2.json()
    second_tool = data2["results"][0]
    print(f"✓ Second search returned: {second_tool['id']}")
    print(f"  Schema empty? {second_tool['input_schema'] == {}}")
    print(f"  Already seen? {second_tool.get('already_seen', False)}")
    
    # Check session stats
    resp_stats = httpx.get(f"{BASE}/v1/sessions/{session_id}", headers=headers)
    stats = resp_stats.json()
    print(f"✓ Session stats: {stats['tools_seen']} tools seen, {stats['tokens_saved_by_dedup']} tokens saved by dedup")
    
    # Cleanup
    httpx.delete(f"{BASE}/v1/sessions/{session_id}", headers=headers)
    print("✓ Session deleted")
    return True


def test_batch_search():
    """Test 5: Batch search - multiple queries in one call"""
    print("\n=== Test 5: Batch Search ===")
    
    # Create a shared session for the batch
    resp_sess = httpx.post(
        f"{BASE}/v1/sessions",
        headers=headers,
        json={"agent_id": "batch-test", "ttl_seconds": 300}
    )
    session_id = resp_sess.json()["session_id"]
    
    # Batch search
    resp = httpx.post(
        f"{BASE}/v1/search/batch",
        headers=headers,
        json={
            "queries": [
                {"query": "send email", "top_k": 1},
                {"query": "create github issue", "top_k": 1},
                {"query": "upload file to drive", "top_k": 1}
            ],
            "minimal": True,
            "session_id": session_id
        },
        timeout=60
    )
    assert resp.status_code == 200
    data = resp.json()
    
    print(f"✓ Batch completed: {data['total_queries']} queries")
    print(f"✓ Batch time: {data['batch_time_ms']:.1f}ms")
    print(f"✓ Total tokens saved: {data['total_tokens_saved']:,}")
    print(f"✓ Dedup savings: {data['total_dedup_savings']:,}")
    
    for i, result in enumerate(data["results"]):
        tool_name = result["results"][0]["name"] if result["results"] else "None"
        print(f"  Query {i+1}: {tool_name}")
    
    # Cleanup
    httpx.delete(f"{BASE}/v1/sessions/{session_id}", headers=headers)
    return True


def test_profiles():
    """Test 6: Tool profiles - scoped tool subsets"""
    print("\n=== Test 6: Tool Profiles ===")
    
    # Create a profile
    resp = httpx.post(
        f"{BASE}/v1/profiles",
        headers=headers,
        json={
            "name": "email-agent",
            "description": "Agent for email and calendar tasks",
            "tool_patterns": ["GMAIL_*", "OUTLOOK_*", "GOOGLECALENDAR_*"],
            "pinned_tool_ids": []
        }
    )
    
    if resp.status_code == 409:
        print("✓ Profile 'email-agent' already exists")
    else:
        assert resp.status_code == 200
        profile = resp.json()
        print(f"✓ Created profile: {profile['name']}")
        print(f"  Tool count: {profile['tool_count']}")
    
    # Search with profile
    resp_search = httpx.post(
        f"{BASE}/v1/search",
        headers=headers,
        json={"query": "send message", "top_k": 3, "profile": "email-agent"},
        timeout=30
    )
    data = resp_search.json()
    print(f"✓ Search with profile returned {len(data['results'])} tools")
    print(f"  Profile active: {data.get('profile_active')}")
    for r in data["results"]:
        print(f"    - {r['name']}")
    
    # List all profiles
    resp_list = httpx.get(f"{BASE}/v1/profiles", headers=headers)
    profiles = resp_list.json()
    print(f"✓ Total profiles: {len(profiles)}")
    
    return True


def test_cost_report():
    """Test 7: Cost report endpoint"""
    print("\n=== Test 7: Cost Report ===")
    
    resp = httpx.get(f"{BASE}/v1/cost-report", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    
    print(f"✓ Lifetime searches: {data['lifetime']['total_searches']}")
    print(f"✓ Tokens saved by search: {data['lifetime']['tokens_saved_by_search']:,}")
    print(f"✓ Tokens saved by dedup: {data['lifetime']['tokens_saved_by_dedup']:,}")
    print(f"✓ Total tokens saved: {data['lifetime']['total_tokens_saved']:,}")
    print(f"✓ Model: {data['lifetime']['model']}")
    print(f"✓ Cache hit rate: {data['cache']['hit_rate']:.1%}")
    print(f"✓ Active sessions: {len(data['active_sessions'])}")
    print(f"✓ Active profiles: {len(data['active_profiles'])}")
    
    return True


def main():
    print("=" * 60)
    print("ToolsDNS Multi-Agent Features Test Suite")
    print("=" * 60)
    
    tests = [
        ("Health", test_health),
        ("Basic Search", test_basic_search),
        ("Minimal Mode", test_minimal_mode),
        ("Sessions", test_sessions),
        ("Batch Search", test_batch_search),
        ("Profiles", test_profiles),
        ("Cost Report", test_cost_report),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"\n✗ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
