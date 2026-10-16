import io
import json
import contextlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/mcd-smart-basket/scripts"))
from mcp_readonly import Client, decode_response, NoRedirect, main


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


class ProtocolTests(unittest.TestCase):
    def test_json(self):
        result = decode_response(Response(b'{"id":1,"result":{"ok":true}}'), 1)
        self.assertTrue(result["result"]["ok"])

    def test_sse_skips_notifications_and_unrelated_responses(self):
        response = Response(b': ping\n\ndata: {"method":"notifications/message"}\n\ndata: {"id":2,"result":{}}\n\ndata: {"id":1,\ndata: "result":{"ok":true}}\n\n', "text/event-stream; charset=utf-8")
        self.assertTrue(decode_response(response, 1)["result"]["ok"])

    def test_truncated_stream_is_not_success(self):
        with self.assertRaises(RuntimeError):
            decode_response(Response(b': ping\n\n', "text/event-stream"), 1)

    def test_write_tool_blocked_before_network(self):
        client = Client("synthetic-test-token")
        for name in ("create-order", "auto-bind-coupons", "mall-create-order", "draw-lottery"):
            with self.assertRaises(ValueError):
                client.call(name, {})

    def test_redirect_never_forwards_authorization(self):
        with self.assertRaises(RuntimeError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.org")

    def test_pagination(self):
        client = Client("synthetic-test-token")
        calls = []
        def rpc(method, params):
            calls.append(params)
            return {"tools": [{"name": "first"}], "nextCursor": "next"} if not params else {"tools": [{"name": "second"}]}
        client.rpc = rpc
        self.assertEqual(len(client.list_tools()), 2)
        self.assertEqual(calls, [{}, {"cursor": "next"}])

    def test_tool_error_not_returned_as_quote(self):
        client = Client("synthetic-test-token")
        client.list_tools = lambda: [{"name": "calculate-price"}]
        client.rpc = lambda method, params: {"isError": True, "content": []}
        with self.assertRaises(RuntimeError):
            client.call("calculate-price", {})

    def test_business_failure_inside_successful_mcp_envelope_is_rejected(self):
        client = Client("synthetic-test-token")
        client.list_tools = lambda: [{"name": "calculate-price"}]
        client.rpc = lambda method, params: {"isError": False, "structuredContent": {"success": False, "code": 400, "data": None}}
        with self.assertRaises(RuntimeError):
            client.call("calculate-price", {})

    def test_setup_saves_only_after_validation_and_does_not_echo_token(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["client", "setup"]), \
                     patch.object(sys.stdin, "isatty", return_value=True), \
                     patch("mcp_readonly.getpass.getpass", return_value="synthetic-private-value"), \
                     patch("mcp_readonly.Client") as client, contextlib.redirect_stdout(io.StringIO()) as out:
                    client.return_value.list_tools.return_value = [{"name": "calculate-price"}]
                    main()
                    self.assertNotIn("synthetic-private-value", out.getvalue())
                    self.assertEqual(Path("private/mcd-token").read_text(), "synthetic-private-value")
                    self.assertEqual(Path("private/mcd-token").stat().st_mode & 0o777, 0o600)
            finally:
                os.chdir(original)

    def test_failed_setup_does_not_save_token(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["client", "setup"]), \
                     patch.object(sys.stdin, "isatty", return_value=True), \
                     patch("mcp_readonly.getpass.getpass", return_value="synthetic-private-value"), \
                     patch("mcp_readonly.Client") as client, contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()):
                    client.return_value.initialize.side_effect = RuntimeError("HTTP 401")
                    with self.assertRaises(SystemExit):
                        main()
                    self.assertFalse(Path("private/mcd-token").exists())
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
