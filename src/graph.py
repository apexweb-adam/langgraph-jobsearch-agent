"""LangGraph wiring for the 3-node discovery -> scoring -> approval pipeline.

The graph is intentionally linear in the MVP. Phase 2 can add branches:
- conditional on score: tailor CV/CL only above threshold
- approval loop that re-fetches user decisions and routes to send/draft

To inspect: from src.graph import build; build().get_graph().draw_mermaid()
"""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from .state import GraphState
from .nodes.discovery import discovery_node
from .nodes.scoring import scoring_node
from .nodes.approval import approval_node


def build() -> "CompiledGraph":
    g = StateGraph(GraphState)
    g.add_node("discovery", discovery_node)
    g.add_node("scoring", scoring_node)
    g.add_node("approval", approval_node)

    g.add_edge(START, "discovery")
    g.add_edge("discovery", "scoring")
    g.add_edge("scoring", "approval")
    g.add_edge("approval", END)

    return g.compile()
