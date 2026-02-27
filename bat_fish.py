import requests
from pybatfish.client.session import Session
from pybatfish.datamodel import HeaderConstraints

r = requests.post(
    "http://localhost:9000/v1/batfish/snapshot/refresh?incident=incident_001"
)
print(r.json())


# ── Connect ───────────────────────────────────────────────────────────────────
bf = Session(host="localhost", port=9996)
bf.set_network("gns3_network")
bf.init_snapshot(
    r"C:\Users\franc\PycharmProjects\entities_api\snapshots\incident_001",
    name="incident_001",
    overwrite=True,
)
print("Snapshot loaded\n")

# ── Devices ───────────────────────────────────────────────────────────────────
print("=== NODES ===")
print(bf.q.nodeProperties().answer().frame().to_string())

# ── Interfaces ────────────────────────────────────────────────────────────────
print("\n=== INTERFACES ===")
print(bf.q.interfaceProperties().answer().frame().to_string())

# ── Routing Tables ────────────────────────────────────────────────────────────
print("\n=== ROUTES ===")
print(bf.q.routes().answer().frame().to_string())

# ── Config Issues ─────────────────────────────────────────────────────────────
print("\n=== UNDEFINED REFERENCES ===")
print(bf.q.undefinedReferences().answer().frame().to_string())

print("\n=== UNUSED STRUCTURES ===")
print(bf.q.unusedStructures().answer().frame().to_string())

# ── Routing Protocols ─────────────────────────────────────────────────────────
print("\n=== BGP SESSIONS ===")
print(bf.q.bgpSessionStatus().answer().frame().to_string())

print("\n=== OSPF INTERFACES ===")
print(bf.q.ospfInterfaceConfiguration().answer().frame().to_string())

# ── MTU / OSPF Compatibility ──────────────────────────────────────────────────
print("\n=== OSPF SESSION COMPATIBILITY ===")
print(bf.q.ospfSessionCompatibility().answer().frame().to_string())

print("\n=== INTERFACE MTU ===")
print(bf.q.interfaceProperties(properties="MTU").answer().frame().to_string())

# ── Reachability (edit src/dst to match your topology) ───────────────────────
# print("\n=== TRACEROUTE ===")
# print(bf.q.traceroute(
#     startLocation="router1",
#     headers=HeaderConstraints(dstIps="10.0.0.2")
# ).answer().frame().to_string())
