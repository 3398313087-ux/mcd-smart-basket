#!/usr/bin/env python3
"""Small read-only Streamable HTTP client; token stays in memory, endpoint fixed."""
import argparse
import getpass
import json
import os
import ssl
from pathlib import Path
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://mcp.mcd.cn"
ALLOWED = frozenset({"query-nearby-stores", "delivery-query-addresses",
                     "delivery-query-stores", "query-store-coupons", "query-my-coupons",
                     "query-meals", "query-meal-detail", "calculate-price", "now-time-info"})
MAX_BYTES = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("服务返回重定向；已停止，未转发凭据")


def decode_response(response, request_id):
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/event-stream" not in content_type:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise RuntimeError("响应超过大小上限")
        return json.loads(raw)
    data, size = [], 0
    while True:
        line = response.readline(MAX_BYTES + 1)
        size += len(line)
        if size > MAX_BYTES:
            raise RuntimeError("事件流超过大小上限")
        if not line or line in (b"\n", b"\r\n"):
            if data:
                message = json.loads("\n".join(data))
                data = []
                if message.get("id") == request_id and ("result" in message or "error" in message):
                    return message
            if not line:
                raise RuntimeError("事件流未返回对应请求结果")
        elif line.startswith(b"data:"):
            data.append(line[5:].decode("utf-8").lstrip(" ").rstrip("\r\n"))


class Client:
    def __init__(self, token):
        if not token or "\n" in token or "\r" in token:
            raise ValueError("未提供有效 Token")
        self.token, self.session, self.protocol, self.counter = token, None, None, 0
        self._tool_names = None
        context = ssl.create_default_context()
        # python.org macOS builds may have no bundled CA file. Use the OS CA bundle;
        # hostname and certificate verification remain enabled.
        if sys.platform == "darwin" and not ssl.get_default_verify_paths().cafile and Path("/etc/ssl/cert.pem").is_file():
            context.load_verify_locations(cafile="/etc/ssl/cert.pem")
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def rpc(self, method, params, notification=False):
        self.counter += 1
        request_id = self.counter
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = request_id
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                if notification:
                    return None
                message = decode_response(response, request_id)
        except urllib.error.HTTPError as exc:
            hints = {401: "Token 无效或过期，请在官方平台重新获取", 429: "请求过于频繁，请稍后重试"}
            raise RuntimeError(f"HTTP {exc.code}：{hints.get(exc.code, '服务请求失败；未自动重试')}") from None
        except urllib.error.URLError:
            raise RuntimeError("网络连接失败，请检查连接后重试") from None
        if message.get("id") != request_id:
            raise RuntimeError("响应请求编号不匹配")
        if "error" in message:
            raise RuntimeError("MCP 请求失败，错误码：" + str(message["error"].get("code")))
        return message["result"]

    def initialize(self):
        self._tool_names = None
        result = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                          "clientInfo": {"name": "mcd-smart-basket-readonly", "version": "0.1.0"}})
        self.protocol = result["protocolVersion"]
        if self.protocol not in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
            raise RuntimeError("服务协商了尚未支持的协议版本")
        self.rpc("notifications/initialized", {}, notification=True)

    def list_tools(self):
        result, cursor, seen = [], None, set()
        for _ in range(50):
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            result.extend(page["tools"])
            cursor = page.get("nextCursor")
            if not cursor:
                return result
            if cursor in seen:
                raise RuntimeError("工具列表分页游标重复")
            seen.add(cursor)
        raise RuntimeError("工具列表超过分页上限")

    def call(self, name, arguments):
        if name not in ALLOWED:
            raise ValueError("此客户端仅允许查询和计价，不允许下单、领券、兑换或支付")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须为 JSON 对象")
        if self._tool_names is None:
            self._tool_names = {t["name"] for t in self.list_tools()}
        if name not in self._tool_names:
            raise ValueError("当前服务未提供该工具")
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError("官方工具返回失败，不能视为可用报价；请检查当前 inputSchema 和参数")
        business = result.get("structuredContent")
        if isinstance(business, dict) and business.get("success") is False:
            code = business.get("code")
            suffix = f"（业务码 {code}）" if type(code) is int else ""
            raise RuntimeError("官方业务处理未成功" + suffix + "；不能视为有效菜单或报价")
        return result


def main():
    parser = argparse.ArgumentParser(description="连接麦当劳官方 MCP，只读查询和整单计价。参数须依据实时 inputSchema。")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tools")
    sub.add_parser("setup", help="隐藏输入并验证 Token，成功后仅保存在本机 private/ 中")
    call = sub.add_parser("call")
    call.add_argument("tool", choices=sorted(ALLOWED))
    call.add_argument("arguments", help="按实时 inputSchema 编写的 JSON 参数文件")
    args = parser.parse_args()
    token = os.environ.get("MCD_MCP_TOKEN")
    local_token = Path("private/mcd-token")
    if not token and args.command != "setup" and local_token.is_file():
        token = local_token.read_text(encoding="utf-8").strip()
    if args.command == "setup":
        print("将连接麦当劳官方 MCP，读取工具列表验证连接。")
        print("验证成功后，Token 仅保存在当前项目 private/mcd-token，权限设为仅本用户读写。")
        print("该目录已排除在 Git 提交之外；不会下单。可按 Ctrl+C 取消。")
    if not token and sys.stdin.isatty():
        token = getpass.getpass("麦当劳 MCP Token（隐藏输入，仅用于本次进程）：")
    if not token:
        parser.exit(2, "请在本机设置 MCD_MCP_TOKEN，或在交互终端隐藏输入。不要发到聊天或提交仓库。\n")
    try:
        client = Client(token)
        client.initialize()
        result = client.list_tools() if args.command in ("tools", "setup") else client.call(
            args.tool, json.loads(Path(args.arguments).read_text(encoding="utf-8")))
        if args.command == "setup":
            directory = Path("private")
            if directory.is_symlink():
                raise RuntimeError("private 目录不能为符号链接")
            directory.mkdir(mode=0o700, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(local_token, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(token)
            (directory / "tools.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"连接验证成功，共发现 {len(result)} 个工具。Token 已保存在本机；没有输出到聊天。")
            print("可以回到聊天告诉助手：麦当劳 MCP 已连接。")
            return
        # Responses may contain private data: user selects local destination; never log headers/token.
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        # Do not echo arbitrary server bodies or supplied arguments on error.
        message = str(exc) if isinstance(exc, RuntimeError) else "输入或响应格式错误，请检查本机文件"
        parser.exit(2, message + "\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\n已取消。")
