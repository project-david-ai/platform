import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BRAVE_API_KEY")
if not API_KEY:
    raise RuntimeError("BRAVE_API_KEY not set")

url = "https://api.search.brave.com/res/v1/web/search"

params = {
    "q": "bgp route reflector design",
    "count": 10,
    "offset": 0,
    "safesearch": "off",
    "freshness": "pw",
    "text_decorations": False,
    "search_lang": "en",
}

headers = {"Accept": "application/json", "X-Subscription-Token": API_KEY}

resp = requests.get(url, headers=headers, params=params, timeout=10)
resp.raise_for_status()

results = resp.json()["web"]["results"]

for i, r in enumerate(results):
    print(f"{i+1}. {r['title']}")
    print(f"   {r['url']}")
    print(f"   {r.get('description')}")
    print()
