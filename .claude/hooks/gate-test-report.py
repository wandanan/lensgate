"""gate-test-report.py — PreToolUse Hook

阻断无测试报告即标记任务完成的行为。
当 Edit/Write 目标为 PROJECT_STATUS.md 时，检测状态变更为 ✅/⚠️ 的任务行，
验证对应 test-reports/{task-id}.md 存在且包含 "判定：PASS"。

这是解决"跳过 Tester Agent"失败模式的核心 Hook。
"""

import sys
import os
import re

# 将脚本目录加入路径以导入 hook_util
try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass  # inline exec() context

from hook_util import (
    start_watchdog, parse_tool_input, find_project_root,
    find_test_report, log_hook_event, log_and_output
)


def scan_for_completed_tasks(content):
    """扫描内容中标记为完成 (✅/⚠️) 的任务行，返回 [(task_id, status), ...]"""
    tasks = []
    for line in content.split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        # 匹配表格行: | number | task-id | title | status | ...
        # task-id 在第二列
        m = re.match(
            r'\|\s*\d+\s*\|\s*([^\s|]+)\s*\|.*([✅⚠️])',
            line
        )
        if m:
            task_id = m.group(1).strip()
            status = m.group(2).strip()
            if task_id:
                tasks.append((task_id, status))
    return tasks


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    file_path = tool_input.get('file_path', '')
    if not file_path:
        sys.exit(0)

    # 仅检查 PROJECT_STATUS.md
    if 'PROJECT_STATUS.md' not in file_path:
        sys.exit(0)

    # 扫描 new_string（Edit 操作）或 content（Write 操作）
    content = tool_input.get('new_string', '') or tool_input.get('content', '')
    if not content:
        sys.exit(0)

    completed = scan_for_completed_tasks(content)
    if not completed:
        sys.exit(0)

    root = find_project_root()
    missing = []
    for task_id, status in completed:
        report_path, has_pass = find_test_report(task_id)
        if report_path is None:
            missing.append((task_id, status, 'NOT_FOUND'))
        elif not has_pass:
            missing.append((task_id, status, 'NO_PASS'))

    if missing:
        formatted_tasks = []
        for task_id, status, reason in missing:
            desc = '报告不存在' if reason == 'NOT_FOUND' else '报告无 PASS 判定'
            formatted_tasks.append(f'{task_id} ({status}) → {desc}')

        log_and_output('gate-test-report.py', 'missing_test_report', {
            'missing_tasks': formatted_tasks,
        }, decision='BLOCK')
        sys.exit(1)

    log_hook_event('gate-test-report.py', 'PASS', {
        'file': file_path,
        'tasks_checked': len(completed),
        'all_have_reports': True,
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
