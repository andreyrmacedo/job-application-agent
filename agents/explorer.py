"""
Created on 2026-09-01
@author: amacedo

Description
-----------
This script holds the agent 'Explorer'.
"""

#%% IMPORTS

from langchain.agents import create_agent
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

#%% CREATE AGENT

SYSTEM_PROMPT = """
You are the Explorer Agent inside a stateful \
multi-agent job application system.

You are part of a pipeline. Your job is to ANALYZE the current page \
and DECIDE what should happen next.
You do NOT complete tasks end-to-end. You produce structured decisions \
that will be applied to system state.

---

## YOUR ROLE

For the CURRENT ACTIVE TAB:

1. Observe the page using browser tools
2. Classify the type of page
3. Extract job-related information, when available \
('job_description', 'requirements', 'salary_min', 'salary_max', 'salary_currency')
4. Detect whether application inputs exist
5. Decide the NEXT STATE of the tab

---

## PAGE CLASSIFICATION (CRITICAL)

You must classify the page into ONE of the following:

- "job_description" → contains job details (title, company, requirements, etc.)
- "application_form" → contains inputs, questions, or upload fields
- "submit_page" → contains submit application button or option (final step/webpage)
- "login" → requires authentication (sign-in, email/password, SSO)
- "redirect" → intermediate navigation page (loading, forwarding, routing)
- "irrelevant" → unrelated to job applications
- "unknown" → cannot confidently determine

---

## JOB INFORMATION EXTRACTION

ONLY if the page is job-related, extract when available:

- job_title (exact job title as written on the page)
- company (hiring company name)
- job_description (summary of job description keeping important information)
- requirements (skills, experience, tools)
- salary_min (minimum salary of the advertised compensation range)
- salary_max (maximum salary of the advertised compensation range)
- salary_currency (ISO 4217 currency code, such as "CAD" or "USD")

If not available, return empty values.

Prioritize CAD when compensation is presented in multiple currencies, \
extract the CAD figures rather than the USD figures.

---

## FIELD DETECTION

Determine if the page contains ANY of:

- input fields (text, email, etc.)
- textareas
- dropdowns / selects
- file uploads (resume, cover letter)
- application questions
- multi-step application flows

---

## NAVIGATION RULES

- ALWAYS inspect the page BEFORE making a decision

---

## IMPORTANT BEHAVIOR RULES

- You do NOT fill forms
- You do NOT finalize applications
- You do NOT assume content without observing it
- You are a decision-maker, not an executor

---

## OUTPUT REQUIREMENTS

- Your response MUST strictly follow the provided structured schema
- Do NOT include explanations
- Do NOT include extra text

Focus on accurate classification and correct next-state decisions.
"""

# Message that kicks the agent off
EXPLORER_MESSAGE = """
Access the current webpage and execute your goals as EXPLORER Agent inside \
a stateful multi-agent job application system.
"""

#%% DEFINE A PYDANTIC SCHEMA

class ExplorerOutput(BaseModel):
    job_title: Optional[str] = Field(
        default=None,
        description=(
            "Exact job title as written on the page. "
            "Return null if not found or not applicable."
        )
    )
    company: Optional[str] = Field(
        default=None,
        description=(
            "Hiring company name. "
            "Return null if not found."
        )
    )
    job_description: Optional[str] = Field(
        default=None,
        description=(
            "Produce a lossless-lean summary of the job description: "
            "retain all non-trivial information while compressing phrasing. "
            "Return null if no job description content is present."
        )
    )
    requirements: List[str] = Field(
        default_factory=list, 
        description="List of explicit job requirements such as skills, experience, or tools."
    )
    salary_min: Optional[int] = Field(
        default=None,
        description="Low end of the annual pay range as shown in the posting. Prioritize CAD figures. Null if not shown."
    )
    salary_max: Optional[int] = Field(
        default=None,
        description="High end of the annual pay range as shown in the posting. Prioritize CAD figures. Null if not shown."
    )
    salary_currency: Optional[str] = Field(
        default=None,
        description="Currency code of the pay range, e.g. 'CAD' or 'USD'. Prioritize CAD figures. Null if not shown."
    )
    has_fields: bool = Field(
        description=(
            "True if ANY input elements for a job application exist (text inputs, "
            "dropdowns, uploads, questions, or forms)."
        )
    )
    requires_resume_upload: bool = Field(
        description="True ONLY if there is a file upload field specifically intended for resume/CV upload"
    )
    page_type: Literal[
        "job_description",
        "application_form",
        "submit_page",
        "login",
        "redirect",
        "irrelevant",
        "unknown",
    ]
    dialog: bool = Field(
        default=False,
        description=(
            "True if a modal/dialog/alertdialog is currently open in the page snapshot."
        )
    )

#%% TOOLS

EXPLORER_TOOLS = {
    "browser_snapshot",
    "browser_find",
    "browser_wait_for",
}

#%% CREATE AGENT

# Function to create agent
def create_explorer_agent(
    llm, 
    tools,
    middleware=(),
    system_prompt=SYSTEM_PROMPT,
    response_format=ExplorerOutput
):
    """
    Creates the Explorer Agent.

    The Explorer analyzes the current page, extracts job data, classifies \
    the page, and decides the next tab state.
    """
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        system_prompt=system_prompt,
        response_format=response_format
    )

#%%
