from dotenv import dotenv_values
import requests

# Read keys.env directly
env = dotenv_values("keys.env")

print("Loaded variables:")
for name in env:
    print(" -", repr(name))

print()

# Explicitly look for your Tavily variables
keys = []

for name in ["TAVILY_API_KEY_1", "TAVILY_API_KEY_2"]:
    value = env.get(name)

    if value:
        keys.append((name, value))

print(f"Found {len(keys)} Tavily key(s)\n")

if not keys:
    print("❌ Still no Tavily keys found.")
    raise SystemExit

# Test each key
for name, key in keys:

    print(f"Testing {name}...")

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "query": "What is Python?",
                "max_results": 1,
            },
            timeout=15,
        )

        print("HTTP status:", response.status_code)

        if response.ok:
            data = response.json()
            print("✅ WORKING")
            print("Results:", len(data.get("results", [])))

        elif response.status_code == 401:
            print("❌ INVALID / UNAUTHORIZED")

        elif response.status_code == 403:
            print("⚠️ FORBIDDEN")

        elif response.status_code == 429:
            print("⚠️ RATE LIMIT / QUOTA EXHAUSTED")

        else:
            print("❌ ERROR")
            print(response.text[:300])

    except Exception as e:
        print("❌ CONNECTION ERROR:", e)

    print("-" * 40)