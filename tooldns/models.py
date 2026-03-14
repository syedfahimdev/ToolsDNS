"""
models.py — Pydantic data models for ToolDNS.

Defines the universal tool schema and all API request/response models.
Every tool from every source (MCP, OpenAPI, skill files, custom) gets
normalized into the UniversalTool schema before being indexed.

Key Models:
    UniversalTool: The normalized representation of any tool from any source.
    ToolSource: Describes where tools come from (MCP config, skill dir, etc).
    SearchRequest/SearchResponse: API models for the /v1/search endpoint.
    SourceRequest: API model for adding a new source via /v1/sources.
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    """
    Supported source types for tool ingestion.

    Each source type has a different ingestion strategy:
    - mcp_config: Reads an MCP config file (like nanobot's config.json)
                  and discovers all MCP servers listed in it.
    - mcp_stdio: Connects to a single stdio-based MCP server by spawning
                 a subprocess and communicating via stdin/stdout.
    - mcp_http: Connects to a single HTTP-based MCP server (Streamable HTTP)
                by making POST requests.
    - skill_directory: Reads a directory of skill markdown files and extracts
                       tool definitions from each file.
    - openapi: Parses an OpenAPI/Swagger spec and converts each endpoint
               into a tool definition.
    - custom: A manually registered tool with a user-provided schema.
    """
    MCP_CONFIG = "mcp_config"
    MCP_STDIO = "mcp_stdio"
    MCP_HTTP = "mcp_http"
    SKILL_DIRECTORY = "skill_directory"
    OPENAPI = "openapi"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------

class SourceInfo(BaseModel):
    """
    Metadata about where a tool originally came from.

    This is stored alongside each tool so ToolDNS can track
    provenance and re-fetch from the correct source during refresh.

    Attributes:
        source_type: The type of source this tool came from.
        source_name: Human-readable name of the source (e.g., "composio", "nanobot-skills").
        original_name: The tool's original name in the source system.
        server_command: For stdio MCP servers, the command used to start them.
        server_args: For stdio MCP servers, the command arguments.
        server_url: For HTTP MCP servers, the URL endpoint.
        server_headers: For HTTP MCP servers, any required headers.
    """
    source_type: SourceType
    source_name: str
    original_name: str = ""
    server_command: Optional[str] = None
    server_args: Optional[list[str]] = None
    server_url: Optional[str] = None
    server_headers: Optional[dict[str, str]] = None


class UniversalTool(BaseModel):
    """
    The universal tool schema — every tool from every source gets normalized to this.

    This is the core data model of ToolDNS. Whether a tool comes from an MCP server,
    an OpenAPI spec, a skill file, or a custom registration, it ends up as a
    UniversalTool in the index. This enables uniform semantic search across all tools
    regardless of their origin.

    Attributes:
        id: Unique identifier (format: "{source_name}__{tool_name}").
        name: The tool's name as the LLM will see it.
        description: What the tool does — this is what gets embedded for search.
        input_schema: JSON Schema defining the tool's parameters.
        source_info: Metadata about the tool's origin for refresh/provenance.
        tags: Optional tags for filtering and categorization.
        indexed_at: When this tool was last indexed.
    """
    id: str
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)
    source_info: SourceInfo
    tags: list[str] = Field(default_factory=list)
    indexed_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# API Request / Response Models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    """
    Request body for POST /v1/search.

    The core endpoint of ToolDNS. Send a natural language query
    describing what you need, and get back the most relevant tool(s).

    Attributes:
        query: Natural language description of what tool is needed.
               Example: "create a github issue about the login bug"
        top_k: Maximum number of results to return (default: 3).
        threshold: Minimum confidence score (0.0-1.0) to include a result (default: 0.1).
            Sentence-transformers scores typically range 0.1-0.4 for related content.
    """
    query: str
    top_k: int = Field(default=3, ge=1, le=20)
    threshold: float = Field(default=0.1, ge=0.0, le=1.0)


class SearchResult(BaseModel):
    """
    A single search result returned by the /v1/search endpoint.

    Contains the matched tool's full schema plus metadata about
    the match quality and how to call the tool.

    Attributes:
        id: The tool's unique identifier.
        name: The tool's name.
        description: What the tool does.
        confidence: How confident the match is (0.0-1.0, higher = better).
        input_schema: The tool's parameter schema — ready for the LLM to use.
        source: Which source this tool came from.
        how_to_call: Instructions for calling this tool (MCP server info, etc).
    """
    id: str
    name: str
    description: str
    confidence: float
    input_schema: dict = Field(default_factory=dict)
    source: str
    how_to_call: dict = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """
    Response body for POST /v1/search.

    Includes the search results plus analytics about the search
    efficiency, most notably tokens_saved — the number of tokens
    saved by NOT loading all tools into the LLM context.

    Attributes:
        results: List of matched tools, ordered by confidence.
        total_tools_indexed: Total number of tools in the index.
        tokens_saved: Estimated tokens saved vs. loading all tool schemas.
        search_time_ms: How long the search took in milliseconds.
    """
    results: list[SearchResult]
    total_tools_indexed: int = 0
    tokens_saved: int = 0
    search_time_ms: float = 0.0


class SourceRequest(BaseModel):
    """
    Request body for POST /v1/sources — add a new tool source.

    Tells ToolDNS where to find tools. Supports multiple source types:
    - mcp_config: Point to a config file containing MCP server definitions.
    - mcp_stdio: Point to a specific stdio-based MCP server.
    - mcp_http: Point to a specific HTTP-based MCP server.
    - skill_directory: Point to a directory of skill markdown files.
    - custom: Register a single custom tool with its schema.

    Attributes:
        type: The source type (see SourceType enum).
        name: A human-readable name for this source.
        path: File path (for mcp_config, skill_directory).
        url: URL (for mcp_http, openapi).
        command: Shell command (for mcp_stdio).
        args: Command arguments (for mcp_stdio).
        headers: HTTP headers (for mcp_http).
        config_key: JSON path to MCP servers in config file (for mcp_config).
        tool_name: Tool name (for custom type).
        tool_description: Tool description (for custom type).
        tool_schema: Tool input schema (for custom type).
    """
    type: SourceType
    name: str
    path: Optional[str] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    headers: Optional[dict[str, str]] = None
    config_key: str = "tools.mcpServers"
    tool_name: Optional[str] = None
    tool_description: Optional[str] = None
    tool_schema: Optional[dict] = None


class SourceResponse(BaseModel):
    """
    Response body for source-related endpoints.

    Attributes:
        id: Unique identifier for this source.
        name: Human-readable name.
        type: Source type.
        tools_count: Number of tools discovered from this source.
        status: Current status (active, error, refreshing).
        last_refreshed: When this source was last refreshed.
        error: Error message if the source failed to ingest.
    """
    id: str
    name: str
    type: SourceType
    tools_count: int = 0
    status: str = "active"
    last_refreshed: Optional[datetime] = None
    error: Optional[str] = None
