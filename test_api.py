"""
Test API endpoints to find working configuration.
"""
import requests
import json

BASE_URL = "https://elasticnes.saude.gov.br"

# Headers based on browser analysis (Kibana 8.8.2)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/kibana/app/dashboards",
    "kbn-version": "8.8.2",
    "kbn-xsrf": "kibana",
}

# Simple query for testing
test_query = {
    "size": 1,
    "query": {"match_all": {}}
}

print("Testing CNES API endpoints...\n")

# Test endpoints
endpoints_to_test = [
    (f"{BASE_URL}/kibana/internal/bsearch", "bsearch", {
        "batch": [{"request": {"params": {"index": "cnes_leitos*", "body": test_query}}}]
    }),
    (f"{BASE_URL}/kibana/api/console/proxy?path=cnes_leitos*/_search&method=POST", "console proxy", test_query),
    (f"{BASE_URL}/kibana/elasticsearch/cnes_leitos*/_search", "elasticsearch direct", test_query),
]

for url, name, body in endpoints_to_test:
    print(f"Testing: {name}")
    print(f"  URL: {url[:80]}...")
    try:
        response = requests.post(url, headers=headers, json=body, timeout=15)
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"  Response type: {type(data).__name__}")
                if isinstance(data, dict):
                    print(f"  Keys: {list(data.keys())[:5]}")
                print("  ✅ SUCCESS!")
            except:
                print(f"  Response (first 200 chars): {response.text[:200]}")
        else:
            print(f"  Response: {response.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")
    print()
