from src.api.entities_api.platform_tools.definitions.delegation.delegate_engineer_task import \
    delegate_engineer_task
from src.api.entities_api.platform_tools.definitions.network_inventory.get_device_info import \
    get_device_info
from src.api.entities_api.platform_tools.definitions.network_inventory.search_inventory_by_group import \
    search_inventory_by_group
from src.api.entities_api.platform_tools.definitions.scratch_pad.read_scratchpad import \
    read_scratchpad
from src.api.entities_api.platform_tools.definitions.scratch_pad.update_scratchpad import \
    update_scratchpad

# ============================
# Senior Network Engineer
# Tools Array
# Order mirrors execution flow:
#   1. Discover → 2. Resolve → 3. Plan → 4. Monitor → 5. Delegate
# ============================
SENIOR_ENGINEER_TOOLS = [
    search_inventory_by_group,  # 1. Discovery — find candidate devices by group/role when hostname is unknown
    get_device_info,  # 2. Resolution — fetch full metadata for a specific hostname before delegating
    update_scratchpad,  # 3. Planning — Senior OWNS the scratchpad: writes [INCIDENT], [PLAN], [FINDING], [TOMBSTONE]
    read_scratchpad,  # 4. Monitoring — reads Junior's appended ✅/🚩/⚠️ entries after each delegation returns
    delegate_engineer_task,  # 5. Delegation — dispatches a focused command set to the Junior Engineer
]
