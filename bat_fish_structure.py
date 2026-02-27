from pybatfish.client.session import Session

bf = Session(host="localhost", port=9996)
bf.set_network("gns3_network")
bf.init_snapshot(
    r"C:\Users\franc\PycharmProjects\entities_api\snapshots\incident_001",
    name="incident_001",
    overwrite=True,
)

ospf_sessions = bf.q.ospfSessionCompatibility().answer().frame()
ospf_interfaces = bf.q.ospfInterfaceConfiguration().answer().frame()
mtu_data = bf.q.interfaceProperties(properties="MTU").answer().frame()
routes = bf.q.routes().answer().frame()

output = f"""
You are a senior network engineer performing root cause analysis.
The following is verified, deterministic data collected by Batfish from live device configs.
This is NOT raw config — it is parsed, structured network state. Trust it completely.

=== OSPF SESSION COMPATIBILITY ===
{ospf_sessions.to_string()}

=== OSPF INTERFACE CONFIGURATION ===
{ospf_interfaces.to_string()}

=== INTERFACE MTU ===
{mtu_data.to_string()}

=== ROUTING TABLE ===
{routes.to_string()}

---
ANALYSIS RULES — follow these strictly before drawing any conclusions:

RULE 1 — MAP THE TOPOLOGY FIRST:
For every subnet in the routing table marked as "connected", identify which two interfaces
share that subnet. Build a complete list of directly connected interface pairs.

RULE 2 — FIND MISSING OSPF SESSIONS:
Cross-reference every directly connected interface pair against the OSPF SESSION COMPATIBILITY
table. Any pair that is directly connected but does NOT appear as ESTABLISHED is a failure.

RULE 3 — DIAGNOSE EACH MISSING SESSION IN THIS EXACT ORDER:
  a) Are both interfaces present in the OSPF INTERFACE CONFIGURATION table?
  b) Is OSPF_Enabled = True on both sides?
  c) Is OSPF_Passive = False on both sides?
  d) Do both interfaces have the same MTU in the INTERFACE MTU table?
  e) Are both interfaces Admin_Up and Active?
  IMPORTANT: MTU mismatch is a PRIMARY cause of OSPF adjacency failure — not secondary.
  An MTU mismatch alone will prevent OSPF from forming, even if everything else is correct.
  Do NOT skip or deprioritize MTU as a cause.

RULE 4 — ROUTE TABLE EVIDENCE:
If a directly connected pair has no OSPF session, confirm the impact by checking whether
traffic between those nodes is being rerouted suboptimally through a third router.

RULE 5 — FIX COMMANDS:
Only recommend fixes that are supported by specific values in the data above.
Do not invent missing configurations. If MTU is the root cause, the fix is to match MTUs.

---
TASK:
1. Map all directly connected interface pairs from the routing table.
2. Identify which OSPF sessions are missing between directly connected routers.
3. Diagnose the root cause of each missing session using Rules 3a-3e above.
4. Describe the network impact with specific reference to routing table entries.
5. Provide exact CLI fix commands with the specific values from the data.
6. Explain verification steps.
"""

print(output)
