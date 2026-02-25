import json
import os

from dotenv import load_dotenv
from projectdavid import Entity

# ------------------------------------------------------------------
# 0. SDK Init & Environment Setup
# ------------------------------------------------------------------
load_dotenv()

# =============================================
# Initialize the main entry point
#
# ===============================================
client = Entity(
    base_url=os.getenv(
        "BASE_URL", "http://localhost:9000/v1"
    ),  # Ensure /v1 is handled in base or here
    api_key=os.getenv("ENTITIES_API_KEY"),
)

# ------------------------------------------------------------------
# 1. Define the Network Topology (The "Mental Map")
# ------------------------------------------------------------------
# NOTE: This only contains metadata. NO PASSWORDS or SSH KEYS.
# The actual credentials stay local in your secure environment.
network_inventory = [
    {
        "host_name": "core-router-01",
        "ip_address": "10.0.0.1",
        "platform": "cisco_ios",
        "groups": ["core", "hq", "critical"],
        "site": "New York HQ",
        "role": "Edge Router",
    },
    {
        "host_name": "dist-switch-01",
        "ip_address": "10.0.0.2",
        "platform": "cisco_nxos",
        "groups": ["distribution", "hq"],
        "site": "New York HQ",
        "role": "Aggregator",
    },
    {
        "host_name": "fw-primary",
        "ip_address": "10.0.0.254",
        "platform": "paloalto_panos",
        "groups": ["security", "firewall"],
        "site": "New York HQ",
        "role": "Perimeter Firewall",
    },
]

# ------------------------------------------------------------------
# 2. Ingest Inventory to "The Engineer"
# ------------------------------------------------------------------
print(f"🚀 Uploading {len(network_inventory)} devices to The Engineer...")

try:
    # We scope this to the User (Derived automatically from ENTITIES_API_KEY)
    response = client.engineer.ingest_inventory(
        devices=network_inventory,
        clear_existing=True,  # Optional: Refresh the map completely
    )

    print("\n✅ Success! The Engineer now has a mental map of the network.")
    print(json.dumps(response, indent=2))

except Exception as e:
    print(f"\n❌ Failed to ingest inventory: {e}")

# ------------------------------------------------------------------
# 3. What happens next? (Conceptual)
# ------------------------------------------------------------------
# Now, when you ask the LLM: "Check the interfaces on the Core Router"
# The LLM will:
# 1. Search its tool for 'core-router'
# 2. Find '10.0.0.1' and 'cisco_ios'
# 3. Issue a command back to your SDK to execute locally.
