"""
Created on 2026-09-01
@author: amacedo

Description
-----------
This script gathers MCP tools/fuctions from playwright,
and the helpers that drive the MCP's 'current' tab without an LLM.
"""

#%% IMPORTS

import re
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

from langchain_mcp_adapters.client import MultiServerMCPClient
from settings import BROWSER_URL, SKIP_LINKS

#%% MCP TOOLS

MCP_VERSION = '0.0.83' # the version scripts/patch_mcp.sh pins and patches

# Built by scripts/patch_mcp.sh: no bringToFront, background tabs keep drawing
PATCHED_MCP = (
    Path.home()
    / '.local/share/playwright-mcp-patched'
    / 'node_modules/@playwright/mcp/cli.js'
)

# LOGIN_PASSWORD=... lives here. The agent types the NAME; the MCP types the value
SECRETS_FILE = Path(__file__).resolve().parent / '.env.secrets'


def make_browser_client(cdp_endpoint=BROWSER_URL):
    args = ["--cdp-endpoint", cdp_endpoint]
    if SECRETS_FILE.exists():
        args += ["--secrets", str(SECRETS_FILE)]

    # The patched build if you installed it, the stock pinned one otherwise
    if PATCHED_MCP.exists():
        command, args = "node", [str(PATCHED_MCP), *args]
    else:
        command, args = "npx", ["-y", f"@playwright/mcp@{MCP_VERSION}", *args]

    client = MultiServerMCPClient({
        "playwright": {"transport": "stdio", "command": command, "args": args}
    })

    return client

"""
OBSERVATION
-----------
We want 'cdp-endpoint' directing the tools to the browser already running.
"""


async def get_browser_tools(cdp_endpoint=BROWSER_URL):
    client = make_browser_client(cdp_endpoint)
    return await client.get_tools()

#%% TAB HELPERS

# A tab line from browser_tabs 'list' looks like:  - 3: (current) [Title](https://...)
TAB_LINE = re.compile(r'- (\d+): (\(current\) )?\[(.*)\]\((.*)\)')


class Tab(NamedTuple):
    index: int
    title: str
    url: str
    current: bool


async def list_tabs(tabs_tool):
    """
    Ask the MCP server which tabs are open.
    Returns a list of Tab, in the server's order.
    """
    result = await tabs_tool.ainvoke({'action': 'list'})

    # The tool returns content blocks: [{'type': 'text', 'text': '### Result\\n- 0: ...'}]
    text = '\n'.join(block['text'] for block in result if block.get('type') == 'text')

    tabs = []
    for line in text.splitlines():
        match = TAB_LINE.fullmatch(line.strip())
        if match:                                # skip headers and blank lines
            index, current, title, url = match.groups()
            tabs.append(Tab(int(index), title, url, current is not None))
    return tabs


async def current_tab(tabs_tool):
    """
    The tab every agent tool acts on (None if there is none).
    """
    for tab in await list_tabs(tabs_tool):
        if tab.current:
            return tab
    return None


def is_target(url):
    """
    True for tabs the loop should process: http(s) pages not on a skipped host.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):   # about:blank, chrome://, ...
        return False

    # endswith also catches subdomains such as merchantpool1.linkedin.com
    host = parsed.hostname or ''
    return not any(host == h or host.endswith('.' + h) for h in SKIP_LINKS)


async def select_tab(tabs_tool, url):
    """
    Make the tab with this URL the MCP's current tab.
    Returns True if the switch worked, False if the tab is gone.
    """
    matches = [tab for tab in await list_tabs(tabs_tool) if tab.url == url]
    if not matches:
        return False                             # no open tab has this URL

    await tabs_tool.ainvoke({'action': 'select', 'index': matches[0].index})

    # Don't trust the call: check which tab is current now
    now = await current_tab(tabs_tool)
    return now is not None and now.url == url

#%%
