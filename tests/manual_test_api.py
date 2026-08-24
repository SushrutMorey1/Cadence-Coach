"""Quick test script for the /api/rewrite endpoint."""
import requests
import json

url = "http://localhost:8000/api/rewrite"
payload = {
    "user_text": (
        "We are launching a new product next week. "
        "It will help people save time and money. "
        "Our team has worked really hard on this."
    ),
    "target_style": "Inspirational TED Talk speaker",
}

print("Sending request to /api/rewrite ...")
r = requests.post(url, json=payload, timeout=60)
print(f"Status: {r.status_code}\n")
print(json.dumps(r.json(), indent=2, ensure_ascii=False))
