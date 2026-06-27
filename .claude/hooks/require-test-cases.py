"""require-test-cases.py — PreToolUse Hook

阻止在无测试用例的情况下启动 Dev Agent。
当 Agent 调用的 subagent_type 包含 "dev" 时，
提取 task ID 并验证 test-cases/{task-id}.md 存在。
"""

import sys
import os

try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass  # inline exec() context

from hook_util import (
    start_watchdog, parse_tool_input, find_test_cases,
    extract_task_id, log_hook_event, log_and_output
)

# 被视为 "Dev Agent" 的 subagent_type 关键词（不区分大小写）
DEV_KEYWORDS = ['dev', 'developer', 'dev-agent', 'dev_agent', 'devagent']


def is_dev_agent(tool_input):
    """判断是否为 Dev Agent 调用。"""
    subagent_type = (tool_input.get('subagent_type') or '').lower()
    description = (tool_input.get('description') or '')

    # 检查 subagent_type
    for kw in DEV_KEYWORDS:
        if kw in subagent_type:
            return True

    # 检查 description 是否以 "Dev:" 开头（不区分大小写）
    if description.lower().startswith('dev:'):
        return True

    return False


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    # 仅拦截 Dev Agent
    if not is_dev_agent(tool_input):
        sys.exit(0)

    description = tool_input.get('description', '')
    prompt = tool_input.get('prompt', '')
    task_id = extract_task_id(description, prompt)

    # 无法提取 task ID → 放行（避免误报）
    if task_id is None:
        sys.exit(0)

    # 搜索 test-cases
    tc_path = find_test_cases(task_id)
    if tc_path is None:
        log_and_output('require-test-cases.py', 'no_test_cases_file', {
            'task_id': task_id,
        }, decision='BLOCK')
        sys.exit(1)

    log_hook_event('require-test-cases.py', 'PASS', {
        'task_id': task_id,
        'tc_path': tc_path,
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
