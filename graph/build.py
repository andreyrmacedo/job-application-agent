"""
Created on 2026-10-04
@author: amacedo
Description
-----------
Wire the nodes and routers into one compiled graph.
"""

#%% IMPORTS

from langchain.agents.middleware import ModelCallLimitMiddleware
from langgraph.graph import StateGraph, START, END
from settings import LLM_EXPLORE, LLM_NAVIGATE, LLM_FILL, LLM_ANSWER, TRIM_TOOL_CONTEXT
from graph.state import State
from graph.nodes import explorer_node, navigator_node, form_filler_node, answer_writer_node
from graph.routing import route_after_explore, route_after_navigate, router_after_fill


#%% BUILD GRAPH

# Build graph
def build_graph(tools, middleware):
    """
    Compile the graph. 'tools' must come from the live MCP session.
    """
    graph = StateGraph(State)

    graph.add_node('explore', explorer_node(LLM_EXPLORE, tools, middleware))
    graph.add_node('navigate', navigator_node(LLM_NAVIGATE, tools, middleware))
    graph.add_node(
        'fill', 
        form_filler_node(
            LLM_FILL, 
            tools, 
            [*middleware, TRIM_TOOL_CONTEXT]
        )
    )
    graph.add_node('answer', answer_writer_node(LLM_ANSWER, tools, middleware))

    # Start
    graph.add_edge(START, 'explore')

    # Conditional edges
    graph.add_conditional_edges(
        'explore',
        route_after_explore,
        {'navigate': 'navigate', 'fill': 'fill', END: END}
    )
    graph.add_conditional_edges(
        'navigate',
        route_after_navigate,
        {'explore': 'explore', END: END}
    )
    graph.add_conditional_edges(
        'fill',
        router_after_fill,
        {'answer': 'answer', 'navigate': 'navigate'}
    )
    graph.add_edge('answer', 'navigate')

    return graph.compile()

#%%