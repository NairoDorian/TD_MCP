"""Minimal stdio MCP doc-RAG server used by the integration tests to
prove multi-process RAG fusion. Returns a synthetic remote doc so
RemoteMCPStrategy can be exercised without a real external server.
"""

import anyio

import mcp.types as types
from mcp.server import Server

app = Server("fake-remote-rag")


@app.list_tools()
async def list_tools():
    return [types.Tool("search_touchdesigner_docs", "fake remote",
                       {"query": {"type": "string"}})]


@app.call_tool()
async def call_tool(name, arguments):
    q = (arguments or {}).get("query", "")
    text = (f"FAKE REMOTE RESULT for: {q}\n"
            "This synthetic document comes from an external RAG server "
            "and is fused with the local KB by Reciprocal Rank Fusion.")
    return [types.TextContent(type="text", text=text)]


async def run():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (r, w):
        await app.run(r, w, app.create_initialization_options())


if __name__ == "__main__":
    anyio.run(run)
