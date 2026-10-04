from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_lists_gpt_workflow_tools():
    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "server"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"build_workflow", "validate_output", "spine_doctor"}.issubset(names)

    asyncio.run(check())
