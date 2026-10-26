"""
Created on 2026-10-04
@author: amacedo

Description
-----------
One node per agent. Each factory builds its agent once; the node it returns
runs that agent on the current tab and writes its report into the state.
"""

#%% IMPORTS

from agents.explorer import create_explorer_agent, EXPLORER_MESSAGE, EXPLORER_TOOLS, ExplorerOutput
from agents.navigator import create_navigator_agent, NAVIGATOR_MESSAGE, NAVIGATOR_TOOLS
from agents.form_filler import create_form_filler_agent, FORM_FILLER_MESSAGE, FORM_FILLER_TOOLS
from agents.answer_writer import create_answer_writer_agent, ANSWER_WRITER_MESSAGE, ANSWER_WRITER_TOOLS
from browser_tools import list_tabs, current_tab
from graph.state import PAGE_STATUS, State
from settings import PROFILE


#%% SALARY

def salary_target(job: ExplorerOutput, rules: dict):
    """
    The salary values to enter, from the profile's 'salary' rules and the
    posting's band. None when salary filling is off.
    A band in another currency is ignored: floor and ceiling are in the
    profile's currency.
    """
    if not rules or not rules.get('enabled'):
        return None

    currency = rules['default_currency']
    band_high = job.salary_max or job.salary_min
    same_currency = job.salary_currency in (None, currency)

    if band_high and same_currency and rules.get('mirror_posting_band_when_shown'):
        target = min(max(band_high, rules['floor']), rules['ceiling'])
    else:
        target = rules['no_band_value']

    single = (rules.get('single_numeric_box_required') or {}).get('custom_value') or target
    template = (rules.get('free_text_required') or {}).get('template', '{target} {currency}')

    return {
        'single_box': single,
        'min_max': (target, rules['ceiling']),
        'free_text': template.format(target=target, currency=currency),
        'currency': currency,
    }


#%% NODES

def explorer_node(
    llm, 
    tools, 
    middleware, 
    agent_tools=EXPLORER_TOOLS,
    agent_message=EXPLORER_MESSAGE
):
    """
    Build explorer node.
    """
    # Define tools
    explorer_tools = [t for t in tools if t.name in agent_tools]

    # Create explorer agent
    explorer = create_explorer_agent(llm, explorer_tools, middleware=middleware)

    async def explore(state: State) -> dict:
        # Invoke llm & get results
        result = await explorer.ainvoke(
            {'messages': [{'role': 'user', 'content': agent_message}]},
        )
        report = result.get('structured_response')
        if report is None: # call limit reached before a report
            print('    [explore] call limit reached -> blocked')
            report = ExplorerOutput(page_type='unknown', has_fields=False,
                                    requires_resume_upload=False)

        # Classify 'next_status'
        status = PAGE_STATUS.get(report.page_type, 'blocked')

        # Print relevant information for easy tracking
        print(f"    [explore] page_type={report.page_type} next={status} "
              f"has_fields={report.has_fields} resume={report.requires_resume_upload}")

        return {
            'explorer_output': report,
            'status': status,
            'last_node': 'explore',
        }

    return explore


def navigator_node(
    llm, 
    tools, 
    middleware, 
    agent_tools=NAVIGATOR_TOOLS,
    agent_message=NAVIGATOR_MESSAGE
):
    """
    Build navigator node.
    """
    # Define tools
    navigator_tools = [t for t in tools if t.name in agent_tools]
    tabs_tool = next(t for t in tools if t.name == 'browser_tabs')

    # Create navigator agent
    navigator = create_navigator_agent(llm, navigator_tools, middleware=middleware)

    async def navigate(state: State) -> dict:
        # How many tabs exist BEFORE the click
        n_before = len(await list_tabs(tabs_tool))

        # Invoke llm & get results
        result = await navigator.ainvoke(
            {'messages': [{'role': 'user', 'content': agent_message}]},
        )
        report = result.get('structured_response')
        if report is None: # call limit reached before a report
            print('    [navigate] call limit reached -> no_action')
            return {
                'navigator_output': {'action': 'no_action', 'target': ''},
                'last_node': 'navigate',
            }

        # Print relevant information for easy tracking
        print(f"    [navigate] action={report.action} target={report.target}")

        # If the click opened a new tab, move to it
        tabs = await list_tabs(tabs_tool)
        if len(tabs) > n_before:
            newest = tabs[-1] # the last one
            await tabs_tool.ainvoke({'action': 'select', 'index': newest.index})
            print(f"    [navigate] new tab opened -> switched to {newest.url}")

        # where we are now (could be useful at some point)
        tab = await current_tab(tabs_tool)
        url = tab.url if tab else None

        return {
            'navigator_output': {
                **report.model_dump(),
                'url': url
            },
            'last_node': 'navigate',
        }

    return navigate
    
    
def form_filler_node(
    llm, 
    tools, 
    middleware,  
    agent_tools=FORM_FILLER_TOOLS,
    agent_message=FORM_FILLER_MESSAGE
):
    """
    Build form_filler node.
    """
    # Define tools
    form_filler_tools = [t for t in tools if t.name in agent_tools]

    # Create form_filler agent
    form_filler = create_form_filler_agent(llm, form_filler_tools, middleware=middleware)

    async def fill(state: State) -> dict:
        # Job context after the profile, so the prompt's start (and llama.cpp's cache) stays the same
        job = state['explorer_output']
        salary = salary_target(job, PROFILE.get('salary'))
        band = f"{job.salary_min} - {job.salary_max} {job.salary_currency or ''}" if job.salary_max else 'not shown'
        context = (
            "\n## JOB CONTEXT\n"
            f"job_title: {job.job_title or ''}\n"
            f"company: {job.company or ''}\n"
            f"posting salary range: {band}\n"
        )
        if salary:
            context += (
                "salary to enter (mandatory salary fields only):\n"
                f"- single number box: {salary['single_box']}\n"
                f"- min and max boxes: {salary['min_max'][0]} and {salary['min_max'][1]}\n"
                f"- free text: {salary['free_text']}\n"
            )

        # Invoke llm & get results
        result = await form_filler.ainvoke(
            {'messages': [{'role': 'user', 'content': agent_message + context}]},
        )
        report = result.get('structured_response')
        if report is None: # call limit reached before a report
            print('    [fill] call limit reached; nothing reported')
            return {'form_filler_output': {}, 'last_node': 'fill'}

        # Print relevant information for easy tracking
        print(f"    [fill] filled={report.filled_fields} skipped={report.skipped_fields} "
              f"open_questions={report.questions_to_answer}")

        return {
            'form_filler_output': report.model_dump(),
            'last_node': 'fill'
        }

    return fill


def answer_writer_node(
    llm, 
    tools, 
    middleware, 
    agent_tools=ANSWER_WRITER_TOOLS,
    agent_message=ANSWER_WRITER_MESSAGE
):
    """
    Build answer_writer node.
    """
    # Define tools
    answer_writer_tools = [t for t in tools if t.name in agent_tools]

    # Create answer_writer agent
    answer_writer = create_answer_writer_agent(llm, answer_writer_tools, middleware=middleware)

    async def answer(state: State) -> dict:
        # Job context gathered by the Explorer
        job = state['explorer_output']
        context = (
            f"job_title: {job.job_title or ''}\n"
            f"company: {job.company or ''}\n"
            f"requirements: {'; '.join(job.requirements)}\n"
            f"job_description: {job.job_description or ''}"
        )

        # Adapt prompt & invoke llm
        prompt = f"{agent_message}\n{context}"
        result = await answer_writer.ainvoke(
            {'messages': [{'role': 'user', 'content': prompt}]},
        )
        report = result.get('structured_response')
        if report is None: # call limit reached before a report
            print('    [answer] call limit reached')
            return {'answer_writer_output': {}, 'last_node': 'answer'}

        # Print relevant information for easy tracking
        print(f"    [answer] answered={len(report.answered)} "
              f"unanswered={len(report.unanswered)}")

        return {
            'answer_writer_output': report.model_dump(),
            'last_node': 'answer',
        }

    return answer
