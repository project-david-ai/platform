from entities_api.orchestration.mixins import ScratchpadMixin
from src.api.entities_api.orchestration.mixins import (
    AssistantCacheMixin, CodeExecutionMixin, ConsumerToolHandlersMixin,
    ConversationContextMixin, DelegationMixin, FileSearchMixin, JsonUtilsMixin,
    PlatformToolHandlersMixin, ServiceRegistryMixin, ShellExecutionMixin,
    ToolRoutingMixin, WebSearchMixin)


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
):
    """Flat bundle for Provider Mixins."""
