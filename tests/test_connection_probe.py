import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("connection_probe", Path(__file__).resolve().parents[1] / "benchmarks/connection_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ConnectionProbeTests(unittest.TestCase):
    def test_stage_accounting_and_resource_cleanup_without_network(self):
        raw = MagicMock()
        tls = MagicMock()
        tls.recv.side_effect = [b"H", b"TTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", b""]
        ctx = MagicMock()
        ctx.wrap_socket.return_value = tls
        with patch.object(probe.socket, "getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]), patch.object(probe.socket, "socket", return_value=raw), patch.object(probe.ssl, "create_default_context", return_value=ctx), patch.object(probe.time, "perf_counter", side_effect=[0, .001, .003, .006, .010, .015]):
            result = probe.measure_cold("https://example.invalid/version")
        self.assertEqual(result["status"], 200)
        self.assertIsNone(result["error"])
        stages = [result[k] for k in ("dns_ms", "tcp_ms", "tls_setup_handshake_ms", "request_first_byte_ms", "remaining_receive_ms")]
        self.assertAlmostEqual(sum(stages), result["total_ms"])
        self.assertTrue(tls.sendall.call_args.args[0].startswith(b"GET /version HTTP/1.1"))
        tls.close.assert_called_once()

    def test_bad_inputs_do_not_create_socket(self):
        with patch.object(probe.socket, "socket") as create:
            for url in ("http://example.invalid/", "https://user:pass@example.invalid/", "https://example.invalid/\r\nX:evil", "https://example.invalid/#fragment"):
                with self.assertRaises(ValueError):
                    probe.measure_cold(url)
            create.assert_not_called()

    def test_failed_connection_keeps_failure_and_duration(self):
        with patch.object(probe.socket, "getaddrinfo", side_effect=OSError("test failure")):
            result = probe.measure_cold("https://example.invalid/")
        self.assertIn("OSError", result["error"])
        self.assertGreaterEqual(result["total_ms"], 0)
        self.assertIsNone(result["status"])

    def test_empty_response_is_failure_and_closed(self):
        raw, tls, ctx = MagicMock(), MagicMock(), MagicMock()
        tls.recv.return_value = b""
        ctx.wrap_socket.return_value = tls
        with patch.object(probe.socket, "getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]), patch.object(probe.socket, "socket", return_value=raw), patch.object(probe.ssl, "create_default_context", return_value=ctx):
            result = probe.measure_cold("https://example.invalid/")
        self.assertIn("without an HTTP response", result["error"])
        tls.close.assert_called_once()

    def test_warm_burst_reuses_client_and_retains_failure(self):
        class Client:
            def __init__(self):
                self.calls = 0

            async def get(self, path, timeout):
                self.calls += 1
                if self.calls == 2:
                    raise TimeoutError("test")
                return SimpleNamespace(status_code=200, content=b"ok")

        client = Client()
        rows = asyncio.run(probe.measure_warm_burst(client, "/version", 3))
        self.assertEqual(client.calls, 3)
        self.assertEqual([r["status"] for r in rows], [200, None, 200])
        self.assertEqual(rows[1]["error"], "TimeoutError")
        self.assertEqual(probe.summarize([1, 3])["median_ms"], 2)
        self.assertNotIn("p99", probe.summarize([1, 3]))


if __name__ == "__main__":
    unittest.main()
