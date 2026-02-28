from entities_api.orchestration.mixins import ScratchpadMixin
from src.api.entities_api.orchestration.mixins import (
    AssistantCacheMixin, BatfishMixin, CodeExecutionMixin,
    ConsumerToolHandlersMixin, ContextMixin, DelegationMixin, FileSearchMixin,
    JsonUtilsMixin, NetworkInventoryMixin, PlatformToolHandlersMixin,
    ServiceRegistryMixin, ShellExecutionMixin, ToolRoutingMixin,
    WebSearchMixin)


class _ProviderMixins(
    ServiceRegistryMixin,
    AssistantCacheMixin,
    JsonUtilsMixin,
    ContextMixin,
    DelegationMixin,
    BatfishMixin,
    ToolRoutingMixin,
    PlatformToolHandlersMixin,
    ConsumerToolHandlersMixin,
    CodeExecutionMixin,
    ShellExecutionMixin,
    FileSearchMixin,
    WebSearchMixin,
    ScratchpadMixin,
    NetworkInventoryMixin,
):
    """Flat bundle for Provider Mixins."""
