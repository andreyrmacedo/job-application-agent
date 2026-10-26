"""
Created on 2026-10-19
@author: amacedo

Description
-----------
Model benchmark on a LOCAL practice site: the project's own Explorer, Navigator
and Form Filler, with their own prompts, tools and schemas; only the LLM changes.

Five tasks, N runs each. A task passes only if
    - its goal was met, read from the page itself (not from the model's report)
    - the agent delivered its structured report
    - no model request hung (errored, or took over 60 s)

The fill task's goal is the 6 fields right and still being on the form. The
consent checkbox is recorded in 'detail' but not scored.

    python -m http.server 8765 --directory eval/site &
    brave-browser --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/bakeoff-profile &
    PROFILE_FILE=profile.example.yaml LANGSMITH_TRACING=false BROWSER_URL=http://localhost:9333 \\
        python eval/bakeoff.py 5 <label>

Results go to eval/results/result_<label>.json.
"""

#%% IMPORTS

import asyncio, json, os, re, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / 'results'
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
load_dotenv(REPO / '.env')
os.environ.setdefault('LANGSMITH_PROJECT', 'job-agent-model-bakeoff')
assert os.environ.get('BROWSER_URL') == 'http://localhost:9333', 'refusing to run against your own browser'

import httpx
import settings
from langchain_openai import ChatOpenAI
from langchain_core.callbacks import AsyncCallbackHandler
from langchain.agents.middleware import wrap_tool_call
from langchain_mcp_adapters.tools import load_mcp_tools
from browser_tools import make_browser_client
import agents.explorer as ex
import agents.navigator as nav
import agents.form_filler as ff


#%% CONFIG

SITE = 'http://localhost:8765'
MODEL = httpx.get(settings.BASE_URL + '/models', timeout=10).json()['data'][0]['id']
TEMP = float(os.getenv('TEMP', '0'))
HANG_SECONDS = 60 # a model call slower than this counts as hung

LLM = ChatOpenAI(
    base_url=settings.BASE_URL,
    model=MODEL,
    api_key=settings.API_KEY,
    temperature=TEMP,
    timeout=90,
    max_retries=0, # a hang shows up as one 90 s timeout, not 3 x 240 s
    extra_body={'chat_template_kwargs': {'enable_thinking': False}}
)

REF = re.compile(r'^(f\d+)?e\d+$') # a bare element ref, as the tools expect

# What the filled form must hold (read from the page after the fill)
FORM_STATE = """() => ({first: first.value, last: last.value, email: email.value,
    phone: phone.value, city: city.value, country: document.getElementById('country-value').textContent,
    consent: document.getElementById('consent').checked, path: location.pathname})"""


#%% MEASUREMENT

def text(r):
    if isinstance(r, list):
        return '\n'.join(b.get('text', '') for b in r if isinstance(b, dict))
    return str(getattr(r, 'content', r))


class Meter(AsyncCallbackHandler):
    """
    Seconds, output tokens and outcome of every model call.
    """
    def __init__(self): self.calls, self._t = [], {}
    async def on_chat_model_start(self, s, m, *, run_id, **kw): self._t[run_id] = time.time()
    async def on_llm_end(self, response, *, run_id, **kw):
        um = getattr(response.generations[0][0].message, 'usage_metadata', None) or {}
        self.calls.append((time.time() - self._t.pop(run_id), um.get('output_tokens'), 'ok'))
    async def on_llm_error(self, error, *, run_id, **kw):
        self.calls.append((time.time() - self._t.pop(run_id, time.time()), None, type(error).__name__))


TOOLS_LOG = []

@wrap_tool_call
async def record(request, handler):
    """
    Every tool call of the current agent run, and whether it errored.
    """
    call = request.tool_call
    result = await handler(request)
    out = text(getattr(result, 'content', result))
    TOOLS_LOG.append({'name': call['name'], 'args': call['args'], 'error': '### Error' in out})
    return result


async def page_eval(T, fn):
    r = text(await T['browser_evaluate'].ainvoke({'function': fn}))
    m = re.search(r'### Result\n(.*?)\n###', r + '\n###', re.S)
    return json.loads(m.group(1)) if m else None


async def open_page(T, path, wait):
    await T['browser_navigate'].ainvoke({'url': SITE + path})
    await asyncio.sleep(wait)


async def run_agent(agent, content):
    meter = Meter(); TOOLS_LOG.clear(); t0 = time.time()
    try:
        r = await agent.ainvoke({'messages': [{'role': 'user', 'content': content}]},
                                config={'callbacks': [meter], 'recursion_limit': 60})
        report, err = r.get('structured_response'), None
    except Exception as e:
        report, err = None, f'{type(e).__name__}: {str(e)[:120]}'

    targets = [c['args'].get('target') for c in TOOLS_LOG if isinstance(c.get('args'), dict) and 'target' in c['args']]
    for c in TOOLS_LOG: # fill_form carries targets inside 'fields'
        if c['name'] == 'browser_fill_form':
            targets += [f.get('target') for f in c['args'].get('fields', [])]

    return {
        'seconds': round(time.time() - t0, 1), 'report': report, 'error': err,
        'llm_calls': len(meter.calls),
        'hangs': sum(1 for c in meter.calls if c[2] != 'ok' or c[0] > HANG_SECONDS),
        'output_tokens': sum(c[1] or 0 for c in meter.calls),
        'targets': len(targets), 'bad_targets': [t for t in targets if not (isinstance(t, str) and REF.match(t))],
        'clicks_ok': sum(1 for c in TOOLS_LOG if c['name'] == 'browser_click' and not c['error']),
        'tool_errors': sum(1 for c in TOOLS_LOG if c['error']),
    }


def score(r, task, goal, detail=''):
    """
    The pass rule: goal met (from the page), report delivered, nothing hung.
    """
    r.update(task=task, goal=bool(goal), detail=detail,
             ok=bool(goal) and r['report'] is not None and r['hangs'] == 0)
    return r


#%% THE TASKS

async def main(runs, label):
    profile = settings.PROFILE['personal']
    want = {'first': profile['first_name'], 'last': profile['last_name'], 'email': profile['email'],
            'phone': profile['phone'], 'city': profile['city'], 'country': 'Canada'}
    norm = lambda v: re.sub(r'\D', '', v) if v and v[:1].isdigit() else (v or '').strip().lower()

    rows = []
    async with make_browser_client().session('playwright') as s:
        tools = await load_mcp_tools(s)
        T = {t.name: t for t in tools}
        pick = lambda names: [t for t in tools if t.name in names]
        explorer = ex.create_explorer_agent(LLM, pick(ex.EXPLORER_TOOLS), middleware=[record])
        navigator = nav.create_navigator_agent(LLM, pick(nav.NAVIGATOR_TOOLS), middleware=[record])
        filler = ff.create_form_filler_agent(LLM, pick(ff.FORM_FILLER_TOOLS), middleware=[record])

        for k in range(runs):
            # 1. Explorer on the job page
            await open_page(T, '/job.html', 1)
            r = await run_agent(explorer, ex.EXPLORER_MESSAGE)
            rows.append(score(r, 'explore job page',
                              r['report'] and r['report'].page_type == 'job_description'))

            # 2. Navigator on the job page: exactly one click, on Apply
            await open_page(T, '/job.html', 1)
            r = await run_agent(navigator, nav.NAVIGATOR_MESSAGE)
            url = await page_eval(T, '() => location.pathname')
            rows.append(score(r, 'navigate job page', url == '/apply.html' and r['clicks_ok'] == 1,
                              f"url={url} report={r['report'] and (r['report'].action, r['report'].target)}"))

            # 3. Explorer on the loaded form (it appears 7 s after the page shell)
            await open_page(T, '/apply.html', 8)
            r = await run_agent(explorer, ex.EXPLORER_MESSAGE)
            rows.append(score(r, 'explore form',
                              r['report'] and r['report'].page_type == 'application_form' and r['report'].has_fields))

            # 4. Filler on the loaded form: 6 values right, still on the form
            await open_page(T, '/apply.html', 8)
            r = await run_agent(filler, ff.FORM_FILLER_MESSAGE)
            got = await page_eval(T, FORM_STATE) or {}
            correct = sum(1 for key, v in want.items() if norm(str(got.get(key, ''))) == norm(str(v)))
            rows.append(score(r, 'fill form', correct == 6 and got.get('path') == '/apply.html',
                              f"{correct}/6 fields, consent={'ticked' if got.get('consent') else 'left'} (not scored), "
                              f"path={got.get('path')}"))

            # 5. Navigator on the filled form: one click, on Save and Continue
            r = await run_agent(navigator, nav.NAVIGATOR_MESSAGE)
            url = await page_eval(T, '() => location.pathname')
            rows.append(score(r, 'navigate form', url == '/done.html' and r['clicks_ok'] == 1,
                              f"url={url} report={r['report'] and (r['report'].action, r['report'].target)}"))

            for x in rows[-5:]:
                print(f"[{label} run {k+1}] {x['task']:<18} {'OK  ' if x['ok'] else 'FAIL'} {x['seconds']:5.1f}s "
                      f"llm={x['llm_calls']} hangs={x['hangs']} bad_targets={len(x['bad_targets'])}/{x['targets']} "
                      f"clicks={x['clicks_ok']} report={'yes' if x['report'] else 'NO'} {x['detail']} {x['error'] or ''}",
                      flush=True)

    # Summary
    print(f"\n===== {label}: {MODEL} (temperature {TEMP}), {runs} runs =====")
    tasks = list(dict.fromkeys(x['task'] for x in rows))
    for t in tasks:
        xs = [x for x in rows if x['task'] == t]
        tg = sum(x['targets'] for x in xs); bad = sum(len(x['bad_targets']) for x in xs)
        print(f"{t:<18} pass {sum(x['ok'] for x in xs)}/{len(xs)} | goal met {sum(x['goal'] for x in xs)}/{len(xs)} "
              f"| report {sum(1 for x in xs if x['report'])}/{len(xs)} | valid targets {tg - bad}/{tg} "
              f"| hangs {sum(x['hangs'] for x in xs)} | median {sorted(x['seconds'] for x in xs)[len(xs)//2]:.1f}s")
    print(f"{'TOTAL':<18} pass {sum(x['ok'] for x in rows)}/{len(rows)} | {sum(x['seconds'] for x in rows):.0f}s")
    bad = [t for x in rows for t in x['bad_targets']]
    if bad: print('bad targets seen:', sorted(set(map(str, bad)))[:8])

    OUT.mkdir(exist_ok=True)
    (OUT / f'result_{label}.json').write_text(json.dumps(
        {'model': MODEL, 'temperature': TEMP, 'runs': runs, 'date': time.strftime('%Y-%m-%d'),
         'rows': [{k: (v if k != 'report' else v is not None) for k, v in x.items()} for x in rows]},
        indent=1, default=str))


#%% RUN

if __name__ == '__main__':
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5, sys.argv[2] if len(sys.argv) > 2 else 'model'))


#%%
