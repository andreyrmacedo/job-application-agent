#!/usr/bin/env bash
# Install a pinned Playwright MCP and patch it:
#  1. selectTab() no longer calls bringToFront()
#     (that makes GNOME switch your workspace)
#  2. keep Playwright's focus emulation
#     (noDefaults: false), so background tabs draw
# It stops if the code it patches has moved.
set -euo pipefail

MCP_VERSION=0.0.83
DIR="${1:-$HOME/.local/share/playwright-mcp-patched}"
PKG="@playwright/mcp@$MCP_VERSION"

npm install --prefix "$DIR" --no-save --silent "$PKG"

LIB="$DIR/node_modules/playwright-core/lib"
BUNDLE="$LIB/coreBundle.js"
CLI="$DIR/node_modules/@playwright/mcp/cli.js"

MARK='patched: keep focus emulation'
if grep -q "$MARK" "$BUNDLE"; then
  echo "already patched: $CLI"
  exit 0
fi

# Patch 1: no bringToFront in selectTab
OLD1='^        await tab2.page.bringToFront();$'
NEW1='        // patched: no bringToFront'
n=$(grep -c "$OLD1" "$BUNDLE" || true)
if [ "$n" -lt 1 ]; then
  echo "bringToFront line not found: not patched"
  exit 1
fi
sed -i "s|$OLD1|$NEW1|" "$BUNDLE"

# Patch 2: keep focus emulation
OLD2='^    noDefaults: true,$'
NEW2="    noDefaults: false, // $MARK"
n=$(grep -c "$OLD2" "$BUNDLE" || true)
if [ "$n" -ne 1 ]; then
  echo "noDefaults line found $n times: not patched"
  exit 1
fi
sed -i "s|$OLD2|$NEW2|" "$BUNDLE"

echo "patched MCP: $CLI"
