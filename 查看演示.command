#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
python3 examples/demo.py
echo
read -r "reply?按回车关闭演示窗口…"
