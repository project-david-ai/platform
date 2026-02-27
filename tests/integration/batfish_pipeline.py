import os
import time

from dotenv import load_dotenv
from projectdavid import Entity

# ------------------------------------------------------------------
# 0.  SDK init + env
# ------------------------------------------------------------------
load_dotenv()

client = Entity(
    base_url=os.getenv("BASE_URL", "http://localhost:9000"),
    api_key=os.getenv("ENTITIES_API_KEY"),
)

# ------------------------------------------------------------------
# 1.  Create a snapshot — server generates and returns the opaque ID
# ------------------------------------------------------------------
# Option B — pass the container-side path explicitly
snapshot = client.batfish.refresh_snapshot(
    snapshot_name="incident_0013",
    configs_root="/data/gns3/configs_for_batfish",
)
# time.sleep(1000)
print(snapshot.id)  # e.g. "snap_a1b2c3d4"
print(snapshot.status)  # StatusEnum.active
print(snapshot.device_count)  # 12
print(snapshot.devices)  # ["router-1", "router-2", ...]

# ------------------------------------------------------------------
# 2.  Store the ID — everything else flows through it
# ------------------------------------------------------------------
snapshot_id = snapshot.id


# ------------------------------------------------------------------
# 3.  Run a single RCA tool (what the LLM agent calls per function call)
# ------------------------------------------------------------------
result = client.batfish.run_tool(snapshot_id, "get_logical_topology_with_mtu")
print(result["result"])

# ------------------------------------------------------------------
# 4.  Run all tools in one shot (broader RCA sweep)
# ------------------------------------------------------------------
all_results = client.batfish.run_all_tools(snapshot_id)
for tool_name, output in all_results["results"].items():
    print(f"\n{'='*60}")
    print(f"TOOL: {tool_name}")
    print(output)


# ------------------------------------------------------------------
# 5.  Look up the snapshot later by ID
# ------------------------------------------------------------------
record = client.batfish.get_snapshot(snapshot_id)
if record:
    print(record.last_ingested_at)
    print(record.snapshot_key)  # "{user_id}_{snapshot_id}" — internal only

# ------------------------------------------------------------------
# 6.  List all snapshots owned by this caller
# ------------------------------------------------------------------
snapshots = client.batfish.list_snapshots()
for s in snapshots:
    print(f"{s.id}  {s.snapshot_name:<20}  {s.status.value}  devices={s.device_count}")

# ------------------------------------------------------------------
# 7.  Soft-delete when done
# ------------------------------------------------------------------
client.batfish.delete_snapshot(snapshot_id)

# ------------------------------------------------------------------
# 8.  Health check
# ------------------------------------------------------------------
health = client.batfish.check_health()
print(health)  # {"status": "reachable", "host": "batfish", "port": 9996}
