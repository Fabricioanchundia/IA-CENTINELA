"""Propose `scale_up payments` for an incident through Centinela's MCP server (end-to-end test).

    uv run python scripts/e2e-propose.py INCIDENT_ID

Used by scripts/e2e.sh when the small CI model did not propose the fix itself. It goes through
exactly the path another AI assistant would use: the MCP server, its policy checks and its
database role that can only create *pending* proposals.
"""

import asyncio
import sys

from mcp import Client, StdioServerParameters

SERVER = StdioServerParameters(
    command="kubectl",
    args=["-n", "centinela", "exec", "-i", "deploy/centinela-mcp", "-c", "mcp", "--"]
    + ["centinela", "mcp"],
)


async def main(incident_id: int) -> int:
    async with Client(SERVER) as client:
        result = await client.call_tool(
            "propose_action",
            {
                "incident_id": incident_id,
                "kind": "scale_up",
                "target": "payments",
                "reason": "end-to-end test: payments has 0 replicas and ServiceDown is firing",
            },
        )
        text = result.content[0].text if result.content else ""
        print("    MCP propose_action:", "refused" if result.is_error else "accepted", text[:120])
        return 1 if result.is_error else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]))))
