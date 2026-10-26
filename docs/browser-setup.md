# Browser setup

The agent drives a browser you start yourself, over the Chrome DevTools Protocol (CDP).

## Start it

```bash
brave-browser --remote-debugging-port=9222 --user-data-dir="$HOME/.brave-agent"
```

Use a dedicated `--user-data-dir`: logins you make there stay there, and the agent never sees
your everyday profile.

## Check it before a run

`curl localhost:9222/json/version` answers even from a wedged browser. Test a real CDP attach:

```bash
python -c "
import asyncio
from playwright.async_api import async_playwright
async def m():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp('http://localhost:9222', timeout=15000)
        print('OK —', sum(len(c.pages) for c in b.contexts), 'tabs')
        await b.close()
asyncio.run(m())"
```

If it times out, one tab's renderer has usually hung. Close tabs, or restart the browser.

## Keep working while it runs (Linux, GNOME)

Measured on GNOME Wayland:

- **A Wayland-native browser on a hidden workspace draws 0 frames, and every click times out.**
  Start it as an X11 client so it keeps drawing:

  ```bash
  brave-browser --ozone-platform=x11 --remote-debugging-port=9222 --user-data-dir="$HOME/.brave-agent"
  ```

- **Switching tabs calls `bringToFront()`, and GNOME then pulls you to the browser's
  workspace.** `scripts/patch_mcp.sh` installs Playwright MCP 0.0.83 with that call removed. It
  also keeps focus emulation on, so background tabs still draw. `browser_tools.py` uses the
  patched build automatically when it's installed.

- **Leave a "parking" tab in front.** The skipped LinkedIn tab works. Park the window on another
  workspace; don't minimize it.

## How the agent sees tabs

- **One MCP session for the whole run.** Element refs (`e48`) only live inside the MCP process
  that took the snapshot.
- **Every tool acts on the MCP's current tab.** The runner moves it with `browser_tabs select`.
  A click that opens a new tab doesn't move it, so the Navigator node follows new tabs itself.
- **Tabs run one at a time.** One session has one current tab.
