"""Run the real CLI and stdio MCP server against a local provider fixture."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from web_retrieval_research.client import ENDPOINTS


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".venv/bin/web-research"
EXPECTED = {(provider, operation): path for provider, entries in ENDPOINTS.items() for operation, path in entries.items()}
calls: list[dict] = []


class Fixture(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        auth = self.headers.get("Authorization") or self.headers.get("x-api-key")
        calls.append({"path": self.path, "request": body, "auth_valid": auth in {"Bearer fixture-key", "fixture-key"}})
        if self.path == "/v2/scrape" and body.get("url") == "https://example.test/failure":
            status, response = 429, {"success": False, "error": "fixture rate limit"}
        elif self.path == "/v1/web/scrape" and body.get("url") == "https://example.test/embedded-error":
            status, response = 200, {"highlights": {"requested": True, "success": False, "data": None}}
        else:
            status, response = 200, {"fixture": True, "path": self.path, "echo": body}
        encoded = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_: object) -> None:
        pass


async def check_mcp(env: dict[str, str]) -> dict:
    params = StdioServerParameters(command=str(BIN), args=["serve"], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            expected_names = sorted(f"{provider}_{operation}" for provider, operation in EXPECTED)
            assert names == expected_names, (names, expected_names)
            response = await session.call_tool("exa_contents", {"request": {"urls": ["https://example.test/page"], "maxAgeHours": 0}})
            assert not response.isError, response
            result = response.structuredContent or json.loads(response.content[0].text)
            assert result["status_code"] == 200, result
            assert result["response"]["path"] == "/contents", result
            return {"tools": names, "call": result}


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    for provider in ENDPOINTS:
        env[f"{provider.upper()}_API_KEY"] = "fixture-key"
        env[f"WEB_RESEARCH_{provider.upper()}_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    traces = []
    try:
        for provider, operation in EXPECTED:
            request = {"query": "fixture question"} if operation == "search" else {"url": "https://example.test/page", "probe": operation}
            if operation == "contents" or operation == "extract":
                request = {"urls": ["https://example.test/page"], "probe": operation}
            run = subprocess.run(
                [str(BIN), "call", provider, operation, "--json", json.dumps(request)],
                env=env, capture_output=True, text=True, check=True, timeout=15,
            )
            trace = json.loads(run.stdout)
            assert trace["response"] == {"fixture": True, "path": EXPECTED[(provider, operation)], "echo": request}, trace
            assert trace["request"] == request and trace["error"] is None, trace
            assert "fixture-key" not in run.stdout
            traces.append(trace)
        failure = subprocess.run(
            [str(BIN), "call", "firecrawl", "scrape", "--json", '{"url":"https://example.test/failure"}'],
            env=env, capture_output=True, text=True, timeout=15,
        )
        assert failure.returncode == 1, failure
        failed_trace = json.loads(failure.stdout)
        assert failed_trace["status_code"] == 429 and failed_trace["response"]["error"] == "fixture rate limit"
        embedded = subprocess.run(
            [str(BIN), "call", "context", "scrape", "--json", '{"url":"https://example.test/embedded-error","formats":{"highlights":true}}'],
            env=env, capture_output=True, text=True, check=True, timeout=15,
        )
        embedded_trace = json.loads(embedded.stdout)
        assert embedded_trace["status_code"] == 200 and embedded_trace["response"]["highlights"]["success"] is False
        missing_env = env | {"EXA_API_KEY": ""}
        missing = subprocess.run(
            [str(BIN), "call", "exa", "contents", "--json", '{"urls":["https://example.test/page"]}'],
            env=missing_env, capture_output=True, text=True, timeout=15,
        )
        assert missing.returncode != 0 and "Set EXA_API_KEY" in missing.stderr
        mcp = asyncio.run(check_mcp(env))
        assert len(calls) == 11 and all(call["auth_valid"] for call in calls), calls
        artifact = {
            "protocol": "local HTTP fixture through installed CLI and stdio MCP server",
            "calls": calls,
            "traces": traces,
            "http_failure": failed_trace,
            "embedded_failure": embedded_trace,
            "missing_key_rejected": True,
            "mcp": mcp,
        }
        output = ROOT / "artifacts/e2e-trace.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(artifact, indent=2) + "\n")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        print(f"PASS: 8 provider operations, HTTP/embedded failures, missing key, 8 MCP tools, MCP call; artifact={output}; sha256={digest}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
