"""Client factory: USE_MOCKS picks mock vs real implementations."""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class Clients:
    drex: object
    ndi: object
    claude: object
    mock: bool


def get_clients(use_mocks: bool | None = None) -> Clients:
    mock = config.USE_MOCKS if use_mocks is None else use_mocks
    if mock:
        from src.clients.claude_client import MockClaudeClient
        from src.clients.drex_client import MockDrexClient
        from src.clients.ndi_client import MockNDIClient

        return Clients(MockDrexClient(), MockNDIClient(), MockClaudeClient(), True)
    from src.clients.claude_client import ClaudeClient
    from src.clients.drex_client import DrexClient
    from src.clients.ndi_client import NDIClient

    return Clients(DrexClient(), NDIClient(), ClaudeClient(), False)
