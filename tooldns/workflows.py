"""
workflows.py — Smart Tool Chaining and Workflow Learning for ToolsDNS.

This module handles:
1. Workflow pattern matching (suggest workflows based on queries)
2. Workflow learning (detect patterns from agent tool usage)
3. Workflow execution (run workflows with parallel/sequential support)
4. Agent preference learning (track which tools agents prefer)

Usage:
    from tooldns.workflows import WorkflowEngine
    engine = WorkflowEngine(database)
    
    # Suggest a workflow
    workflow = engine.suggest_workflow("onboard new employee")
    
    # Learn from usage
    engine.learn_from_usage()
    
    # Execute a workflow
    result = await engine.execute_workflow(workflow_id, args)
"""

import json
import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from difflib import SequenceMatcher

from tooldns.config import logger
from tooldns.database import ToolDatabase


class WorkflowEngine:
    """
    Engine for workflow suggestion, learning, and execution.

    Attributes:
        db: ToolDatabase instance for persistence.
        tool_caller: Callable (tool_id, arguments) -> dict for real execution.
    """

    def __init__(self, database: ToolDatabase, tool_caller=None):
        """
        Initialize with database connection and optional tool caller.

        Args:
            database: ToolDatabase instance.
            tool_caller: Callable(tool_id: str, arguments: dict) -> dict.
                         If None, workflow execution returns dry-run results.
        """
        self.db = database
        self.tool_caller = tool_caller
    
    # -----------------------------------------------------------------------
    # Workflow Suggestion
    # -----------------------------------------------------------------------
    
    def suggest_workflows(self, query: str, agent_id: Optional[str] = None,
                         top_k: int = 3) -> list[dict]:
        """
        Suggest workflows based on query text.
        
        Matches against trigger phrases using semantic + keyword similarity.
        
        Args:
            query: Natural language query.
            agent_id: Optional agent ID for personalized ranking.
            top_k: Number of suggestions to return.
            
        Returns:
            List of matching workflows with confidence scores.
        """
        all_workflows = self.db.get_all_workflows()
        scored = []
        
        for wf in all_workflows:
            # Score against trigger phrases
            max_score = 0.0
            match_reason = ""
            
            for phrase in wf.get("trigger_phrases", []):
                # Exact substring match
                if phrase.lower() in query.lower():
                    max_score = max(max_score, 0.95)
                    match_reason = f"exact trigger match: '{phrase}'"
                    break
                
                # Fuzzy similarity
                similarity = SequenceMatcher(None, phrase.lower(), query.lower()).ratio()
                if similarity > 0.7:
                    max_score = max(max_score, similarity * 0.9)
                    match_reason = f"fuzzy match: '{phrase}' ({similarity:.0%})"
            
            # Boost by usage count (popular workflows rank higher)
            usage_boost = min(0.1, wf.get("usage_count", 0) / 1000)
            score = min(0.99, max_score + usage_boost)
            
            # Boost by agent preferences
            if agent_id and score > 0:
                pref = self.db.get_agent_preferences(agent_id)
                if pref:
                    wf_tools = [s["tool_id"] for s in wf.get("steps", [])]
                    pref_tools = set(pref.get("preferred_tools", []))
                    matches = len(set(wf_tools) & pref_tools)
                    if matches > 0:
                        score = min(0.99, score + 0.05 * matches)
                        match_reason += f" + agent preference boost ({matches} tools)"
            
            if score > 0.5:  # Minimum threshold
                scored.append({
                    **wf,
                    "confidence": round(score, 2),
                    "match_reason": match_reason
                })
        
        # Sort by confidence
        scored.sort(key=lambda x: x["confidence"], reverse=True)
        return scored[:top_k]
    
    # -----------------------------------------------------------------------
    # Workflow Learning
    # -----------------------------------------------------------------------
    
    def learn_from_usage(self, time_window_minutes: int = 60,
                        min_occurrences: int = 3,
                        similarity_threshold: float = 0.85) -> dict:
        """
        Learn workflow patterns from recent tool usage.
        
        Analyzes tool call sequences and creates workflow patterns
        for frequently occurring sequences.
        
        Args:
            time_window_minutes: Time window for sequence detection.
            min_occurrences: Minimum times a pattern must occur.
            similarity_threshold: Minimum similarity to match existing patterns.
            
        Returns:
            Stats about learned workflows.
        """
        sequences = self.db.get_recent_tool_sequences(
            time_window_minutes=time_window_minutes
        )
        
        # Normalize sequences (remove args, keep tool order)
        normalized = []
        for seq in sequences:
            tool_ids = [s["tool_id"] for s in seq]
            if len(tool_ids) >= 2:
                normalized.append(tool_ids)
        
        # Count occurrences
        pattern_counts = {}
        for tools in normalized:
            key = tuple(tools)
            pattern_counts[key] = pattern_counts.get(key, 0) + 1
        
        # Create/update workflows for frequent patterns
        new_workflows = 0
        boosted_workflows = 0
        
        for tool_sequence, count in pattern_counts.items():
            if count < min_occurrences:
                continue
            
            # Check if similar workflow exists
            existing = self._find_similar_workflow(
                list(tool_sequence), 
                similarity_threshold
            )
            
            if existing:
                # Boost existing workflow
                self.db.increment_workflow_usage(existing["id"])
                boosted_workflows += 1
            else:
                # Create new workflow
                workflow_id = f"wp_{hashlib.md5(str(tool_sequence).encode()).hexdigest()[:12]}"
                
                # Generate name from tools
                tool_names = [t.split("__")[-1] if "__" in t else t 
                             for t in tool_sequence]
                name = " → ".join(tool_names[:3])
                if len(tool_sequence) > 3:
                    name += f" (+{len(tool_sequence)-3})"
                
                # Generate trigger phrases
                trigger_phrases = self._generate_trigger_phrases(tool_sequence)
                
                # Build steps
                steps = []
                parallel_groups = []
                current_group = []
                
                for i, tool_id in enumerate(tool_sequence, 1):
                    tool_name = tool_id.split("__")[-1] if "__" in tool_id else tool_id
                    steps.append({
                        "step_number": i,
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "purpose": f"Execute {tool_name}",
                        "arg_mapping": {},
                        "arg_defaults": {},
                        "depends_on": [],
                        "condition": "",
                        "on_error": "stop",
                        "retry_count": 0
                    })
                    current_group.append(i)
                
                if current_group:
                    parallel_groups.append(current_group)
                
                workflow = {
                    "id": workflow_id,
                    "name": name,
                    "description": f"Auto-learned workflow: {name}",
                    "trigger_phrases": trigger_phrases,
                    "steps": steps,
                    "parallel_groups": parallel_groups,
                    "usage_count": count,
                    "success_rate": 0.8,  # Start optimistic
                    "avg_completion_time_ms": 0.0,
                    "source": "learned",
                    "created_by": "system",
                    "created_at": datetime.utcnow().isoformat(),
                    "last_used_at": datetime.utcnow().isoformat()
                }
                
                self.db.upsert_workflow(workflow)
                new_workflows += 1
                logger.info(f"Learned new workflow: {name} (used {count} times)")
        
        return {
            "patterns_analyzed": len(normalized),
            "new_workflows_created": new_workflows,
            "existing_workflows_boosted": boosted_workflows,
            "workflows": [f"wp_{hashlib.md5(str(k).encode()).hexdigest()[:12]}" 
                         for k, v in pattern_counts.items() if v >= min_occurrences]
        }
    
    def _find_similar_workflow(self, tool_sequence: list[str],
                               threshold: float) -> Optional[dict]:
        """Find an existing workflow with similar tool sequence."""
        all_workflows = self.db.get_all_workflows()
        
        for wf in all_workflows:
            wf_tools = [s["tool_id"] for s in wf.get("steps", [])]
            if len(wf_tools) != len(tool_sequence):
                continue
            
            # Calculate similarity
            matches = sum(1 for a, b in zip(wf_tools, tool_sequence) if a == b)
            similarity = matches / len(tool_sequence)
            
            if similarity >= threshold:
                return wf
        
        return None
    
    def _generate_trigger_phrases(self, tool_sequence: list[str]) -> list[str]:
        """Generate likely trigger phrases from tool names."""
        phrases = []
        
        # Extract action words from tool names
        actions = []
        for tool_id in tool_sequence:
            name = tool_id.split("__")[-1] if "__" in tool_id else tool_id
            # Common action mappings
            if "CREATE" in name or "ADD" in name:
                actions.append("create")
            if "SEND" in name:
                actions.append("send")
            if "UPDATE" in name:
                actions.append("update")
            if "DELETE" in name:
                actions.append("delete")
        
        # Generate phrases
        if len(tool_sequence) == 2:
            phrases.append(f"{actions[0]} and {actions[1]}")
        elif len(tool_sequence) >= 3:
            phrases.append(f"{actions[0]} and notify")
            phrases.append("complete workflow")
        
        return phrases
    
    # -----------------------------------------------------------------------
    # Workflow Execution
    # -----------------------------------------------------------------------
    
    async def execute_workflow(self, workflow_id: str, args: dict,
                               execution_mode: str = "parallel",
                               session_id: Optional[str] = None) -> dict:
        """
        Execute a workflow with the given arguments.

        Calls real tools via self.tool_caller when available.
        Supports parallel execution via asyncio.gather for independent steps.

        Args:
            workflow_id: ID of workflow to execute.
            args: Arguments to pass to workflow steps.
            execution_mode: "parallel", "sequential", or "dry_run".
            session_id: Optional session for schema dedup.

        Returns:
            Execution result with step statuses.
        """
        from tooldns.caller import resolve_args

        workflow = self.db.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_id}")

        execution_id = f"exec_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{workflow_id[:8]}"
        start_time = datetime.utcnow()

        steps_result = []
        step_results_map = {}  # step_number -> result dict (for arg chaining)
        total_tokens = 0
        overall_status = "completed"

        # Determine execution order based on dependencies
        if execution_mode == "sequential":
            execution_order = [[s["step_number"]] for s in workflow["steps"]]
        else:
            execution_order = workflow.get("parallel_groups", [])
            if not execution_order:
                execution_order = [[s["step_number"]] for s in workflow["steps"]]

        for group in execution_order:
            async def _execute_step(step_num):
                step = next((s for s in workflow["steps"] if s["step_number"] == step_num), None)
                if not step:
                    return None

                # Check condition
                if step.get("condition"):
                    if not self._evaluate_condition(step["condition"], args):
                        return {
                            "step": step_num,
                            "tool": step["tool_id"],
                            "status": "skipped",
                            "result": {},
                            "error": "",
                            "tokens_used": 0
                        }

                # Resolve arguments from templates
                arg_mapping = step.get("arg_mapping", {})
                arg_defaults = step.get("arg_defaults", {})
                resolved = {**arg_defaults, **resolve_args(arg_mapping, args, step_results_map)}

                # Dry run
                if execution_mode == "dry_run":
                    return {
                        "step": step_num,
                        "tool": step["tool_id"],
                        "status": "completed",
                        "result": {"dry_run": True, "would_call": step["tool_id"], "resolved_args": resolved},
                        "error": "",
                        "tokens_used": 0
                    }

                # Real execution
                if self.tool_caller:
                    retries = step.get("retry_count", 0)
                    last_error = ""
                    for attempt in range(retries + 1):
                        try:
                            result = await asyncio.to_thread(
                                self.tool_caller, step["tool_id"], resolved
                            )
                            return {
                                "step": step_num,
                                "tool": step["tool_id"],
                                "status": "completed",
                                "result": result,
                                "error": "",
                                "tokens_used": 150
                            }
                        except Exception as e:
                            last_error = str(e)
                            if attempt < retries:
                                logger.warning(
                                    f"Workflow step {step_num} failed (attempt {attempt+1}/{retries+1}): {e}"
                                )
                                continue

                    # All retries exhausted
                    on_error = step.get("on_error", "stop")
                    if on_error == "skip":
                        return {
                            "step": step_num,
                            "tool": step["tool_id"],
                            "status": "skipped",
                            "result": {},
                            "error": last_error,
                            "tokens_used": 0
                        }
                    return {
                        "step": step_num,
                        "tool": step["tool_id"],
                        "status": "failed",
                        "result": {},
                        "error": last_error,
                        "tokens_used": 0
                    }
                else:
                    # No tool_caller — return dry-run-like result
                    return {
                        "step": step_num,
                        "tool": step["tool_id"],
                        "status": "completed",
                        "result": {"no_caller": True, "resolved_args": resolved},
                        "error": "",
                        "tokens_used": 0
                    }

            # Execute group (parallel or sequential)
            if execution_mode == "parallel" and len(group) > 1:
                group_results = await asyncio.gather(
                    *[_execute_step(sn) for sn in group],
                    return_exceptions=True
                )
            else:
                group_results = []
                for sn in group:
                    group_results.append(await _execute_step(sn))

            # Process results
            stop_execution = False
            for res in group_results:
                if isinstance(res, Exception):
                    steps_result.append({
                        "step": 0, "tool": "unknown",
                        "status": "failed", "result": {},
                        "error": str(res), "tokens_used": 0
                    })
                    overall_status = "failed"
                    stop_execution = True
                elif res is not None:
                    steps_result.append(res)
                    step_results_map[res["step"]] = res.get("result", {})
                    total_tokens += res.get("tokens_used", 0)
                    if res["status"] == "failed":
                        step = next((s for s in workflow["steps"] if s["step_number"] == res["step"]), {})
                        if step.get("on_error", "stop") == "stop":
                            overall_status = "failed"
                            stop_execution = True

            if stop_execution:
                break

        completion_time = (datetime.utcnow() - start_time).total_seconds() * 1000

        success = overall_status == "completed"
        self.db.increment_workflow_usage(workflow_id, success=success,
                                         completion_time_ms=completion_time)

        return {
            "execution_id": execution_id,
            "status": overall_status,
            "steps": steps_result,
            "progress": {
                "completed": len([s for s in steps_result if s["status"] == "completed"]),
                "failed": len([s for s in steps_result if s["status"] == "failed"]),
                "skipped": len([s for s in steps_result if s["status"] == "skipped"])
            },
            "started_at": start_time.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "total_tokens_used": total_tokens
        }
    
    def _evaluate_condition(self, condition: str, args: dict) -> bool:
        """Evaluate a step condition against args."""
        # Simple condition evaluation
        # Format: "if {variable} == value" or "if {variable}"
        try:
            # Remove "if " prefix
            condition = condition.replace("if ", "").strip()
            
            # Check for variable existence
            if condition.startswith("{") and condition.endswith("}"):
                var_name = condition[1:-1]
                return bool(args.get(var_name))
            
            # Check equality
            if "==" in condition:
                left, right = condition.split("==", 1)
                left = left.strip()
                right = right.strip().strip('"\'')
                
                if left.startswith("{") and left.endswith("}"):
                    var_name = left[1:-1]
                    return str(args.get(var_name)) == right
            
            return True
        except Exception:
            return True  # Default to executing on error
    
    # -----------------------------------------------------------------------
    # Agent Preference Learning
    # -----------------------------------------------------------------------
    
    def record_tool_selection(self, agent_id: str, tool_id: str,
                              query: str = "", confidence: float = 0.0) -> None:
        """
        Record when an agent selects a tool from search results.
        
        This updates the agent's preferences for future personalized search.
        
        Args:
            agent_id: Agent identifier.
            tool_id: Selected tool ID.
            query: Original search query.
            confidence: Confidence score of the selection.
        """
        # Update agent preferences
        self.db.upsert_agent_preference(agent_id, tool_id, confidence)
        
        # Log for workflow learning
        self.db.log_tool_call(agent_id, tool_id, query)
        
        logger.debug(f"Recorded tool selection: {agent_id} -> {tool_id}")
    
    def get_agent_boosts(self, agent_id: str) -> dict[str, float]:
        """
        Get confidence boosts for tools based on agent preferences.

        Returns:
            Dict mapping tool_id to boost amount.
        """
        pref = self.db.get_agent_preferences(agent_id)
        if not pref:
            return {}

        boosts = {}
        counts = pref.get("tool_selection_counts", {})
        total_selections = sum(counts.values())

        if total_selections == 0:
            return {}

        # Time-based decay: reduce boost for stale preferences
        decay = 1.0
        last_updated = pref.get("last_updated")
        if last_updated:
            try:
                if isinstance(last_updated, str):
                    last_updated = datetime.fromisoformat(last_updated)
                days_since = (datetime.utcnow() - last_updated).total_seconds() / 86400
                decay = max(0.3, 1.0 - days_since / 90)
            except (ValueError, TypeError):
                pass

        # Calculate boost based on selection frequency
        for tool_id, count in counts.items():
            frequency = count / total_selections
            # Boost up to 0.15 based on frequency, decayed over time
            boosts[tool_id] = min(0.15, frequency * 0.3) * decay

        return boosts
