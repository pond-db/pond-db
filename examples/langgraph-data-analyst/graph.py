"""LangGraph workflow: Data Engineer -> Analyst -> Report Writer.

Each agent runs sequentially. They share state through PondDB — not through
the LLM context window. The Data Engineer uploads data, the Analyst queries
it and saves queries, the Report Writer reads saved queries and writes a
summary.
"""

from __future__ import annotations

from langgraph.graph import END, START, MessagesState, StateGraph

from agents import analyst, data_engineer, report_writer

# Build the sequential workflow
workflow = StateGraph(MessagesState)

workflow.add_node("data_engineer", data_engineer)
workflow.add_node("analyst", analyst)
workflow.add_node("report_writer", report_writer)

# Sequential pipeline: engineer -> analyst -> report writer
workflow.add_edge(START, "data_engineer")
workflow.add_edge("data_engineer", "analyst")
workflow.add_edge("analyst", "report_writer")
workflow.add_edge("report_writer", END)

graph = workflow.compile()
