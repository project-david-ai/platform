from entities_api.platform_tools.definitions.read_web_page import read_web_page
from entities_api.platform_tools.definitions.scratch_pad.append_scratchpad import (
    append_scratchpad,
)
from entities_api.platform_tools.definitions.scratch_pad.read_scratchpad import (
    read_scratchpad,
)
from entities_api.platform_tools.definitions.web_search.perform_web_search import (
    perform_web_search,
)
from entities_api.platform_tools.definitions.web_search.scroll_web_page import (
    scroll_web_page,
)
from entities_api.platform_tools.definitions.web_search.search_web_page import (
    search_web_page,
)

WORKER_TOOLS = [
    read_web_page,
    scroll_web_page,
    perform_web_search,
    search_web_page,
    append_scratchpad,
    read_scratchpad,
]
