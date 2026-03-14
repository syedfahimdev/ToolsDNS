"""
health.py — Tool and source health monitoring for ToolDNS.

Periodically checks whether registered MCP servers are reachable
and marks their tools as healthy, degraded, or down. This prevents
the LLM from trying to call tools that are offline.

Health check strategy:
  - HTTP MCP servers: Send a ping request, check HTTP 200.
  - stdio MCP servers: Can't ping without spawning a process. Instead,
    use a "staleness" heuristic: if the source was last refreshed within
    2 × refresh_interval, it's "healthy"; otherwise "degraded".
  - Skill directories: Always "healthy" (files are local, no network needed).

Usage:
    monitor = HealthMonitor(db, settings)
    await monitor.check_all()   # runs all checks
"""

import asyncio
from datetime import datetime, timedelta
from tooldns.config import settings, logger
from tooldns.database import ToolDatabase


class HealthMonitor:
    """
    Checks MCP server reachability and updates tool health in the database.

    Attributes:
        db: The ToolDatabase instance.
        check_timeout: HTTP request timeout in seconds.
        _prev_status: Previous status per source name for transition detection.
    """

    def __init__(self, db: ToolDatabase, check_timeout: int = 5):
        self.db = db
        self.check_timeout = check_timeout
        self._prev_status: dict[str, str] = {}

    async def check_all(self) -> dict:
        """
        Check health for all registered sources.

        Updates health_status on each source and its tools in the database.
        Fires a webhook when a source transitions between healthy/degraded/down.

        Returns:
            dict: Summary of health check results.
        """
        sources = self.db.get_all_sources()
        results = {"healthy": 0, "degraded": 0, "down": 0, "skipped": 0}

        tasks = [self._check_source(source) for source in sources]
        statuses = await asyncio.gather(*tasks, return_exceptions=True)

        webhook_tasks = []
        for source, status in zip(sources, statuses):
            if isinstance(status, Exception):
                status = "degraded"
                logger.warning(f"Health check error for {source['name']}: {status}")

            self.db.set_source_health(source["id"], status)
            self.db.set_tools_health_by_source(source["name"], status)
            results[status] = results.get(status, 0) + 1

            # Fire webhook on status transition
            prev = self._prev_status.get(source["name"])
            if prev is not None and prev != status and settings.webhook_url:
                webhook_tasks.append(self._fire_webhook(source["name"], prev, status))
            self._prev_status[source["name"]] = status

        if webhook_tasks:
            await asyncio.gather(*webhook_tasks, return_exceptions=True)

        logger.info(f"Health check complete: {results}")
        return results

    async def _fire_webhook(self, source_name: str, prev: str, current: str) -> None:
        """POST a status-change event to the configured webhook URL."""
        import httpx
        payload = {
            "event": "source_health_change",
            "source": source_name,
            "previous_status": prev,
            "current_status": current,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        headers = {"Content-Type": "application/json"}
        if settings.webhook_secret:
            headers["X-ToolDNS-Secret"] = settings.webhook_secret
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(settings.webhook_url, json=payload, headers=headers)
            logger.info(f"Webhook fired: {source_name} {prev} → {current}")
        except Exception as e:
            logger.warning(f"Webhook failed for {source_name}: {e}")

    async def _check_source(self, source: dict) -> str:
        """
        Check health for a single source.

        Args:
            source: Source dict from the database.

        Returns:
            str: "healthy", "degraded", or "down".
        """
        source_type = source.get("type", "")
        config = source.get("config", {})

        # Skill directories are always healthy (local files)
        if "skill" in source_type:
            return "healthy"

        # HTTP MCP servers — ping them
        url = config.get("url") or config.get("server_url")
        if url:
            return await self._ping_http(url, source.get("config", {}).get("headers"))

        # stdio MCP servers — use staleness heuristic
        if config.get("command") or config.get("server_command"):
            return self._check_stdio_staleness(source)

        # mcp_config sources — check based on last refresh
        return self._check_stdio_staleness(source)

    async def _ping_http(self, url: str, headers: dict = None) -> str:
        """
        Send a ping to an HTTP MCP server.

        Tries to send a JSON-RPC ping. Falls back to a GET request.
        Returns 'healthy', 'degraded', or 'down'.
        """
        import httpx

        if not headers:
            headers = {}

        # Normalize URL — use base URL for ping
        base_url = url.rstrip("/")
        ping_payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

        try:
            async with httpx.AsyncClient(timeout=self.check_timeout) as client:
                resp = await client.post(
                    base_url,
                    json=ping_payload,
                    headers={"Content-Type": "application/json", **headers},
                )
                # MCP servers return 200 for valid JSON-RPC (even for unknown methods)
                if resp.status_code in (200, 201, 202):
                    return "healthy"
                if resp.status_code in (400, 404, 405):
                    # Server is up but doesn't support ping — still alive
                    return "healthy"
                if resp.status_code >= 500:
                    return "degraded"
                return "down"

        except httpx.TimeoutException:
            return "down"
        except httpx.ConnectError:
            return "down"
        except Exception as e:
            logger.debug(f"HTTP ping failed for {url}: {e}")
            return "degraded"

    def _check_stdio_staleness(self, source: dict) -> str:
        """
        Estimate stdio server health based on last refresh time.

        A stdio server is considered:
        - 'healthy' if refreshed within 2 × refresh_interval minutes
        - 'degraded' if stale (hasn't been refreshed recently)
        - 'down' if it has an error recorded
        """
        if source.get("error"):
            return "down"

        last_refreshed = source.get("last_refreshed")
        if not last_refreshed:
            return "unknown"

        try:
            refresh_time = datetime.fromisoformat(last_refreshed)
            # Allow 2× the configured refresh interval before marking degraded
            stale_threshold = timedelta(minutes=max(settings.refresh_interval * 2, 60))
            if datetime.utcnow() - refresh_time > stale_threshold:
                return "degraded"
            return "healthy"
        except Exception:
            return "unknown"
