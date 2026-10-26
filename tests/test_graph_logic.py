"""
Created on 2026-10-24
@author: amacedo

Description
-----------
Unit tests for the parts of the graph that are plain Python: routing, the
Explorer reducer, tab parsing, the repeat check and context trimming.
No LLM, no browser, no MCP.

    python -m pytest tests
"""

#%% IMPORTS

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END

from agents.explorer import ExplorerOutput
from agents.navigator import NavigatorOutput
from browser_tools import is_target, list_tabs
from graph.nodes import salary_target
from graph.routing import route_after_explore, route_after_navigate, router_after_fill
from graph.state import merge_explorer
from settings import ClearToolUsesInSteps, same_action


#%% HELPERS

def report(**kw):
    """An Explorer report with the required fields filled in."""
    base = dict(has_fields=False, requires_resume_upload=False, page_type='job_description')
    return ExplorerOutput(**{**base, **kw})


#%% ROUTING

@pytest.mark.parametrize('status, dialog, expected', [
    ('submit_page', True, END),        # submit ends the graph, even with a dialog open
    ('ready_to_fill', True, 'navigate'), # a dialog is handled before the form
    ('ready_to_fill', False, 'fill'),
    ('analyzed', False, 'navigate'),
    ('login', False, 'navigate'),
    ('irrelevant', False, END),
    ('blocked', False, END),
])
def test_route_after_explore(status, dialog, expected):
    state = {'status': status, 'explorer_output': report(dialog=dialog)}
    assert route_after_explore(state) == expected


@pytest.mark.parametrize('action, expected', [('no_action', END), ('clicked', 'explore')])
def test_route_after_navigate(action, expected):
    assert route_after_navigate({'navigator_output': {'action': action}}) == expected


def test_router_after_fill():
    question = {'name': 'why', 'prompt': 'Why us?'}
    assert router_after_fill({'form_filler_output': {'questions_to_answer': [question]}}) == 'answer'
    assert router_after_fill({'form_filler_output': {'questions_to_answer': []}}) == 'navigate'
    assert router_after_fill({'form_filler_output': {}}) == 'navigate' # filler gave no report


#%% EXPLORER REDUCER

def test_merge_explorer_keeps_job_context_across_pages():
    page1 = report(job_title='Data Scientist', company='Acme', job_description='short',
                   requirements=['Python'])
    page2 = report(job_title='Sign in', job_description='a longer job description',
                   requirements=['python', 'SQL'], has_fields=True, page_type='application_form')

    merged = merge_explorer(page1, page2)

    assert merged.job_title == 'Data Scientist'          # first value is protected
    assert merged.company == 'Acme'                      # not erased by an unset field
    assert merged.requirements == ['Python', 'SQL']      # accumulated, case-insensitive dedupe
    assert merged.job_description == 'a longer job description' # longer one wins
    assert merged.page_type == 'application_form'        # observations follow the new page
    assert merged.has_fields is True


def test_merge_explorer_keeps_the_salary_band():
    job_page = report(salary_min=95_000, salary_max=125_000, salary_currency='CAD')
    form_page = report(page_type='application_form', has_fields=True) # no band shown here

    merged = merge_explorer(job_page, form_page)

    assert (merged.salary_min, merged.salary_max, merged.salary_currency) == (95_000, 125_000, 'CAD')


#%% SALARY

RULES = {
    'enabled': True, 'mirror_posting_band_when_shown': True,
    'floor': 90_000, 'ceiling': 120_000, 'default_currency': 'CAD', 'no_band_value': 100_000,
    'free_text_required': {'template': '{target} {currency}, negotiable'},
    'single_numeric_box_required': {'use': 'target', 'custom_value': None},
}


@pytest.mark.parametrize('band, expected', [
    ((70_000, 85_000, 'CAD'), 90_000),    # band below the floor -> floor
    ((95_000, 110_000, 'CAD'), 110_000),  # inside -> the band's high end
    ((100_000, 140_000, 'CAD'), 120_000), # above the ceiling -> ceiling
    ((None, None, None), 100_000),        # no band -> no_band_value
    ((95_000, 110_000, 'USD'), 100_000),  # other currency -> band ignored
    ((95_000, 110_000, None), 110_000),   # currency not shown -> assume the profile's
])
def test_salary_target(band, expected):
    low, high, currency = band
    s = salary_target(report(salary_min=low, salary_max=high, salary_currency=currency), RULES)

    assert s['single_box'] == expected
    assert s['min_max'] == (expected, 120_000)
    assert s['free_text'] == f'{expected} CAD, negotiable'


def test_salary_target_off():
    assert salary_target(report(), {**RULES, 'enabled': False}) is None
    assert salary_target(report(), None) is None


#%% BROWSER HELPERS

@pytest.mark.parametrize('url, expected', [
    ('https://boards.greenhouse.io/acme/jobs/1', True),
    ('https://www.linkedin.com/jobs/view/1', False),
    ('https://merchantpool1.linkedin.com/x', False),     # subdomains are skipped too
    ('https://notlinkedin.com/jobs', True),              # but not look-alike hosts
    ('about:blank', False),
    ('chrome://newtab/', False),
])
def test_is_target(url, expected):
    assert is_target(url) is expected


class FakeTabsTool:
    """Returns a browser_tabs 'list' result the way the MCP does."""
    async def ainvoke(self, args):
        text = ('### Result\n'
                '- 0: [Acme - Data Scientist](https://acme.com/jobs/1)\n'
                '- 1: (current) [Apply [beta]](https://acme.com/apply?id=1)\n')
        return [{'type': 'text', 'text': text}]


def test_list_tabs_parses_the_mcp_output():
    tabs = asyncio.run(list_tabs(FakeTabsTool()))

    assert [t.index for t in tabs] == [0, 1]
    assert tabs[0].url == 'https://acme.com/jobs/1' and not tabs[0].current
    assert tabs[1].title == 'Apply [beta]' and tabs[1].current


#%% MIDDLEWARE HELPERS

def test_same_action_ignores_the_element_description():
    a = {'name': 'browser_click', 'args': {'ref': 'e12', 'element': 'Next button'}}
    b = {'name': 'browser_click', 'args': {'ref': 'e12', 'element': 'the Next button'}}
    c = {'name': 'browser_click', 'args': {'ref': 'e13', 'element': 'Next button'}}
    assert same_action(a, b)
    assert not same_action(a, c)


def test_navigator_report_without_target():
    assert NavigatorOutput(action='no_action').target == ''


#%% CONTEXT TRIMMING

def tool_history(n):
    """n tool calls, each an AIMessage followed by its ToolMessage."""
    messages = []
    for i in range(n):
        messages.append(AIMessage('', tool_calls=[{'id': f'c{i}', 'name': 'browser_snapshot', 'args': {}}]))
        messages.append(ToolMessage(f'snapshot {i}', tool_call_id=f'c{i}'))
    return messages


@pytest.mark.parametrize('n_tools, n_cleared', [
    (3, 0),    # nothing older than the newest 3
    (12, 0),   # 9 old results: not a full step of 10 yet
    (13, 10),  # the first full step
    (22, 10),  # the line stays put between steps, so the prompt prefix is unchanged
    (23, 20),
])
def test_trimming_moves_in_steps(n_tools, n_cleared):
    messages = tool_history(n_tools)
    ClearToolUsesInSteps(trigger=0, keep=3, step=10).apply(messages, count_tokens=lambda m: 1)

    cleared = [m for m in messages if isinstance(m, ToolMessage) and m.content == '[cleared]']
    assert len(cleared) == n_cleared
    assert messages[-1].content == f'snapshot {n_tools - 1}' # the newest result is always kept


def test_trimming_waits_for_the_trigger():
    messages = tool_history(30)
    ClearToolUsesInSteps(trigger=75_000, keep=3, step=10).apply(messages, count_tokens=lambda m: 1_000)
    assert all(m.content != '[cleared]' for m in messages if isinstance(m, ToolMessage))
