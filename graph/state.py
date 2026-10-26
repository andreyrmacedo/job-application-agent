"""
Created on 2026-10-16
@author: amacedo

Description
-----------
The graph's state, the reducer that merges Explorer reports, and the table
that turns a page type into the next status.
"""

#%% IMPORTS

from typing import Annotated, Any, Dict, TypedDict
from agents.explorer import ExplorerOutput


#%% STATUS CLASSIFICATION

PAGE_STATUS = {
    'application_form': 'ready_to_fill', # stop
    'submit_page': 'submit_page', # final stop
    'job_description': 'analyzed', # continue
    'login': 'login', # stop
    'irrelevant': 'irrelevant', # stop
    'redirect': 'analyzed', # continue
    'unknown': 'blocked', # stop
}


#%% REDUCERS

def merge_explorer(a: ExplorerOutput, b: ExplorerOutput) -> ExplorerOutput:
    a_data = a.model_dump()
    b_data = b.model_dump(exclude_none=True, exclude_unset=True)
    b_all = b.model_dump() # all observations, set or not

    result = dict(a_data)

    # Protected fields (don't overwrite)
    PROTECTED = ['job_title', 'company', 'salary_min', 'salary_max', 'salary_currency']
    for field in PROTECTED:
        if not result.get(field) and b_data.get(field):
            result[field] = b_data[field]


    # Accumulate 'requirements'
    if 'requirements' in b_data:
        existing = a_data.get('requirements') or []
        incoming = b_data['requirements']

        seen = set(x.lower() for x in existing)
        merged = existing.copy()

        for req in incoming:
            if req.lower() not in seen:
                merged.append(req)
                seen.add(req.lower())

        result['requirements'] = merged


    # 'job_description' (prefer longer version)
    a_jd = a_data.get('job_description')
    b_jd = b_data.get('job_description')

    if b_jd:
        if not a_jd:
            result['job_description'] = b_jd
        elif b_jd not in a_jd:
            # prefer longer
            result['job_description'] = (
                b_jd if len(b_jd) > len(a_jd) else a_jd
            )


    # Overwrite everything else
    for k, v in b_all.items():
        if k not in [*PROTECTED, 'requirements', 'job_description']:
            result[k] = v

    return ExplorerOutput(**result)


#%% STATE

class State(TypedDict):
    # One output per agent
    explorer_output: Annotated[ExplorerOutput, merge_explorer]
    navigator_output: Dict[str, Any]
    form_filler_output: Dict[str, Any]
    answer_writer_output: Dict[str, Any]

    # Control
    status: str
    last_node: str


# A fresh state for one tab
def new_state() -> State:
    """
    'explorer_output' is left out on purpose: the first Explorer report becomes its
    value. An empty {} there would be passed to merge_explorer as the previous report.
    """
    return {
        'navigator_output': {},
        'form_filler_output': {},
        'answer_writer_output': {},
        'status': 'open',
        'last_node': '',
    }

#%%