"""OpenAI Agents SDK client for the local GPT Spine MCP server."""
from __future__ import annotations

import asyncio
import sys


async def _run(prompt: str, model: str) -> str:
    try:
        from agents import Agent, Runner
        from agents.mcp import MCPServerStdio
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI Agents SDK support is optional. Install it with "
            "`uv tool install 'gpt-spine-mcp[agents]'`."
        ) from exc

    params = {"command": sys.executable, "args": ["-m", "server"]}
    async with MCPServerStdio(name="GPT Spine", params=params, cache_tools_list=True) as server:
        agent = Agent(
            name="GPT Spine Director",
            model=model,
            instructions=(
                "Create production-ready Spine 2D assets. Inspect sources before rigging, "
                "use explicit output paths, validate every result, and report any operation "
                "that needs the licensed Spine CLI instead of claiming it succeeded."
            ),
            mcp_servers=[server],
        )
        result = await Runner.run(agent, prompt)
        return result.final_output


def run_agent(prompt: str, model: str) -> str:
    return asyncio.run(_run(prompt, model))
