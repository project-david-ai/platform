import os

from dotenv import load_dotenv
from projectdavid import Entity

load_dotenv()

client = Entity(
    base_url=os.getenv("BASE_URL", "http://localhost:9000"),
    api_key=os.getenv("ENTITIES_API_KEY"),
)

# ------------------------------------------------------------------
# Pass the opaque ID directly — no create_snapshot needed
# ------------------------------------------------------------------
snapshot_id = "snap_4e05e65ce5b549febd89"
user_id = os.getenv("ENTITIES_USER_ID")  # add to your .env

# ------------------------------------------------------------------
# 1.  Run a single RCA tool
# ------------------------------------------------------------------
result = client.batfish.run_tool(snapshot_id, "get_ospf_failures", user_id=user_id)
print(result["result"])

# ------------------------------------------------------------------
# 2.  Run all tools in one shot
# ------------------------------------------------------------------
all_results = client.batfish.run_all_tools(snapshot_id, user_id=user_id)
for tool_name, output in all_results["results"].items():
    print(f"\n{'='*60}")
    print(f"TOOL: {tool_name}")
    print(output)

# ------------------------------------------------------------------
# 3.  Look up the snapshot by ID
# ------------------------------------------------------------------
record = client.batfish.get_snapshot(snapshot_id, user_id=user_id)
if record:
    print(record.last_ingested_at)
    print(record.snapshot_key)

# ------------------------------------------------------------------
# 4.  List all snapshots
# ------------------------------------------------------------------
snapshots = client.batfish.list_snapshots(user_id=user_id)
for s in snapshots:
    print(f"{s.id}  {s.snapshot_name:<20}  {s.status.value}  devices={s.device_count}")

# ------------------------------------------------------------------
# 5.  Health check
# ------------------------------------------------------------------
health = client.batfish.check_health()
print(health)
