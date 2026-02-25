# src/api/entities_api/constants/junior_network_engineer.py

from src.api.entities_api.platform_tools.definitions.network_device_commands.execute_network_command import \
    execute_network_command
from src.api.entities_api.platform_tools.definitions.scratch_pad.append_scratchpad import \
    append_scratchpad

# ============================
# Junior Network Engineer
# Tools Array
# Order mirrors execution flow:
#   1. Discover → 2. Resolve → 3. Plan → 4. Monitor → 5. Delegate
# ============================
JUNIOR_ENGINEER_TOOLS = [execute_network_command, append_scratchpad]
