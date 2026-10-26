"""
Created on 2026-10-18
@author: amacedo

Description
-----------
The conditional edges: which node runs after explore, navigate and fill.
"""

#%% IMPORTS

from langgraph.graph import END
from graph.state import State

#%% ROUTING

def route_after_explore(state: State) -> str:
    status = state['status']

    # 'submit_page' ends the graph
    if status == 'submit_page':
        print("    [route] submit_page -> END (you submit it)")
        return END

    # Dialog is checked before 'has_fields'
    if state['explorer_output'].dialog:
        print("    [route] dialog -> navigate")
        return 'navigate'

    if status == 'ready_to_fill':
        print("    [route] ready_to_fill -> fill")
        return 'fill'

    if status in ['analyzed', 'login']:
        print(f"    [route] {status} -> navigate")
        return 'navigate'

    # 'irrelevant', 'blocked'
    print(f"    [route] {status} -> END")
    return END


def route_after_navigate(state: State) -> str:
    # Another 'explore' may send us back here
    if state['navigator_output'].get('action') == 'no_action':
        print("    [route] no_action -> END")
        return END

    return 'explore'


def router_after_fill(state: State) -> str:
    questions = state['form_filler_output'].get('questions_to_answer')

    if questions:
        print(f"    [route] {len(questions)} open question(s) -> answer")
        return 'answer'

    print("    [route] no open questions -> navigate")
    return 'navigate'


#%%