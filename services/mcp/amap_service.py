import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient
from async_lru import alru_cache
from config import settings

_mcp_client = None

@alru_cache(maxsize=1)
async def get_amap_tools() -> dict:
    global _mcp_client

    if _mcp_client is None:
        _mcp_client = MultiServerMCPClient({
            "amap_maps": {
                "command": "npx",
                "args": ["-y", "@amap/amap-maps-mcp-server"],
                "transport": "stdio",
                "env": {
                    "AMAP_MAPS_API_KEY": settings.AMAP_API_KEY,
                },
            }
        })

    tools_list = await _mcp_client.get_tools()
    return {tool.name: tool for tool in tools_list}
