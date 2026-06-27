"""remind-after-dev.py — PostToolUse Hook

Dev Agent 完成后提醒主智能体启动 Tester Agent (Step ⑤)。
仅提醒，不阻断 — 始终 exit 0。
"""

import sys
import os

try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass  # inline exec() context

from hook_util import start_watchdog, parse_tool_input, log_hook_event, extract_task_id, log_and_output

# Dev Agent 关键词
DEV_KEYWORDS = ['dev', 'developer', 'dev-agent', 'dev_agent', 'devagent']


def is_dev_agent_completion(tool_input):
    """判断刚完成的工具调用是否为 Dev Agent。"""
    subagent_type = (tool_input.get('subagent_type') or '').lower()
    description = (tool_input.get('description') or '').lower()

    for kw in DEV_KEYWORDS:
        if kw in subagent_type:
            return True

    if description.startswith('dev:'):
        return True

    return False


def main():
    start_watchdog(timeout=5.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    if not is_dev_agent_completion(tool_input):
        sys.exit(0)

    description = tool_input.get('description', 'Task')
    task_id = extract_task_id(description)
    log_and_output('remind-after-dev.py', 'dev_completed', {
        'description': description,
    }, decision='REMIND')
    sys.exit(0)


if __name__ == '__main__':
    main()
