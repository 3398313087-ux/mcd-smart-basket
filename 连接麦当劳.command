#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
python3 skills/mcd-smart-basket/scripts/mcp_readonly.py setup
echo
read -r "reply?按回车关闭窗口…"
