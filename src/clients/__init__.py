"""Client factory: USE_MOCKS picks mock vs real implementations."""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class Clients:
    drex: object  # thin adapter over intern.decisions.DecisionClient (Level A)
    ndi: object
    claude: object
    mock: bool
    deepinfra: object | None = None  # partner-tool LLM baseline (Qwen on DeepInfra); None when no key


def get_clients(use_mocks: bool | None = None, need_deepinfra: bool = False) -> Clients:
    mock = config.USE_MOCKS if use_mocks is None else use_mocks
    if mock:
        from src.clients.claude_client import MockClaudeClient
        from src.clients.deepinfra_client import MockDeepInfraClient
        from src.clients.drex_client import MockDrexClient
        from src.clients.ndi_client import MockNDIClient

        return Clients(MockDrexClient(), MockNDIClient(), MockClaudeClient(), True, MockDeepInfraClient())
    from src.clients.claude_client import ClaudeClient
    from src.clients.deepinfra_client import DeepInfraClient
    from src.clients.drex_client import DrexClient
    from src.clients.ndi_client import NDIClient

    di = None
    if config.DEEPINFRA_API_KEY or need_deepinfra:
        di = DeepInfraClient()  # raises a clear error naming DEEPINFRA_API_KEY when it is needed but missing
    return Clients(DrexClient(), NDIClient(), ClaudeClient(), False, di)
