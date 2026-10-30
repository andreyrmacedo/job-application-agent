"""
Created on 2026-10-15
@author: amacedo

Description
-----------
Run the application graph over every open job tab, one tab at a time.

    python run.py                   # every target tab, 25 min cap per tab
    python run.py --timeout 600     # 10 min cap per tab
"""

#%% IMPORTS

from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import csv
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from langchain_mcp_adapters.tools import load_mcp_tools

from browser_tools import make_browser_client, list_tabs, is_target, select_tab
from graph import build_graph, new_state
from settings import (
    log_tool_calls,
    block_repeats,
    safe_files,
    strip_tab_list,
    GRAPH_RECURSION_LIMIT,
    TAB_TIMEOUT,
    MODEL,
)


#%% RUN ONE TAB

async def run_one_tab(graph, latest, config):
    """
    Run the graph on the MCP's current tab.
    Returns the final state; status is 'stuck' if a guard stopped it.
    'latest' always holds the newest state, so a timeout doesn't lose it.
    """
    last_fill = None # field names filled
    last_click = None # url of previous navigator

    async for state in graph.astream(
        new_state(),
        config=config,
        stream_mode='values' # full state after every node
    ):
        latest['state'] = state
        node = state['last_node']

        # # Guard 1: the same fields filled twice in a row
        # if node == 'fill':
        #     filled = state['form_filler_output'].get('filled_fields', [])
        #     names = sorted(field['name'] for field in filled)
        #     if names and names == last_fill:
        #         print(' [guard] same fields filled twice -> page did not advance')
        #         return {**state, 'status': 'stuck'}
        #     last_fill = names
        #     last_click = None # after a fill, clicking "Next" again is fine
        
        # # Guard 2: the same click on the same URL twice, nothing in between
        # elif node == 'navigate':
        #     nav = state['navigator_output']
        #     click = (nav.get('url'), nav.get('target'))
        #     if nav.get('action') == 'clicked' and click == last_click:
        #         print(' [guard] same click on same URL twice -> page did not advance')
        #         return {**state, 'status': 'stuck'}
        #     last_click = click
    
    return state # the graph reached END on its own


#%% TRACKER

DATA = Path(__file__).resolve().parent / 'data' # gitignored
COLUMNS = ['timestamp', 'company', 'title', 'url', 'status', 'seconds',
           'salary_min', 'salary_max', 'salary_currency', 'model', 'trace_id']

def track(url, status, elapsed, state, trace_id):
    """
    Record one tab, right away, so an interrupted run keeps the finished tabs.
    CSV: one row per tab. JSONL: the same plus requirements and the job
    description, kept because postings go offline. Nothing from your profile.
    """
    job = (state or {}).get('explorer_output')
    get = lambda field: getattr(job, field, None)
    record = {
        'timestamp': datetime.now().astimezone().isoformat(timespec='seconds'),
        'company': get('company'), 'title': get('job_title'), 'url': url,
        'status': status, 'seconds': round(elapsed),
        'salary_min': get('salary_min'), 'salary_max': get('salary_max'),
        'salary_currency': get('salary_currency'), 'model': MODEL, 'trace_id': trace_id,
    }
    DATA.mkdir(exist_ok=True)

    csv_file = DATA / 'applications.csv'
    new = not csv_file.exists()
    with open(csv_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow({k: '' if v is None else v for k, v in record.items()})

    full = {**record, 'requirements': get('requirements') or [], 'job_description': get('job_description')}
    with open(DATA / 'applications.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(full, ensure_ascii=False) + '\n')


#%% THE LOOP

async def find_targets(tabs_tool):
    """
    URLs of the open tabs to process, decided once.
    """
    targets = []
    for tab in await list_tabs(tabs_tool):
        if is_target(tab.url) and tab.url not in targets:
            targets.append(tab.url)
    return targets

async def main(timeout=TAB_TIMEOUT):
    async with make_browser_client().session('playwright') as session:
        tools = await load_mcp_tools(session)
        tabs_tool = next(t for t in tools if t.name == 'browser_tabs')
        graph = build_graph(tools, middleware=[log_tool_calls, block_repeats, safe_files, strip_tab_list])

        targets = await find_targets(tabs_tool)
        print(f"[loop] {len(targets)} target tab(s)")

        results = []
        for i, url in enumerate(targets, 1):
            print(f"\n[loop] ({i}/{len(targets)}) {url}")

            # Point the MCP
            if not await select_tab(tabs_tool, url):
                results.append((url, 'tab_not_found', 0))
                continue

            # Start loop
            latest = {} # the newest state, kept even if the tab times out
            # Our own run id becomes the LangSmith trace id, so the tracker rows link to it
            trace_id = str(uuid.uuid4())
            config = {
                'recursion_limit': GRAPH_RECURSION_LIMIT,
                'run_id': trace_id,
                'metadata': {'url': url},
                'tags': ['run.py'],
            }
            start = time.time()
            try:
                final = await asyncio.wait_for(run_one_tab(graph, latest, config), timeout=timeout)
                status = final['status']
            except TimeoutError:
                status = 'timeout'
            except Exception as e:
                status = f"error: {type(e).__name__}"
            final = latest.get('state')
            elapsed = time.time() - start
            results.append((url, status, elapsed))
            tracing = os.getenv('LANGSMITH_TRACING', '').lower() == 'true'
            track(url, status, elapsed, final, trace_id if tracing else None)

    print('\n===== SUMMARY =====')
    for url, status, elapsed in results:
        print(f"{status:<18} {elapsed:5.0f}s {url[:60]}")


#%% RUN

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fill job applications in the open browser tabs.')
    parser.add_argument(\
        '--timeout', 
        type=int, 
        default=TAB_TIMEOUT,
        help=f"max seconds per tab (default {TAB_TIMEOUT})"
    )
    args = parser.parse_args()

    asyncio.run(main(args.timeout))


#%%