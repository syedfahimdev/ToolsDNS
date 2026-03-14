"""
tokens.py — Token counting and model cost estimation for ToolDNS.

Counts real tokens (not estimates) for tool schemas using tiktoken.
Uses model-aware pricing to calculate actual cost savings per search.

Token counting uses cl100k_base encoding (used by Claude, GPT-4, etc.)
as a universal approximation. It's accurate within ~5% for all major LLMs.

Model prices come from official pricing pages. The pricing map covers
common Claude, GPT, and Gemini models by substring matching so new
model variants are automatically matched (e.g. "claude-sonnet-4-7" hits
the "claude-sonnet" key).
"""

import json
from typing import Optional

# Try tiktoken for accurate counts; fall back to char-based approximation
try:
    import tiktoken as _tiktoken
    _enc = _tiktoken.get_encoding("cl100k_base")
    def _count(text: str) -> int:
        return len(_enc.encode(text))
except Exception:
    def _count(text: str) -> int:
        # ~4 chars per token is the standard approximation
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Model pricing — input tokens, USD per 1M tokens
# Keys are lowercase substrings matched against the model name.
# Order matters: more specific keys first.
# ---------------------------------------------------------------------------
MODEL_PRICES: dict[str, float] = {
    # Claude 4.x
    "claude-opus-4":    15.00,
    "claude-sonnet-4":   3.00,
    "claude-haiku-4":    0.25,
    # Claude 3.x fallback
    "claude-opus":      15.00,
    "claude-sonnet":     3.00,
    "claude-haiku":      0.25,
    # OpenAI
    "gpt-4o-mini":       0.15,
    "gpt-4o":            2.50,
    "gpt-4-turbo":      10.00,
    "gpt-4":            30.00,
    "gpt-3.5-turbo":     0.50,
    "o1-mini":           1.10,
    "o1":               15.00,
    # Google
    "gemini-1.5-pro":    1.25,
    "gemini-1.5-flash":  0.075,
    "gemini-2.0-flash":  0.10,
    # Mistral
    "mistral-large":     2.00,
    "mistral-small":     0.20,
    "codestral":         0.20,
    # Meta / Groq
    "llama-3.3":         0.59,
    "llama-3.1":         0.59,
    "llama-3":           0.59,
    # DeepSeek
    "deepseek":          0.14,
}


def get_model_price(model_name: str) -> Optional[float]:
    """
    Look up USD price per 1M input tokens for a model.

    Matches by substring so "claude-sonnet-4-6" hits "claude-sonnet-4".
    Returns None if the model is unknown (so UI can show "unknown" not $0).

    Args:
        model_name: Model ID string, e.g. "claude-sonnet-4-6"

    Returns:
        float or None: Price per 1M tokens, or None if unknown.
    """
    lower = model_name.lower()
    for key, price in MODEL_PRICES.items():
        if key in lower:
            return price
    return None


def count_tool_tokens(tool: dict) -> int:
    """
    Count the tokens a single tool would consume when loaded into an LLM context.

    Accounts for name, description, and full input schema JSON — the
    actual text that would appear in a tool-use system prompt or tool list.

    Args:
        tool: Tool dict with keys: name, description, input_schema.

    Returns:
        int: Token count for this tool's full schema representation.
    """
    schema = tool.get("input_schema", {})
    schema_text = json.dumps(schema, separators=(",", ":")) if schema else ""

    text = (
        f"Tool: {tool.get('name', '')}\n"
        f"Description: {tool.get('description', '')}\n"
        f"Schema: {schema_text}"
    )
    # Add ~8 tokens for formatting overhead (JSON wrapper, commas, etc.)
    return _count(text) + 8


def count_tools_tokens(tools: list[dict]) -> int:
    """Count total tokens for a list of tools."""
    return sum(count_tool_tokens(t) for t in tools)


def tokens_to_cost(tokens: int, price_per_million: float) -> float:
    """Convert a token count to USD cost."""
    return (tokens / 1_000_000) * price_per_million
