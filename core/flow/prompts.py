FLOW_PRIMARY_ADDENDUM = """## Flow Runtime

You are inside a Flow. The controller owns whether work continues.

- In planning, call `submit_subtask_list` with 1-15 concrete subtasks. Do not narrate a plan without the tool.
- During a subtask, use `terminal` and `web_search`. Command output arrives in the resumption excerpt.
- End a subtask with `done(summary)` or pause with `ask(question)`.
- After a subtask, you may `patch_subtask_list` for remaining planned items only.
- When reporting, call `export_report` with the final Markdown.
- Do not wait for the operator unless you call `ask`.
"""

FLOW_SPECIALIST_ADDENDUM = """## Flow Runtime

You are a specialist inside a Flow subtask. Use `terminal` and `web_search` as needed. Do not call Flow plan tools.
"""

PLANNING_PROMPT = """Create the Flow plan now.

Call `submit_subtask_list` with ordered subtasks that complete the goal inside the authorized scope. Each item needs a title and a brief description. Maximum 15 items. Do not start testing until the plan tool succeeds.
"""

SUBTASK_PROMPT = """Execute the current Flow subtask until it is done.

Current subtask:
{title}

{description}

Remaining planned subtasks:
{remaining}

Use `terminal` and `web_search`. Call `done` with a concise evidence-backed summary when this subtask is complete, or `ask` if you must stop for the operator.
"""

REFINE_PROMPT = """The previous subtask is complete.

Summary:
{summary}

Call `patch_subtask_list` if remaining planned work must change. If the remaining plan is still correct, call `patch_subtask_list` with an empty ops list.
"""

REPORT_PROMPT = """The Flow subtasks are complete. Call `export_report` with a Markdown report that includes the original goal, what was tested, command-backed evidence, findings or useful negatives, and residual gaps.
"""
