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
    WorkflowPattern: Learned or manual multi-tool sequences.
    AgentPreference: Per-agent tool preferences for personalized search.
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
        agent_id: Agent identifier for personalized search (learned preferences).
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
    agent_id: Optional[str] = Field(
        default=None,
        description="Agent identifier for personalized search based on learned preferences."
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
        preference_boost: Amount of confidence boost from agent preferences.
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
    preference_boost: float = 0.0  # Boost from agent preferences


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
        agent_preferences_applied: Whether agent preference boosting was used.
    """
    results: list[SearchResult]
    total_tools_indexed: int = 0
    tokens_saved: int = 0
    tokens_saved_by_dedup: int = 0       # Extra savings from session schema dedup
    search_time_ms: float = 0.0
    hint: str | None = None
    profile_active: str | None = None    # Profile used for this search
    session_tool_count: int = 0          # Total unique tools seen in this session
    agent_preferences_applied: bool = False  # Whether agent prefs were used


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
        agent_id: Agent identifier for personalized search.
    """
    queries: list[BatchSearchItem] = Field(..., min_length=1, max_length=50)
    minimal: bool = False
    session_id: Optional[str] = None
    profile: Optional[str] = None
    agent_id: Optional[str] = None


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
    working on the same task can share a session (set shared=True and 
    distribute the session_id).

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
# Workflow / Smart Chaining Models
# ---------------------------------------------------------------------------

class WorkflowStep(BaseModel):
    """
    A single step in a workflow pattern.

    Attributes:
        step_number: Order in the workflow (1-indexed).
        tool_id: The tool to call (e.g. "composio__SLACK_CREATE_CHANNEL").
        tool_name: Human-readable name.
        purpose: Why this step exists in the workflow.
        arg_mapping: Template mapping for arguments (e.g. {"name": "{employee_name}"}).
        arg_defaults: Default values for arguments.
        depends_on: Step numbers that must complete before this one.
        condition: Optional condition for execution (e.g. "if {send_email} == true").
        on_error: Error handling strategy: "stop", "skip", or "retry".
        retry_count: Number of retries on failure.
    """
    step_number: int
    tool_id: str
    tool_name: str = ""
    purpose: str = ""
    arg_mapping: dict = Field(default_factory=dict)
    arg_defaults: dict = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    condition: str = ""
    on_error: str = "stop"  # "stop" | "skip" | "retry"
    retry_count: int = 0


class WorkflowPattern(BaseModel):
    """
    A learned or manually-defined workflow pattern.
    
    ToolsDNS learns these by observing agent behavior, or they can be
    manually created. Workflows enable smart tool chaining — one query
    triggers a complete multi-tool sequence.

    Attributes:
        id: Unique workflow ID (e.g. "wp_employee_onboarding").
        name: Human-readable name.
        description: What this workflow does.
        trigger_phrases: Phrases that activate this workflow.
        steps: Ordered list of workflow steps.
        parallel_groups: Groups of steps that can run in parallel.
        usage_count: How many times this workflow was used.
        success_rate: Percentage of successful completions.
        avg_completion_time_ms: Average time to complete.
        source: "learned" | "manual" | "community".
        created_by: Agent or user who created it.
        created_at: Creation timestamp.
        last_used_at: Last usage timestamp.
    """
    id: str
    name: str
    description: str = ""
    trigger_phrases: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
    parallel_groups: list[list[int]] = Field(default_factory=list)
    usage_count: int = 0
    success_rate: float = 0.0
    avg_completion_time_ms: float = 0.0
    source: str = "learned"  # "learned" | "manual" | "community"
    created_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: datetime = Field(default_factory=datetime.utcnow)


class SuggestWorkflowRequest(BaseModel):
    """
    Request body for POST /v1/suggest-workflow.

    Attributes:
        query: Natural language description of the task.
        context: Key-value pairs for argument mapping (e.g. {"employee_name": "Sarah"}).
        profile: Optional tool profile to scope suggestions.
        agent_id: Optional agent ID for personalized suggestions.
    """
    query: str
    context: dict = Field(default_factory=dict)
    profile: Optional[str] = None
    agent_id: Optional[str] = None


class SuggestWorkflowResponse(BaseModel):
    """
    Response body for POST /v1/suggest-workflow.

    Attributes:
        suggested_workflows: List of matching workflows with confidence.
        alternative_workflows: Lower-confidence alternatives.
    """
    suggested_workflows: list[WorkflowPattern]
    alternative_workflows: list[WorkflowPattern]


class ExecuteWorkflowRequest(BaseModel):
    """
    Request body for POST /v1/execute-workflow.

    Attributes:
        workflow_id: ID of the workflow to execute.
        args: Arguments to pass to workflow steps.
        execution_mode: "parallel" | "sequential" | "dry_run".
        session_id: For schema dedup across steps.
    """
    workflow_id: str
    args: dict = Field(default_factory=dict)
    execution_mode: str = "parallel"  # "parallel" | "sequential" | "dry_run"
    session_id: Optional[str] = None


class WorkflowExecutionStep(BaseModel):
    """Status of a single step in workflow execution."""
    step: int
    tool: str
    status: str  # "pending" | "running" | "completed" | "failed" | "skipped"
    result: dict = Field(default_factory=dict)
    error: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tokens_used: int = 0


class ExecuteWorkflowResponse(BaseModel):
    """
    Response body for POST /v1/execute-workflow.

    Attributes:
        execution_id: Unique execution ID.
        status: Overall status: "running" | "completed" | "failed".
        steps: Status of each step.
        progress: Completion counts.
        started_at: When execution started.
        completed_at: When execution finished (if done).
        total_tokens_used: Tokens consumed by all steps.
    """
    execution_id: str
    status: str  # "running" | "completed" | "failed"
    steps: list[WorkflowExecutionStep]
    progress: dict = Field(default_factory=dict)
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_tokens_used: int = 0


class CreateWorkflowRequest(BaseModel):
    """Request body for POST /v1/workflows (manual creation)."""
    name: str
    description: str = ""
    trigger_phrases: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep]
    parallel_groups: list[list[int]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent Preference Models (Agent Memory)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tool Call Models
# ---------------------------------------------------------------------------

class CallToolRequest(BaseModel):
    """
    Request body for POST /v1/call.

    Attributes:
        tool_id: The tool's unique identifier (or macro__name for macros).
        arguments: Arguments to pass to the tool.
        agent_id: Agent identifier for preference tracking.
        query: Original search query (for analytics).
        session_id: Optional session for dedup tracking.
    """
    tool_id: str
    arguments: dict = Field(default_factory=dict)
    agent_id: str = ""
    query: str = ""
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Macro Models
# ---------------------------------------------------------------------------

class MacroStep(BaseModel):
    """A single step in a macro."""
    tool_id: str
    arg_template: dict = Field(
        default_factory=dict,
        description="Argument template with {placeholder} variables. E.g. {'to': '{email}'}"
    )


class CreateMacroRequest(BaseModel):
    """
    Request body for POST /v1/macros.

    Macros are reusable multi-tool workflows executed as a single call.

    Example:
        {
            "name": "deploy-and-notify",
            "description": "Create release then notify team",
            "steps": [
                {"tool_id": "GITHUB_CREATE_RELEASE", "arg_template": {"tag": "{version}"}},
                {"tool_id": "SLACK_SEND_MESSAGE", "arg_template": {"text": "Deployed {version}"}}
            ]
        }
    """
    name: str
    description: str = ""
    steps: list[MacroStep] = Field(..., min_length=1, max_length=20)


class MacroInfo(BaseModel):
    """Response model for macro endpoints."""
    id: str
    name: str
    description: str = ""
    steps: list[MacroStep] = Field(default_factory=list)
    usage_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentPreference(BaseModel):
    """
    Learned preferences for a specific agent.
    
    ToolsDNS tracks which tools an agent prefers and boosts their
    scores in search results. This improves accuracy and reduces
    the need for multiple searches.

    Attributes:
        agent_id: Unique agent identifier.
        preferred_tools: Tool IDs the agent uses most often.
        tool_selection_counts: How many times each tool was selected.
        avg_confidence_when_selected: Average confidence of selected tools.
        last_updated: When preferences were last updated.
    """
    agent_id: str
    preferred_tools: list[str] = Field(default_factory=list)
    tool_selection_counts: dict[str, int] = Field(default_factory=dict)
    avg_confidence_when_selected: float = 0.0
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class AgentPreferenceBoost(BaseModel):
    """Boost applied to a tool based on agent preferences."""
    tool_id: str
    boost_amount: float  # Added to confidence score
    reason: str  # Why this boost was applied


class SearchSelectRequest(BaseModel):
    """Record which search result an agent selected."""
    agent_id: str
    tool_id: str
    query: str = ""
    confidence: float = 0.0


class LearnFromUsageRequest(BaseModel):
    """Request body for POST /v1/learn (trigger learning)."""
    time_window_hours: int = Field(default=1, ge=1, le=24)
    min_occurrences: int = Field(default=3, ge=2)
    agent_id: Optional[str] = None  # Learn for specific agent or all


class LearnFromUsageResponse(BaseModel):
    """Response body for POST /v1/learn."""
    patterns_analyzed: int
    new_workflows_created: int
    existing_workflows_boosted: int
    agent_preferences_updated: int
    workflows: list[str]


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


# ---------------------------------------------------------------------------
# Preflight — server-side intent extraction + multi-strategy search
# ---------------------------------------------------------------------------

class PreflightRequest(BaseModel):
    """
    Request body for POST /v1/preflight.

    Send a raw user message, and ToolsDNS will:
    1. Clean the query (strip emails, URLs, dates)
    2. Extract intent keywords using built-in patterns
    3. Run multiple parallel searches (cleaned + intent queries)
    4. Merge, deduplicate, and rank results
    5. Return an LLM-ready context block with tool IDs, schemas, and call templates

    This is designed to be called BEFORE the LLM loop in any agent framework,
    so the LLM sees the right tools immediately without needing to search itself.

    Attributes:
        message: The raw user message (natural language).
        top_k: Max results per search query (default 5).
        threshold: Minimum confidence (default 0.1).
        max_results: Max total results after merge (default 5).
        include_schemas: Include input_schema for top matches (default True).
        include_call_templates: Include ready-to-use call templates (default True).
        include_macros: Check for relevant macros (default True).
        agent_id: Agent identifier for personalized results.
        format: Output format — "context_block" (injectable text) or "structured" (JSON).
    """
    message: str
    top_k: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    max_results: int = Field(default=5, ge=1, le=20)
    include_schemas: bool = True
    include_call_templates: bool = True
    include_macros: bool = True
    agent_id: Optional[str] = None
    format: str = Field(default="context_block", pattern="^(context_block|structured)$")


class PreflightToolMatch(BaseModel):
    """A single tool match in the preflight response."""
    tool_id: str
    name: str
    description: str
    confidence: float
    input_schema: dict = Field(default_factory=dict)
    call_template: Optional[str] = None
    source_type: str = ""
    matched_by: str = ""  # which query found this tool


class PreflightResponse(BaseModel):
    """
    Response body for POST /v1/preflight.

    Attributes:
        found: Whether any tools were found.
        tools: List of matched tools (merged, deduplicated, ranked).
        macros: List of relevant macros (if include_macros=True).
        context_block: LLM-injectable text block (if format="context_block").
        queries_used: The search queries that were generated and executed.
        search_time_ms: Total time for all searches.
    """
    found: bool = False
    tools: list[PreflightToolMatch] = Field(default_factory=list)
    macros: list[str] = Field(default_factory=list)
    context_block: Optional[str] = None
    queries_used: list[str] = Field(default_factory=list)
    search_time_ms: float = 0.0


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
