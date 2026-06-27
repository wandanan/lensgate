"""validate-v-task-exists.py — PreToolUse Hook

当 dev-plan 引用 asset-spec 时，检查是否包含 V 任务。
缺失 V 任务 → BLOCK。

迁移自 SKILL.md 第 11 节"V 任务是一等公民"规则。

触发: Write/Edit 到 dev-plan.md
"""

import sys
import os
import re

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:
    pass

from hook_util import (
    start_watchdog, parse_tool_input, find_project_root,
    find_domains, log_hook_event, log_and_output
)


def is_dev_plan_path(file_path):
    return file_path.endswith('dev-plan.md')


def check_asset_spec_reference(content):
    """检查 dev-plan 是否引用了 asset-spec。

    匹配模式:
    - asset-spec/
    - asset_spec
    - 美术资产
    - V01, V02, ... 任务 ID

    返回: (references_asset_spec: bool, has_v_tasks: bool)
    """
    has_asset_ref = False
    has_v_task = False

    for line in content.split('\n'):
        lower = line.lower()

        # 检查 asset-spec 引用
        if 'asset-spec' in lower or 'asset_spec' in lower:
            has_asset_ref = True
        if '美术资产' in line or '视觉资产' in line or 'art asset' in lower:
            has_asset_ref = True

        # 检查 V 任务
        if re.search(r'\bV\d+\b', line):
            has_v_task = True
        if re.search(r'\bV\s*任务', line):
            has_v_task = True

    return has_asset_ref, has_v_task


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    file_path = tool_input.get('file_path', '')
    if not file_path:
        sys.exit(0)

    if not is_dev_plan_path(file_path):
        sys.exit(0)

    content = tool_input.get('new_string', '') or tool_input.get('content', '')
    if not content:
        sys.exit(0)

    has_asset_ref, has_v_task = check_asset_spec_reference(content)

    if has_asset_ref and not has_v_task:
        log_and_output('validate-v-task-exists.py', 'asset_spec_without_v_tasks', {
            'file_path': file_path,
        }, decision='BLOCK')
        sys.exit(1)

    log_hook_event('validate-v-task-exists.py', 'PASS', {
        'file': file_path,
        'has_asset_ref': has_asset_ref,
        'has_v_tasks': has_v_task,
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
