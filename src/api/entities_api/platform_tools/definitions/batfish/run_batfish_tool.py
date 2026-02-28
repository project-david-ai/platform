# src/api/entities_api/platform_tools/definitions/batfish_tools.py


def create_batfish_tool(name: str, desc: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


get_device_os_inventory = create_batfish_tool(
    "get_device_os_inventory", "Retrieve OS inventory data from the Batfish snapshot."
)
get_logical_topology_with_mtu = create_batfish_tool(
    "get_logical_topology_with_mtu",
    "Check for underlying Layer 2/MTU mismatches on interfaces.",
)
get_ospf_failures = create_batfish_tool(
    "get_ospf_failures", "Detect OSPF adjacency failures or flapping on the network."
)
get_bgp_failures = create_batfish_tool(
    "get_bgp_failures", "Detect BGP session failures or misconfigurations."
)
get_undefined_references = create_batfish_tool(
    "get_undefined_references",
    "Rule out configuration errors like non-existent ACLs or interfaces.",
)
get_unused_structures = create_batfish_tool(
    "get_unused_structures", "Identify declared but unused configuration structures."
)
get_acl_shadowing = create_batfish_tool(
    "get_acl_shadowing", "Detect shadowed rules in Access Control Lists."
)
get_routing_loop_detection = create_batfish_tool(
    "get_routing_loop_detection", "Identify routing loops in the data-plane."
)

BATFISH_TOOLS_LIST = [
    get_device_os_inventory,
    get_logical_topology_with_mtu,
    get_ospf_failures,
    get_bgp_failures,
    get_undefined_references,
    get_unused_structures,
    get_acl_shadowing,
    get_routing_loop_detection,
]
