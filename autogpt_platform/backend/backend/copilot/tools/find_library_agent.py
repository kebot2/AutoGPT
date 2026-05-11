"""Tool for searching agents in the user's library."""

from typing import Any

from backend.copilot.model import ChatSession

from .agent_search import search_agents, search_library_for_creation
from .base import BaseTool
from .models import ToolResponseBase


class FindLibraryAgentTool(BaseTool):
    """Tool for searching agents in the user's library."""

    @property
    def name(self) -> str:
        return "find_library_agent"

    @property
    def description(self) -> str:
        return (
            "Search user's library agents. Returns graph_id, schemas for "
            "sub-agent composition. Omit query to list all. Set "
            "include_graph=true to fetch the full graph structure (nodes + "
            "links) for debugging or editing. "
            "Set for_creation=true with a `goal_summary` BEFORE calling "
            "`create_agent` to check whether the user already has a "
            "functionally similar agent (hybrid semantic + lexical search); "
            "the response will tell you whether to suggest an existing "
            "agent or proceed with creation."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search by name/description. Omit to list all.",
                },
                "include_graph": {
                    "type": "boolean",
                    "description": (
                        "When true, includes the full graph structure "
                        "(nodes + links) for each found agent. "
                        "Use when you need to inspect, debug, or edit an agent."
                    ),
                    "default": False,
                },
                "for_creation": {
                    "type": "boolean",
                    "description": (
                        "Run a hybrid semantic + lexical similarity search "
                        "over the user's library to surface functionally "
                        "similar agents BEFORE calling create_agent. "
                        "Requires `goal_summary`. Call this once per "
                        "create-agent intent to satisfy the similarity "
                        "gate."
                    ),
                    "default": False,
                },
                "goal_summary": {
                    "type": "string",
                    "description": (
                        "One- or two-sentence description of what the user "
                        "wants the new agent to do. Required when "
                        "for_creation=true."
                    ),
                },
            },
            "required": [],
        }

    @property
    def requires_auth(self) -> bool:
        return True

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        query: str = "",
        include_graph: bool = False,
        for_creation: bool = False,
        goal_summary: str = "",
        **kwargs,
    ) -> ToolResponseBase:
        if for_creation:
            return await search_library_for_creation(
                goal_summary=goal_summary or query,
                session_id=session.session_id,
                user_id=user_id,
            )
        return await search_agents(
            query=query.strip(),
            source="library",
            session_id=session.session_id,
            user_id=user_id,
            include_graph=include_graph,
        )
