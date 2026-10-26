"""
Created on 2026-08-30
@author: amacedo

Description
-----------
This script holds basic config information for the project.
"""

#%% IMPORTS

from dotenv import load_dotenv
load_dotenv()
import os

from langchain_openai import ChatOpenAI
from langchain.agents.middleware import wrap_tool_call, ContextEditingMiddleware, ClearToolUsesEdit
from langchain_core.messages import ToolMessage

from dataclasses import dataclass, replace
from pathlib import Path
import yaml


#%% CONFIG / VARIABLES

# llm: .env overrides the defaults ('or', because an empty LLM_BASE_URL= is '')
BASE_URL = os.getenv('LLM_BASE_URL') or "http://localhost:8080/v1"
MODEL = os.getenv('LLM_MODEL') or "Qwen3.8-27B-UD-Q5_K_M.gguf" # curl <BASE_URL>/models
API_KEY = os.getenv('LLM_API_KEY') or "local" # llama.cpp ignores it
TEMPERATURE = 0 # less variance for tasks

SKIP_LINKS = ['linkedin.com'] # avoid agents in linkedin (against its policy)

LLM_EXPLORE_NAME = 'llm_no_thinking' # check the llms below
LLM_NAVIGATE_NAME = 'llm_no_thinking'
LLM_FILL_NAME = 'llm_no_thinking'
LLM_ANSWER_NAME = 'llm_open_question' # there's thinking

TIMEOUT = 240 # max seconds per LLM request (running locally can take a while)
TAB_TIMEOUT = 25 * 60 # max seconds per tab
GRAPH_RECURSION_LIMIT = 40
MAX_REPEATS = 4 # identical calls allowed in sequence
REPEAT_EXEMPT = ['browser_snapshot', 'browser_wait_for']

@dataclass
class ClearToolUsesInSteps(ClearToolUsesEdit):
    """
    ClearToolUsesEdit, but the line between cleared and kept tool results
    moves 'step' results at a time instead of one per call. Between moves the
    prompt starts with the same tokens, so llama.cpp can reuse its cache.
    """
    step: int = 10

    def apply(self, messages, *, count_tokens):
        n = sum(isinstance(m, ToolMessage) for m in messages)
        n_clear = max(0, n - self.keep) // self.step * self.step
        ClearToolUsesEdit.apply(replace(self, keep=n - n_clear), messages, count_tokens=count_tokens)

# Past ~75k tokens, clear old tool results 10 at a time; always keep the newest 3
TRIM_TOOL_CONTEXT = ContextEditingMiddleware(
    edits=[ClearToolUsesInSteps(trigger=75_000, keep=3, step=10)]
)

BROWSER_URL = os.getenv('BROWSER_URL') or "http://localhost:9222" # browser (CDP)
CONFIG_DIR = Path(__file__).resolve().parent / 'config'

#%% LLMs

llm = ChatOpenAI(
    base_url=BASE_URL,
    model=MODEL,
    api_key=API_KEY,
    temperature=TEMPERATURE, 
    timeout=TIMEOUT,
    extra_body={
        'chat_template_kwargs': {'enable_thinking': True}
    }
)

llm_no_thinking = ChatOpenAI(
    base_url=BASE_URL,
    model=MODEL,
    api_key=API_KEY,
    temperature=TEMPERATURE, 
    timeout=TIMEOUT,
    extra_body={
        'chat_template_kwargs': {'enable_thinking': False}
    }
)

llm_open_question = ChatOpenAI(
    base_url=BASE_URL,
    model=MODEL,
    api_key=API_KEY,
    temperature=0.3, # a bit more variance/'creativity' 
    timeout=TIMEOUT,
    extra_body={
        'chat_template_kwargs': {'enable_thinking': True}
    }
)

# Resolve configured models
LLMS = {
    'llm': llm,
    'llm_no_thinking': llm_no_thinking,
    'llm_open_question': llm_open_question,
}
LLM_EXPLORE = LLMS[LLM_EXPLORE_NAME]
LLM_NAVIGATE = LLMS[LLM_NAVIGATE_NAME]
LLM_FILL = LLMS[LLM_FILL_NAME]
LLM_ANSWER = LLMS[LLM_ANSWER_NAME]


#%% MIDDLEWARE

# Log tool calls
@wrap_tool_call
async def log_tool_calls(request, handler):
    """
    Print every tool call.
    """
    call = request.tool_call
    print(f"    [middleware] calling {call['name']} with {call['args']}")

    result = await handler(request)

    return result


def same_action(a, b):
    """
    Same tool, same args.
    """
    strip = lambda args: {k: v for k, v in args.items() if k != 'element'}
    return a['name'] == b['name'] and strip(a['args']) == strip(b['args'])


@wrap_tool_call
async def block_repeats(request, handler):
    """
    Refuse a tool call already made MAX_REPEATS times in a row with the same 
    args.
    """
    call = request.tool_call
    if call['name'] in REPEAT_EXEMPT:
        return await handler(request)

    # Every action of this run, oldest first, up to and including this call
    actions = []
    for msg in request.state['messages']:
        for tc in getattr(msg, 'tool_calls', None) or []:
            if tc['name'] not in REPEAT_EXEMPT:
                actions.append(tc)
            if tc['id'] == call['id']:
                break

    # The streak
    streak = 0
    for tc in reversed(actions):
        if not same_action(tc, call):
            break
        streak += 1

    if streak > MAX_REPEATS:
        print(f"    [middleware] blocked {call['name']}: {streak} identical calls in a row")
        return ToolMessage(
            content=(
                f"BLOCKED: this is the same {call['name']} call {streak} times "
                "in a row and the page did not move on. Do not retry. Put this "
                "field in skipped_fields and continue with the next field."
            ),
            tool_call_id=call['id'],
            status='error',
        )

    return await handler(request)


@wrap_tool_call
async def safe_files(request, handler):
    """
    Keep the MCP's file access to what the task needs. Uploads: only the
    resumes listed in the profile. Snapshots and finds: no 'filename', so
    nothing is written into the repo folder.
    """
    call = request.tool_call
    args = call['args']

    if call['name'] == 'browser_file_upload':
        root = CONFIG_DIR.parent
        allowed = {str((root / p).resolve()) for p in PROFILE.get('resumes', {}).values()}
        paths = [str((root / p).resolve()) for p in args.get('paths') or []]
        if any(p not in allowed for p in paths):
            print(f"    [middleware] refused upload of {args.get('paths')}")
            return ToolMessage(
                content="BLOCKED: only the resume listed in the profile may be uploaded.",
                tool_call_id=call['id'],
                status='error',
            )

    if 'filename' in args:
        print(f"    [middleware] dropped filename={args['filename']!r} from {call['name']}")
        call = {**call, 'args': {k: v for k, v in args.items() if k != 'filename'}}
        request = request.override(tool_call=call)

    return await handler(request)


#%% LOAD THE PROFILE

def load_yaml(name):
    with open(CONFIG_DIR / name, encoding='utf-8') as f:
        return yaml.safe_load(f)

# Your profile; the fictional example until config/profile.yaml exists
PROFILE_FILE = os.getenv('PROFILE_FILE') or 'profile.yaml'
if not (CONFIG_DIR / PROFILE_FILE).exists():
    print(f"[settings] config/{PROFILE_FILE} not found -> using profile.example.yaml")
    PROFILE_FILE = 'profile.example.yaml'

PROFILE = load_yaml(PROFILE_FILE)

#%%
