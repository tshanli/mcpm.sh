"""Exercise the dependency upgrade against real MCP transports and middleware."""

import asyncio
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.server.middleware import MiddlewareContext
from starlette.requests import Request

from mcpm.core.schema import RemoteServerConfig, STDIOServerConfig
from mcpm.fastmcp_integration.middleware import MCPMAuthMiddleware
from mcpm.fastmcp_integration.proxy import MCPMProxyFactory
from mcpm.monitor.base import AccessEventType, AccessMonitor


@pytest.fixture
def backend_script(tmp_path, monkeypatch):
    # SSE's shutdown event must belong to the current test's event loop.
    monkeypatch.setattr("sse_starlette.sse.AppStatus.should_exit_event", None)
    script = tmp_path / "backend.py"
    script.write_text(
        "import os\n"
        "from fastmcp import FastMCP\n"
        "server = FastMCP('upgrade-test')\n"
        "@server.tool\n"
        "def echo(value: str) -> str:\n"
        "    return os.environ['TEST_PREFIX'] + value\n"
        "@server.resource('test://message')\n"
        "def message() -> str:\n"
        "    return 'resource content'\n"
        "@server.prompt\n"
        "def greet(name: str) -> str:\n"
        "    return 'Hello ' + name\n"
        "server.run(show_banner=False)\n"
    )
    return script


def stdio_server(script, name="backend"):
    return STDIOServerConfig(
        name=name, command=sys.executable, args=[str(script)], env={"TEST_PREFIX": f"{name}:"}
    )


@asynccontextmanager
async def serve_http(proxy, transport):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(proxy.http_app(transport=transport), log_level="error"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}/{'sse' if transport == 'sse' else 'mcp'}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("server_names", [("one",), ("one", "two")])
async def test_stdio_proxy_roundtrip(backend_script, server_names):
    monitor = AsyncMock(spec=AccessMonitor)
    factory = MCPMProxyFactory(access_monitor=monitor)
    proxy = await factory.create_proxy_for_servers([stdio_server(backend_script, name) for name in server_names])

    async with Client(proxy) as client:
        names = {tool.name for tool in await client.list_tools()}
        for name in server_names:
            prefix = f"{name}_" if len(server_names) > 1 else ""
            assert prefix + "echo" in names
            result = await client.call_tool(prefix + "echo", {"value": "hello"})
            assert result.content[0].text == f"{name}:hello"
            prompt = await client.get_prompt(prefix + "greet", {"name": "MCPM"})
            assert prompt.messages[0].content.text == "Hello MCPM"
        resources = await client.list_resources()
        assert len(resources) == len(server_names)
        for resource in resources:
            content = await client.read_resource(str(resource.uri))
            assert content[0].text == "resource content"

    await proxy._mcpm_unified_middleware.track_session_end()
    events = [call.kwargs["event_type"] for call in monitor.track_event.await_args_list]
    assert AccessEventType.SESSION_START in events
    assert AccessEventType.TOOL_INVOCATION in events
    assert AccessEventType.RESOURCE_ACCESS in events
    assert AccessEventType.PROMPT_EXECUTION in events
    assert AccessEventType.SESSION_END in events


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_remote_proxy_auth_roundtrip(backend_script, transport):
    factory = MCPMProxyFactory(auth_enabled=True, api_key="test-secret", access_monitor=AsyncMock(spec=AccessMonitor))
    upstream = await factory.create_proxy_for_servers([stdio_server(backend_script)], stdio_mode=False)
    async with serve_http(upstream, transport) as url:
        remote = RemoteServerConfig(name="remote", url=url, headers={"Authorization": "Bearer test-secret"})
        downstream = await MCPMProxyFactory(access_monitor=AsyncMock(spec=AccessMonitor)).create_proxy_for_servers(
            [remote]
        )
        async with Client(downstream, timeout=10, init_timeout=10) as client:
            result = await client.call_tool("echo", {"value": "remote"})
            assert result.content[0].text == "backend:remote"


def test_cli_startup_has_no_dependency_deprecation_warnings():
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "always",
            "-c",
            "import fastmcp.server.auth.providers.jwt; "
            "import authlib.integrations.httpx_client; "
            "from mcpm.cli import main; main()",
            "--help",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "AuthlibDeprecationWarning" not in result.stderr
    assert "FastMCPDeprecationWarning" not in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Bearer test-secret", "bearer test-secret", "test-secret"])
async def test_auth_middleware_reads_authorization_header(authorization):
    headers = [] if authorization is None else [(b"authorization", authorization.encode())]
    request = Request({"type": "http", "headers": headers})
    context = MiddlewareContext(message={}, method="tools/list")
    call_next = AsyncMock(return_value=[])

    with patch("fastmcp.server.dependencies.get_http_request", return_value=request):
        middleware = MCPMAuthMiddleware("test-secret")
        if authorization in (None, "Bearer wrong"):
            with pytest.raises(ValueError, match="Authorization header required|Invalid API key"):
                await middleware(context, call_next)
            call_next.assert_not_awaited()
        else:
            await middleware(context, call_next)
            call_next.assert_awaited_once_with(context)
