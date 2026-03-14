"""
fetcher.py — MCP tool fetcher for ToolDNS.

Connects to MCP servers and extracts their tool definitions.
Supports two transport types:
    1. stdio: Spawns the MCP server as a subprocess, communicates via stdin/stdout
    2. HTTP (Streamable HTTP): Makes POST requests to HTTP-based MCP servers

The MCP protocol uses JSON-RPC 2.0. To discover tools, we:
    1. Send "initialize" — establish the connection
    2. Send "notifications/initialized" — notify we're ready
    3. Send "tools/list" — get all tool names, descriptions, and schemas
    4. Close the connection

This is the core mechanism that makes ToolDNS work: it speaks the same
protocol as every MCP server, so it can discover tools from ANY server.

Usage:
    from tooldns.fetcher import MCPFetcher
    fetcher = MCPFetcher()

    # From a subprocess (stdio)
    tools = fetcher.fetch_stdio("python3", ["-u", "/path/to/server.py"])

    # From an HTTP endpoint
    tools = fetcher.fetch_http("https://mcp.example.com/v3/mcp/...")
"""

import json
import subprocess
import time
from typing import Optional
import httpx
from tooldns.config import logger


class MCPFetcher:
    """
    Connects to MCP servers and extracts their tool definitions.

    Implements both stdio and HTTP transports for the MCP protocol.
    Each fetch method returns a list of tool dicts, where each dict
    contains 'name', 'description', and 'inputSchema' keys — the
    standard MCP tool format.
    """

    # MCP client info sent during initialization
    CLIENT_INFO = {
        "name": "tooldns",
        "version": "1.0.0"
    }
    PROTOCOL_VERSION = "2024-11-05"

    def _make_init_request(self, req_id: int = 1) -> dict:
        """
        Build the MCP initialize request.

        This is the first message sent to any MCP server. It tells
        the server who we are and what protocol version we speak.

        Args:
            req_id: JSON-RPC request ID.

        Returns:
            dict: The initialize request message.
        """
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": self.CLIENT_INFO
            }
        }

    def _make_initialized_notification(self) -> dict:
        """
        Build the MCP initialized notification.

        Sent after receiving the initialize response. This is a
        notification (no 'id' field), so no response is expected.

        Returns:
            dict: The initialized notification message.
        """
        return {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }

    def _make_tools_list_request(self, req_id: int = 2) -> dict:
        """
        Build the MCP tools/list request.

        This is the key request that returns all tools the server provides.
        Each tool has a name, description, and inputSchema.

        Args:
            req_id: JSON-RPC request ID.

        Returns:
            dict: The tools/list request message.
        """
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/list",
            "params": {}
        }

    # -------------------------------------------------------------------
    # stdio transport — for local MCP servers started as subprocesses
    # -------------------------------------------------------------------

    def fetch_stdio(self, command: str, args: list[str],
                    timeout: int = 30, env: Optional[dict] = None) -> list[dict]:
        """
        Fetch tools from a stdio-based MCP server.

        Spawns the MCP server as a subprocess, performs the MCP handshake
        via stdin/stdout, requests the tool list, and then terminates
        the subprocess.

        Args:
            command: The command to run (e.g., "python3", "node").
            args: Command arguments (e.g., ["-u", "/path/to/server.py"]).
            timeout: Max seconds to wait for each response (default: 30).
            env: Optional environment variables for the subprocess.

        Returns:
            list[dict]: List of tool definitions. Each dict has:
                        'name' (str), 'description' (str), 'inputSchema' (dict).

        Raises:
            TimeoutError: If the server doesn't respond within timeout.
            RuntimeError: If the server returns an error or can't be started.
        """
        import os
        proc_env = {**os.environ, **(env or {})}

        logger.info(f"Starting stdio MCP server: {command} {' '.join(args)}")

        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Command not found: {command}. "
                f"Make sure the MCP server is installed."
            )

        try:
            # Step 1: Initialize
            self._stdio_send(proc, self._make_init_request())
            init_resp = self._stdio_recv(proc, timeout)
            logger.debug(f"Initialize response: {init_resp}")

            if "error" in init_resp:
                raise RuntimeError(
                    f"MCP initialize failed: {init_resp['error']}"
                )

            # Step 2: Send initialized notification
            self._stdio_send(proc, self._make_initialized_notification())

            # Small delay to let the server process the notification
            time.sleep(0.1)

            # Step 3: Request tool list
            self._stdio_send(proc, self._make_tools_list_request())
            tools_resp = self._stdio_recv(proc, timeout)

            if "error" in tools_resp:
                raise RuntimeError(
                    f"MCP tools/list failed: {tools_resp['error']}"
                )

            tools = tools_resp.get("result", {}).get("tools", [])
            logger.info(f"Discovered {len(tools)} tools via stdio")
            return tools

        except Exception as e:
            # Read stderr for debugging info
            stderr_output = ""
            try:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            if stderr_output:
                logger.debug(f"Server stderr: {stderr_output}")
            raise RuntimeError(f"Failed to fetch tools via stdio: {e}")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _stdio_send(self, proc: subprocess.Popen, message: dict):
        """
        Send a JSON-RPC message to the MCP server via stdin.

        Each message is a single line of JSON followed by a newline.

        Args:
            proc: The running subprocess.
            message: The JSON-RPC message to send.
        """
        line = json.dumps(message) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.flush()

    def _stdio_recv(self, proc: subprocess.Popen, timeout: int = 30) -> dict:
        """
        Read a JSON-RPC response from the MCP server via stdout.

        Blocks until a line of JSON is received or the timeout expires.
        Skips empty lines and lines that aren't valid JSON (like logging output).

        Args:
            proc: The running subprocess.
            timeout: Maximum seconds to wait for a response.

        Returns:
            dict: The parsed JSON-RPC response.

        Raises:
            TimeoutError: If no response is received within timeout.
        """
        import select
        start = time.time()

        while time.time() - start < timeout:
            # Check if stdout has data ready
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline().decode("utf-8").strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    # Skip non-JSON output (e.g., logging)
                    logger.debug(f"Skipping non-JSON line: {line[:100]}")
                    continue

            # Check if process has died
            if proc.poll() is not None:
                raise RuntimeError(
                    f"MCP server process exited with code {proc.returncode}"
                )

        raise TimeoutError(f"No response from MCP server within {timeout}s")

    def call_stdio(self, command: str, args: list[str],
                   tool_name: str, arguments: dict,
                   timeout: int = 60, env: Optional[dict] = None) -> dict:
        """
        Execute a tool on a stdio-based MCP server.

        Spawns the server, performs the MCP handshake, sends a tools/call
        request, and returns the result. The process is terminated after
        the call completes.

        Args:
            command: The command to run (e.g., "python3", "node", "npx").
            args: Command arguments.
            tool_name: The name of the tool to call.
            arguments: The arguments to pass to the tool.
            timeout: Max seconds to wait per response (default: 60).
            env: Optional extra environment variables.

        Returns:
            dict: The tool's result from the MCP server.

        Raises:
            RuntimeError: If the server errors or can't be started.
        """
        import os
        proc_env = {**os.environ, **(env or {})}

        logger.info(f"Calling stdio MCP tool: {tool_name} via {command} {' '.join(args)}")

        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Command not found: {command}. "
                f"Make sure the MCP server is installed."
            )

        try:
            # Handshake
            self._stdio_send(proc, self._make_init_request(req_id=1))
            init_resp = self._stdio_recv(proc, timeout)
            if "error" in init_resp:
                raise RuntimeError(f"MCP initialize failed: {init_resp['error']}")

            self._stdio_send(proc, self._make_initialized_notification())
            time.sleep(0.1)

            # Call the tool
            call_req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments}
            }
            self._stdio_send(proc, call_req)
            result_resp = self._stdio_recv(proc, timeout)

            if "error" in result_resp:
                raise RuntimeError(f"tools/call failed: {result_resp['error']}")

            return result_resp.get("result", {})

        except Exception as e:
            stderr_output = ""
            try:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            if stderr_output:
                logger.debug(f"Server stderr: {stderr_output}")
            raise RuntimeError(f"stdio tool call failed: {e}")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # -------------------------------------------------------------------
    # HTTP transport — for remote MCP servers (Streamable HTTP)
    # -------------------------------------------------------------------

    def fetch_http(self, url: str, headers: Optional[dict] = None,
                   timeout: int = 30) -> list[dict]:
        """
        Fetch tools from an HTTP-based MCP server (Streamable HTTP transport).

        Makes POST requests to the MCP server's HTTP endpoint, performing
        the standard MCP handshake and tool list request.

        Args:
            url: The MCP server's HTTP endpoint URL.
            headers: Optional HTTP headers (e.g., API keys, auth tokens).
            timeout: Max seconds per request (default: 30).

        Returns:
            list[dict]: List of tool definitions. Each dict has:
                        'name' (str), 'description' (str), 'inputSchema' (dict).

        Raises:
            RuntimeError: If the server returns an error or is unreachable.
        """
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {})
        }
        logger.info(f"Connecting to HTTP MCP server: {url}")

        try:
            # Step 1: Initialize
            resp = httpx.post(
                url, headers=h,
                json=self._make_init_request(),
                timeout=timeout
            )
            resp.raise_for_status()

            # Some servers return a session ID for subsequent requests
            init_data = self._parse_http_response(resp)
            session_id = resp.headers.get("mcp-session-id")
            if session_id:
                h["mcp-session-id"] = session_id

            if "error" in init_data:
                raise RuntimeError(
                    f"MCP initialize failed: {init_data['error']}"
                )

            # Step 2: Send initialized notification
            httpx.post(
                url, headers=h,
                json=self._make_initialized_notification(),
                timeout=timeout
            )

            # Step 3: Request tool list
            resp = httpx.post(
                url, headers=h,
                json=self._make_tools_list_request(),
                timeout=timeout
            )
            resp.raise_for_status()

            tools_data = self._parse_http_response(resp)
            if "error" in tools_data:
                raise RuntimeError(
                    f"MCP tools/list failed: {tools_data['error']}"
                )

            tools = tools_data.get("result", {}).get("tools", [])
            logger.info(f"Discovered {len(tools)} tools via HTTP")
            return tools

        except httpx.HTTPError as e:
            raise RuntimeError(f"HTTP request failed: {e}")

    def _parse_http_response(self, resp: httpx.Response) -> dict:
        """
        Parse an HTTP response from an MCP server.

        Handles both direct JSON responses and SSE (Server-Sent Events)
        streams, since MCP Streamable HTTP can use either format.

        Args:
            resp: The httpx Response object.

        Returns:
            dict: The parsed JSON-RPC response.
        """
        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # Parse SSE format: look for "data:" lines
            for line in resp.text.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data:
                        try:
                            return json.loads(data)
                        except json.JSONDecodeError:
                            continue
            return {}
        else:
            # Direct JSON response
            return resp.json()
