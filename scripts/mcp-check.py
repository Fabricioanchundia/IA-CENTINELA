"""Connect to Centinela's MCP server inside the cluster, exactly like an MCP client would.

    uv run python scripts/mcp-check.py

Lists the tools, reads the latest incidents, and checks that a forbidden proposal (restart
the database) is refused by the policy. It never proposes anything real.
"""

import asyncio

from mcp import Client, StdioServerParameters

SERVER = StdioServerParameters(
    command="kubectl",
    args=[
        "-n",
        "centinela",
        "exec",
        "-i",
        "deploy/centinela-mcp",
        "-c",
        "mcp",
        "--",
        "centinela",
        "mcp",
    ],
)


async def main() -> None:
    async with Client(SERVER) as client:
        tools = (await client.list_tools()).tools
        print("tools:", ", ".join(t.name for t in tools))
        result = await client.call_tool("list_incidents", {"limit": 3})
        for incident in (result.structured_content or {}).get("incidents", []):
            print(f"  incident {incident['id']:>3}  {incident['status']:8}  {incident['title']}")
        refused = await client.call_tool(
            "propose_action",
            {
                "incident_id": 1,
                "kind": "restart",
                "target": "postgres",
                "reason": "policy check from scripts/mcp-check.py",
            },
        )
        text = refused.content[0].text if refused.content else ""
        print(
            "restart postgres ->",
            "REFUSED" if refused.is_error else "ACCEPTED (bug!)",
            f"({text[:80]})",
        )


if __name__ == "__main__":
    asyncio.run(main())
