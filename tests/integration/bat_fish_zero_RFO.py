"""
BatfishRCAEngine — LLM Function-Call Tool Layer
================================================

Each public method is a discrete tool the LLM can call independently
via function calling. No orchestration logic lives here — that belongs
to the agent. This module is purely the tool brick layer.

Tool Registry (what the LLM sees):
------------------------------------
  get_device_os_inventory()         → OS/vendor context for all nodes
  get_logical_topology_with_mtu()   → L3 subnets, MTU mismatches, interface state
  get_ospf_failures()               → OSPF adjacency compatibility failures
  get_bgp_failures()                → BGP session compatibility failures
  get_undefined_references()        → Missing objects called by config
  get_unused_structures()           → Orphaned/dead config structures
  get_acl_shadowing()               → Unreachable ACL lines (rule ordering bugs)
  get_routing_loop_detection()      → Proven data-plane forwarding loops

Usage:
------
  engine = BatfishRCAEngine(host="localhost", port=9996)
  await engine.load_snapshot("/snapshots/incident_001", "incident_001")

  # LLM calls whichever tools it decides are relevant:
  result = await engine.get_bgp_failures()
  result = await engine.get_ospf_failures()
"""

import asyncio
import ipaddress

import pandas as pd
from pybatfish.client.session import Session


class BatfishRCAEngine:
    """
    Each public async method is a self-contained LLM tool.
    Call any method independently — no ordering dependency, no shared state.
    """

    def __init__(self, host="localhost", port=9996, network_name="gns3_network"):
        self.bf = Session(host=host, port=port)
        self.bf.set_network(network_name)

    # ------------------------------------------------------------------
    # SNAPSHOT
    # ------------------------------------------------------------------

    async def load_snapshot(self, snapshot_path: str, snapshot_name: str) -> str:
        """Load or attach to a Batfish snapshot. Call once before any tools."""

        def _load():
            try:
                self.bf.set_snapshot(snapshot_name)
                return f"Attached to cached snapshot: {snapshot_name}"
            except ValueError:
                self.bf.init_snapshot(snapshot_path, name=snapshot_name)
                return f"Uploaded and initialized snapshot: {snapshot_name}"

        msg = await asyncio.to_thread(_load)
        print(msg)
        return msg

    # ------------------------------------------------------------------
    # TOOL: get_device_os_inventory
    # ------------------------------------------------------------------

    async def get_device_os_inventory(self) -> str:
        """
        TOOL — Device OS / Vendor Inventory
        Returns the OS platform and vendor format for every node.
        Always call this first — the LLM needs CLI syntax context
        before reasoning about any other filter output.
        """

        def _fetch():
            return (
                self.bf.q.nodeProperties(properties="Configuration_Format")
                .answer()
                .frame()
            )

        node_props = await asyncio.to_thread(_fetch)

        if node_props.empty or "Configuration_Format" not in node_props.columns:
            return "=== DEVICE OS INVENTORY ===\nNo OS information could be extracted."

        output = ["=== DEVICE OS / VENDOR INVENTORY ==="]
        for os_format, group in node_props.groupby("Configuration_Format"):
            nodes = group["Node"].astype(str).tolist()
            output.append(f"\nOS Platform: [{os_format}]")
            output.append(f"  └─ Devices ({len(nodes)}): {', '.join(nodes)}")
        return "\n".join(output)

    # ------------------------------------------------------------------
    # TOOL: get_logical_topology_with_mtu
    # ------------------------------------------------------------------

    async def get_logical_topology_with_mtu(self) -> str:
        """
        TOOL — Logical L3 Topology with MTU Analysis
        Maps all L3 interfaces grouped by subnet.
        Flags MTU mismatches and inactive interfaces.
        Best for: Blast-radius mapping, identifying broken adjacencies
        caused by jumbo/standard frame mismatches.
        """

        def _fetch():
            iface_props = (
                self.bf.q.interfaceProperties(properties="Primary_Address,Active,MTU")
                .answer()
                .frame()
            )
            ospf_props = self.bf.q.ospfInterfaceConfiguration().answer().frame()
            return iface_props, ospf_props

        iface_props, ospf_props = await asyncio.to_thread(_fetch)

        iface_props["Node"] = iface_props["Interface"].apply(lambda x: x.hostname)
        iface_props["Int"] = iface_props["Interface"].apply(lambda x: x.interface)

        if not ospf_props.empty:
            ospf_props["Node"] = ospf_props["Interface"].apply(lambda x: x.hostname)
            ospf_props["Int"] = ospf_props["Interface"].apply(lambda x: x.interface)
            merged_df = pd.merge(
                iface_props,
                ospf_props[["Node", "Int", "OSPF_Area_Name"]],
                on=["Node", "Int"],
                how="left",
            )
        else:
            merged_df = iface_props
            merged_df["OSPF_Area_Name"] = "None"

        l3_ifaces = merged_df.dropna(subset=["Primary_Address"]).copy()

        def get_subnet(ip_val):
            try:
                return str(ipaddress.IPv4Interface(str(ip_val)).network)
            except Exception:
                return "Unknown"

        l3_ifaces["Subnet"] = l3_ifaces["Primary_Address"].apply(get_subnet)
        l3_ifaces["OSPF_Area_Name"] = l3_ifaces["OSPF_Area_Name"].fillna("None")
        l3_ifaces["Summary"] = l3_ifaces.apply(
            lambda row: f"  └─ {row['Node']}:{row['Int']} "
            f"(IP: {row['Primary_Address']}, Area: {row['OSPF_Area_Name']}, "
            f"Active: {row['Active']}, MTU: {row['MTU']})",
            axis=1,
        )

        mtu_mismatch_flags = l3_ifaces.groupby("Subnet")["MTU"].nunique() > 1
        logical_subnets = (
            l3_ifaces.groupby("Subnet")["Summary"].apply(list).reset_index()
        )

        output = ["=== LOGICAL L3 TOPOLOGY (GROUPED BY SUBNET WITH MTU) ==="]
        for _, row in logical_subnets.iterrows():
            subnet = row["Subnet"]
            warn = "[⚠️ MTU MISMATCH DETECTED]" if mtu_mismatch_flags[subnet] else ""
            output.append(f"\nNetwork: {subnet}{warn}")
            output.extend(row["Summary"])
        return "\n".join(output)

    # ------------------------------------------------------------------
    # TOOL: get_ospf_failures
    # ------------------------------------------------------------------

    async def get_ospf_failures(self) -> str:
        """
        TOOL — OSPF Session Compatibility Failures
        Reports all OSPF adjacencies that are not ESTABLISHED.
        Catches: dead/hello timer mismatches, auth mismatches, area type conflicts.
        Best for: IGP convergence failures, route flapping root cause.
        """

        def _fetch():
            return self.bf.q.ospfSessionCompatibility().answer().frame()

        ospf_sessions = await asyncio.to_thread(_fetch)

        if ospf_sessions.empty or "Session_Status" not in ospf_sessions.columns:
            return "=== OSPF FAILURES ===\nAll configured OSPF sessions are healthy or none exist."

        failed = ospf_sessions[ospf_sessions["Session_Status"] != "ESTABLISHED"]
        if failed.empty:
            return (
                "=== OSPF FAILURES ===\nAll configured OSPF sessions are established."
            )
        return f"=== OSPF FAILURES ===\n{failed.to_string()}"

    # ------------------------------------------------------------------
    # TOOL: get_bgp_failures
    # ------------------------------------------------------------------

    async def get_bgp_failures(self) -> str:
        """
        TOOL — BGP Session Compatibility Failures
        Reports all BGP peers that are not UNIQUE_MATCH compatible.
        Catches: AS number mismatches, missing peer definitions, policy conflicts.
        Best for: WAN/DC peering failures, prefix leaks, missing routes.
        """

        def _fetch():
            return self.bf.q.bgpSessionCompatibility().answer().frame()

        bgp_sessions = await asyncio.to_thread(_fetch)

        if bgp_sessions.empty or "Configured_Status" not in bgp_sessions.columns:
            return "=== BGP FAILURES ===\nNo BGP sessions exist or status cannot be verified."

        failed = bgp_sessions[bgp_sessions["Configured_Status"] != "UNIQUE_MATCH"]
        if failed.empty:
            return "=== BGP FAILURES ===\nAll configured BGP sessions are compatible and healthy."

        output = ["=== BGP SESSION FAILURES DETECTED ==="]
        for node, group in failed.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ Peer IP: {row.get('Remote_IP','?')} | "
                    f"Local AS: {row.get('Local_AS','?')} -> Remote AS: {row.get('Remote_AS','?')} | "
                    f"Status: [⚠️ {row.get('Configured_Status','?')}]"
                )
        return "\n".join(output)

    # ------------------------------------------------------------------
    # TOOL: get_undefined_references
    # ------------------------------------------------------------------

    async def get_undefined_references(self) -> str:
        """
        TOOL — Undefined Configuration References
        Finds config objects that are called but do not exist anywhere.
        Catches: typos in route-map names, missing ACLs, missing prefix-lists.
        Best for: Silent drops caused by human error — no alarm is raised
        when a missing object is referenced, traffic just falls through.
        """

        def _fetch():
            return self.bf.q.undefinedReferences().answer().frame()

        undef_refs = await asyncio.to_thread(_fetch)

        if undef_refs.empty:
            return "=== CONFIGURATION HYGIENE ===\nNo undefined references found (All referenced objects exist)."

        output = ["=== UNDEFINED REFERENCES (POSSIBLE TYPOS/MISSING CONFIG) ==="]
        for node, group in undef_refs.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ Missing[{row.get('Structure_Type','?')}]: '{row.get('Structure_Name','?')}' "
                    f"| Called by: {row.get('Context','?')} | File Lines: {row.get('Source_Lines','?')}"
                )
        return "\n".join(output)

    # ------------------------------------------------------------------
    # TOOL: get_unused_structures
    # ------------------------------------------------------------------

    async def get_unused_structures(self) -> str:
        """
        TOOL — Unused / Orphaned Configuration Structures
        Finds config objects that exist but are never referenced.
        Catches: stale route-maps, deprecated prefix-lists, leftover ACLs.
        Best for: Reducing LLM analysis noise — dead code that looks
        meaningful but has zero operational impact.
        """

        def _fetch():
            return self.bf.q.unusedStructures().answer().frame()

        unused = await asyncio.to_thread(_fetch)

        if unused.empty:
            return "=== DEAD CODE ANALYSIS ===\nNo unused structures found."

        output = ["=== UNUSED STRUCTURES (DEAD CODE / ORPHANED CONFIG) ==="]
        for node, group in unused.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ Unused [{row.get('Structure_Type','?')}]: '{row.get('Structure_Name','?')}'"
                )
        return "\n".join(output)

    # ------------------------------------------------------------------
    # TOOL: get_acl_shadowing
    # ------------------------------------------------------------------

    async def get_acl_shadowing(self) -> str:
        """
        TOOL — ACL / Filter Line Shadowing
        Finds ACL lines that are provably unreachable due to earlier rules.
        Catches: rule ordering bugs where intended permit/deny never fires.
        Best for: Silent security policy failures — no error is logged,
        traffic is just matched by the wrong rule upstream.
        """

        def _fetch():
            return self.bf.q.filterLineReachability().answer().frame()

        acl_lines = await asyncio.to_thread(_fetch)

        if acl_lines.empty:
            return (
                "=== ACL SHADOWING ===\nNo shadowed or unreachable ACL lines detected."
            )

        shadowed = acl_lines[acl_lines["Unreachable_Line"].notna()]
        if shadowed.empty:
            return "=== ACL SHADOWING ===\nAll ACL lines are reachable (no shadowing detected)."

        output = ["=== ACL SHADOWING (UNREACHABLE FILTER LINES) ==="]
        for node, group in shadowed.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ ACL [{row.get('Filter_Name','?')}] | "
                    f"Shadowed Line: '{row.get('Unreachable_Line','?')}' | "
                    f"Action: {row.get('Unreachable_Line_Action','?')} | "
                    f"Blocked By: {row.get('Blocking_Lines','?')}"
                )
        return "\n".join(output)

    # ------------------------------------------------------------------
    # TOOL: get_routing_loop_detection
    # ------------------------------------------------------------------

    async def get_routing_loop_detection(self) -> str:
        """
        TOOL — Data-Plane Forwarding Loop Detection
        Formally proves whether any forwarding loops exist in the network.
        Unlike control-plane checks, simulates actual packet forwarding
        across all routing tables — catches loops invisible to per-device review.
        Zero false positives: only flags loops it can formally prove exist.
        Best for: Catastrophic black-hole incidents, CPU spike RCA.
        """

        def _fetch():
            return self.bf.q.detectLoops().answer().frame()

        loops = await asyncio.to_thread(_fetch)

        if loops.empty:
            return "=== ROUTING LOOP DETECTION ===\nNo forwarding loops detected across the data plane."

        output = ["=== ROUTING LOOPS DETECTED (DATA-PLANE SIMULATION) ==="]
        for node, group in loops.groupby("Node"):
            output.append(f"\nLoop Origin Node: {node}")
            for _, row in group.iterrows():
                loop_path = row.get("Loop", "Unknown")
                path_str = (
                    " → ".join(str(h) for h in loop_path)
                    if isinstance(loop_path, list)
                    else str(loop_path)
                )
                output.append(
                    f"  └─ Ingress Interface: {row.get('Ingress_Interface','?')}\n"
                    f"     Loop Path: {path_str} [⚠️ LOOPS BACK TO ORIGIN]"
                )
        return "\n".join(output)


# ---------------------------------------------------------------------------
# TOOL MANIFEST
# Maps tool names (as the LLM sees them) to engine methods.
# Pass this to your function-calling agent at init time.
# ---------------------------------------------------------------------------


def get_tool_manifest(engine: BatfishRCAEngine) -> dict:
    """
    Returns a flat dict of { tool_name: callable } for agent registration.

    Example agent usage:
        manifest = get_tool_manifest(engine)
        result = await manifest["get_bgp_failures"]()
    """
    return {
        "get_device_os_inventory": engine.get_device_os_inventory,
        # "get_logical_topology_with_mtu": engine.get_logical_topology_with_mtu,
        # "get_ospf_failures": engine.get_ospf_failures,
        # "get_bgp_failures": engine.get_bgp_failures,
        # "get_undefined_references": engine.get_undefined_references,
        # "get_unused_structures": engine.get_unused_structures,
        # "get_acl_shadowing": engine.get_acl_shadowing,
        # "get_routing_loop_detection": engine.get_routing_loop_detection,
    }


# ---------------------------------------------------------------------------
# LIVE RUNNER
# ---------------------------------------------------------------------------


async def main():
    engine = BatfishRCAEngine(host="localhost", port=9996, network_name="gns3_network")
    await engine.load_snapshot("/snapshots/incident_001", "incident_001")

    manifest = get_tool_manifest(engine)

    for tool_name, fn in manifest.items():
        print(f"\n>> Tool call: {tool_name}()")
        print("-" * 50)
        print(await fn())
        print()


if __name__ == "__main__":
    asyncio.run(main())
