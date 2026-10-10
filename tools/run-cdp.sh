#!/bin/bash
# 起一个带调试端口的无头 Chrome（若尚未在跑），然后跑 tools/cdp.js 的交互检查。
# 用法: bash tools/run-cdp.sh [页面URL]
set -u
export PATH="/usr/bin:/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${CDP_PORT:-9333}"
URL="${1:-http://127.0.0.1:8765/}"
NODE="C:/Users/jadezhang/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
CH="/c/Program Files/Google/Chrome/Application/chrome.exe"

alive() { curl -s -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; }

if ! alive; then
  echo "启动无头 Chrome (CDP :$PORT) → $URL"
  "$CH" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
        --remote-debugging-port="$PORT" --user-data-dir="$TEMP/cdp-profile" \
        --window-size=1600,1150 "$URL" >/dev/null 2>&1 &
  for _ in $(seq 1 30); do alive && break; sleep 0.4; done
fi
alive || { echo "Chrome 调试端口没起来"; exit 1; }

cd "$ROOT" && CDP_PORT="$PORT" PAGE_URL="$URL" "$NODE" tools/cdp.js
