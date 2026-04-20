"""CreateAgentTool - Creates agents from pre-built JSON."""

import logging
import uuid
from typing import Any

from backend.copilot.model import ChatSession

from .agent_generator.pipeline import fetch_library_agents, fix_validate_and_save
from .base import BaseTool
from .decompose_goal import needs_build_plan_approval
from .helpers import require_guide_read
from .models import ErrorResponse, ToolResponseBase

logger = logging.getLogger(__name__)


class CreateAgentTool(BaseTool):
    """Tool for creating agents from pre-built JSON."""

    @property
    def name(self) -> str:
        return "create_agent"

    @property
    def description(self) -> str:
        return (
            "Create a new agent from JSON (nodes + links). Validates, "
            "auto-fixes, and saves. "
            "Requires get_agent_building_guide first (refuses otherwise)."
        )

    @property
    def requires_auth(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_json": {
                    "type": "object",
                    "description": "Agent graph with 'nodes' and 'links' arrays.",
                },
                "library_agent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Library agent IDs as building blocks.",
                },
                "save": {
                    "type": "boolean",
                    "description": "Save the agent (default: true). False for preview.",
                    "default": True,
                },
                "folder_id": {
                    "type": "string",
                    "description": "Folder ID to save into (default: root).",
                },
            },
            "required": ["agent_json"],
        }

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        agent_json: dict[str, Any] | None = None,
        save: bool = True,
        library_agent_ids: list[str] | None = None,
        folder_id: str | None = None,
        **kwargs,
    ) -> ToolResponseBase:
        session_id = session.session_id if session else None

        guide_gate = require_guide_read(session, "create_agent")
        if guide_gate is not None:
            return guide_gate

        # Enforce the decompose_goal approval gate at the code level.
        # Prompt-only "STOP" is unreliable: the LLM has been observed
        # (a) calling decompose_goal + create_agent in the same turn and
        # (b) skipping decompose_goal entirely on follow-up build requests.
        # Require that the most recent user message is an approval AND a
        # decompose_goal call exists before it in the session.
        if session and needs_build_plan_approval(session):
            return ErrorResponse(
                message=(
                    "You must call decompose_goal first and wait for user "
                    "approval before calling create_agent. Call decompose_goal "
                    "now with the build steps, then end your turn — the "
                    "platform will resume the conversation after the user "
                    "responds with Approved (or Approved with modifications)."
                ),
                error="build_plan_approval_required",
                session_id=session_id,
            )

        if not agent_json:
            return ErrorResponse(
                message=(
                    "Please provide agent_json with the complete agent graph. "
                    "Use find_block to discover blocks, then generate the JSON."
                ),
                error="missing_agent_json",
                session_id=session_id,
            )

        if library_agent_ids is None:
            library_agent_ids = []

        nodes = agent_json.get("nodes", [])
        if not nodes:
            return ErrorResponse(
                message="The agent JSON has no nodes. An agent needs at least one block.",
                error="empty_agent",
                session_id=session_id,
            )

        # Ensure top-level fields
        if "id" not in agent_json:
            agent_json["id"] = str(uuid.uuid4())
        if "version" not in agent_json:
            agent_json["version"] = 1
        if "is_active" not in agent_json:
            agent_json["is_active"] = True

        # Fetch library agents for AgentExecutorBlock validation
        library_agents = await fetch_library_agents(user_id, library_agent_ids)

        return await fix_validate_and_save(
            agent_json,
            user_id=user_id,
            session_id=session_id,
            save=save,
            is_update=False,
            default_name="Generated Agent",
            library_agents=library_agents,
            folder_id=folder_id,
        )
