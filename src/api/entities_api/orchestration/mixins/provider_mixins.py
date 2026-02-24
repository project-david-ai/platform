from entities_api.orchestration.mixins import ScratchpadMixin
from src.api.entities_api.orchestration.mixins import (
    AssistantCacheMixin,
    CodeExecutionMixin,
    ConsumerToolHandlersMixin,
    ConversationContextMixin,
    DelegationMixin,
    FileSearchMixin,
    JsonUtilsMixin,
    PlatformToolHandlersMixin,
    ServiceRegistryMixin,
    ShellExecutionMixin,
    ToolRoutingMixin,
    WebSearchMixin,
    NetworkInventoryMixin,
)


class _ProviderMixins(
    ServiceRegistryMixin,
    AssistantCacheMixin,
    JsonUtilsMixin,
    ConversationContextMixin,
    ToolRoutingMixin,
    PlatformToolHandlersMixin,
    ConsumerToolHandlersMixin,
    CodeExecutionMixin,
    ShellExecutionMixin,
    FileSearchMixin,
    WebSearchMixin,
    ScratchpadMixin,
    DelegationMixin,
    NetworkInventoryMixin,
):
    """Flat bundle for Provider Mixins."""
