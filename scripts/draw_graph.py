"""
Created on 2026-10-22
@author: amacedo

Description
-----------
Draw the compiled application graph to docs/images/graph.png.

    python scripts/draw_graph.py

Needs Node (the MCP server lists its tools), but no browser and no LLM server.
LangGraph renders the PNG through mermaid.ink: only the Mermaid text (node
names and edges) is sent.
"""

#%% IMPORTS

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_mcp_adapters.tools import load_mcp_tools
from browser_tools import make_browser_client
from graph import build_graph

OUT = ROOT / 'docs' / 'images' / 'graph.png'


#%% DRAW

async def main():
    async with make_browser_client().session('playwright') as session:
        tools = await load_mcp_tools(session)
        graph = build_graph(tools, middleware=[])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(graph.get_graph().draw_mermaid_png())
    print(f"wrote {OUT.relative_to(ROOT)}")


#%% RUN

if __name__ == '__main__':
    asyncio.run(main())


#%%
