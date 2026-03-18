"""
models.py — Pydantic data models for ToolsDNS.

Defines the universal tool schema and all API request/response models.
Every tool from every source (MCP, OpenAPI, skill files, custom) gets
normalized into the UniversalTool schema before being indexed.

Key Models:
    UniversalTool: The normalized representation of any tool from any source.
    ToolSource: Describes where tools come from (MCP config, skill dir, etc).
    SearchRequest/SearchResponse: API models for the /v1/search endpoint.
    BatchSearchRequest/BatchSearchResponse: Batch multi-query search.
    AgentSession: Per-agent schema dedup session for token savings.
    ToolProfile: Named tool subset for scoping agents to relevant tools.
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
    - mcp_config: Reads an MCP config file (like claude_desktop_config.json)
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

    This is stored alongside each tool so ToolsDNS can track
    provenance and re-fetch from the correct source during refresh.

    Attributes:
        source_type: The type of source this tool came from.
        source_name: Human-readable name of the source (e.g., "composio", "my-tools").
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

    This is the core data model of ToolsDNS. Whether a tool comes from an MCP server,
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
# Search Models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    """
    Request body for POST /v1/search.

    The core endpoint of ToolsDNS. Send a natural language query
    describing what you need, and get back the most relevant tool(s).

    Attributes:
        query: Natural language description of what tool is needed.
               Example: "create a github issue about the login bug"
        top_k: Maximum number of results to return (default: 3).
        threshold: Minimum confidence score (0.0-1.0) to include a result (default: 0.1).
        minimal: If True, return trimmed schemas (required fields only) — saves ~70% tokens.
        session_id: Agent session ID for schema dedup — skips tools already seen this session.
        profile: Tool profile name to scope search to a relevant subset (e.g. "email-agent").
    """
    query: str
    top_k: int = Field(default=3, ge=1, le=20)
    threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    # ── Multi-agent token saving features ──────────────────────────────────
    minimal: bool = Field(
        default=False,
        description="Return trimmed schemas (required fields only). Saves ~70% tokens per result."
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Agent session ID. Tools already returned this session are skipped (dedup)."
    )
    profile: Optional[str] = Field(
        default=None,
        description="Tool profile name. Scopes search to a named subset of tools."
    )


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
        match_reason: Human-readable explanation of why this tool was returned.
        already_seen: True if this tool was already returned in the current session.
                      When True, input_schema is omitted to save tokens.
    """
    id: str
    name: str
    description: str
    confidence: float
    input_schema: dict = Field(default_factory=dict)
    source: str
    category: str = "Other"
    how_to_call: dict = Field(default_factory=dict)
    match_reason: str = ""
    already_seen: bool = False  # True = schema omitted, agent already has it


class SearchResponse(BaseModel):
    """
    Response body for POST /v1/search.

    Includes the search results plus analytics about the search
    efficiency, most notably tokens_saved — the number of tokens
    saved by NOT loading all tools into the LLM context.

    Attributes:
        results: List of matched tools, ordered by confidence.
        total_tools_indexed: Total number of tools in the index.
        tokens_saved: Tokens saved vs. loading all tool schemas.
        tokens_saved_by_dedup: Additional tokens saved by session schema dedup.
        search_time_ms: How long the search took in milliseconds.
        hint: LLM-readable suggestion when confidence is low.
        profile_active: Name of the profile used to scope this search, if any.
        session_tool_count: How many unique tools this session has seen so far.
    """
    results: list[SearchResult]
    total_tools_indexed: int = 0
    tokens_saved: int = 0
    tokens_saved_by_dedup: int = 0       # Extra savings from session schema dedup
    search_time_ms: float = 0.0
    hint: str | None = None
    profile_active: str | None = None    # Profile used for this search
    session_tool_count: int = 0          # Total unique tools seen in this session


# ---------------------------------------------------------------------------
# Batch Search Models
# ---------------------------------------------------------------------------

class BatchSearchItem(BaseModel):
    """A single query inside a batch search request."""
    query: str
    top_k: int = Field(default=3, ge=1, le=20)
    threshold: float = Field(default=0.1, ge=0.0, le=1.0)


class BatchSearchRequest(BaseModel):
    """
    Request body for POST /v1/search/batch.

    Execute multiple tool searches in a single HTTP call.
    Critical for multi-agent systems — 16 agents can submit all
    their queries at once instead of making 16 separate requests.

    The shared session_id ensures schema dedup works across all
    queries in the batch: if query 1 and query 4 both match
    GMAIL_SEND_EMAIL, it's only returned once with full schema.

    Attributes:
        queries: List of search queries to execute in parallel.
        minimal: Strip schemas to required fields only (~70% token reduction).
        session_id: Shared session for cross-query schema dedup.
        profile: Tool profile to scope all queries to a relevant subset.
    """
    queries: list[BatchSearchItem] = Field(..., min_length=1, max_length=50)
    minimal: bool = False
    session_id: Optional[str] = None
    profile: Optional[str] = None


class BatchSearchResponse(BaseModel):
    """
    Response body for POST /v1/search/batch.

    Attributes:
        results: Ordered list of SearchResponse, one per query.
        total_queries: Number of queries executed.
        total_tokens_saved: Sum of tokens_saved across all results.
        total_dedup_savings: Sum of tokens_saved_by_dedup across all results.
        batch_time_ms: Wall-clock time for the entire batch.
        vs_sequential_ms: Estimated time if queries were made sequentially.
    """
    results: list[SearchResponse]
    total_queries: int
    total_tokens_saved: int = 0
    total_dedup_savings: int = 0
    batch_time_ms: float = 0.0
    vs_sequential_ms: float = 0.0   # Estimated sequential time for comparison


# ---------------------------------------------------------------------------
# Agent Session Models
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    """
    Request body for POST /v1/sessions.

    Creates an agent session for schema dedup tracking. Once created,
    pass the session_id in search requests to avoid receiving duplicate
    schemas for tools already seen in this session.

    In multi-agent setups, each agent gets its own session. Or, agents
    working on the same task can share a session to avoid redundant schemas
    between them (set shared=True and distribute the session_id).

    Attributes:
        agent_id: Optional human-readable label (e.g. "email-agent-1").
        profile: Pre-assign a tool profile to this session's searches.
        shared: If True, this session_id can be used by multiple agents.
        ttl_seconds: Session lifetime in seconds (default: 1 hour).
    """
    agent_id: str = ""
    profile: str = ""
    shared: bool = False
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class SessionInfo(BaseModel):
    """
    Response body for session endpoints — current session state.

    Attributes:
        session_id: The session's unique ID to pass in search requests.
        agent_id: Human label for this session.
        profile: Active tool profile for this session.
        shared: Whether this session is shared across agents.
        tools_seen: Number of unique tool schemas sent so far.
        tokens_saved_by_dedup: Tokens saved by not re-sending known schemas.
        created_at: When this session was created.
        expires_at: When this session will expire.
    """
    session_id: str
    agent_id: str = ""
    profile: str = ""
    shared: bool = False
    tools_seen: int = 0
    tokens_saved_by_dedup: int = 0
    created_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# Tool Profile Models
# ---------------------------------------------------------------------------

class CreateProfileRequest(BaseModel):
    """
    Request body for POST /v1/profiles.

    A tool profile is a named, reusable subset of tools scoped to a specific
    agent type or task. Agents that use a profile only search within that
    subset — dramatically reducing token cost and improving search accuracy.

    Example profiles:
        "email-agent"   → GMAIL_*, OUTLOOK_*, EMAIL_*
        "code-agent"    → GITHUB_*, GITLAB_*, LINEAR_*, JIRA_*
        "data-agent"    → AIRTABLE_*, NOTION_*, GOOGLEDRIVE_*, SHEETS_*
        "social-agent"  → TWITTER_*, LINKEDIN_*, SLACK_*

    Attributes:
        name: Unique profile name (e.g. "email-agent").
        description: What kind of agent/task this profile is for.
        tool_patterns: Glob patterns matched against tool names (e.g. "GMAIL_*").
        pinned_tool_ids: Always-include specific tool IDs regardless of patterns.
    """
    name: str
    description: str = ""
    tool_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns matched against tool names. E.g. ['GMAIL_*', 'OUTLOOK_*']"
    )
    pinned_tool_ids: list[str] = Field(
        default_factory=list,
        description="Specific tool IDs to always include. E.g. ['composio__SLACK_SEND_MESSAGE']"
    )


class ProfileInfo(BaseModel):
    """
    A tool profile — named subset of tools for an agent type.

    Attributes:
        name: Unique profile name.
        description: What this profile is for.
        tool_patterns: Glob patterns for tool name matching.
        pinned_tool_ids: Explicitly included tool IDs.
        tool_count: Number of tools currently matched by this profile.
        created_at: When this profile was created.
    """
    name: str
    description: str = ""
    tool_patterns: list[str] = Field(default_factory=list)
    pinned_tool_ids: list[str] = Field(default_factory=list)
    tool_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Source Models
# ---------------------------------------------------------------------------

class SourceRequest(BaseModel):
    """
    Request body for POST /v1/sources — add a new tool source.

    Tells ToolsDNS where to find tools. Supports multiple source types:
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


class RegisterMCPRequest(BaseModel):
    """
    Request body for POST /v1/register-mcp — agents add a new MCP server.

    AI agents call this to register an MCP server into ToolsDNS without
    any interactive prompts. The server is added to ~/.tooldns/config.json,
    env vars are written to ~/.tooldns/.env, and tools are indexed immediately.

    Attributes:
        name: Short identifier for this server (e.g., "github", "slack").
        command: Executable for stdio servers (e.g., "npx", "python3").
        args: Arguments for stdio servers (e.g., ["-y", "@mcp/github"]).
        url: URL for HTTP/SSE servers.
        headers: HTTP headers for HTTP servers (e.g., auth tokens).
        env_vars: Environment variables to save (e.g., {"GITHUB_TOKEN": "ghp_..."}).
        ingest: Whether to index the server's tools immediately (default: true).
    """
    name: str
    command: Optional[str] = None
    args: Optional[list[str]] = None
    url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    env_vars: Optional[dict[str, str]] = None
    ingest: bool = True


class CreateSkillRequest(BaseModel):
    """
    Request body for POST /v1/skills — agents create a new skill file.

    AI agents call this to write a skill markdown file into the ToolsDNS
    skills directory. The skill is indexed immediately after creation.

    Attributes:
        name: Skill name, used as the folder name (e.g., "send-report").
        description: One-line description of what the skill does.
        content: Full markdown content of the SKILL.md file.
        skill_path: Optional path to a specific skill directory. Defaults
                    to ~/.tooldns/skills/.
        ingest: Whether to re-index skills immediately (default: true).
    """
    name: str
    description: str
    content: str
    skill_path: Optional[str] = None
    ingest: bool = True


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
