"""validate-console-errors.py — PostToolUse Hook

阻断包含 console 错误的测试报告。
当 Write/Edit 目标为 test-reports/*.md 时，检查 console error 计数。
console.error > 0 → BLOCK。

迁移自 SKILL.md 第 11 节"控制台零报错"规则。

触发: Write/Edit 到 test-reports/*.md
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
    log_hook_event, log_and_output
)


def is_test_report_path(file_path):
    return 'test-reports/' in file_path and file_path.endswith('.md')


def extract_task_id(file_path):
    basename = os.path.basename(file_path)
    return os.path.splitext(basename)[0]


def count_console_errors(content):
    """扫描测试报告中的 console 错误。

    检测模式:
    - [error] ... (console.error 输出)
    - Console 错误（如有）章节下的内容
    - Uncaught TypeError / ReferenceError / SyntaxError
    - console.error 计数: N

    返回: (error_count: int, examples: list[str])
    """
    errors = []
    in_console_section = False

    for line in content.split('\n'):
        stripped = line.strip()

        # 检测 Console 错误章节标题
        if re.match(r'#+\s*Console\s*错误', stripped) or \
           re.match(r'#+\s*Console\s*Errors', stripped) or \
           re.match(r'#+\s*控制台错误', stripped):
            in_console_section = True
            continue

        # 遇到下一个章节标题，退出 Console 章节
        if in_console_section and re.match(r'#+\s+', stripped):
            in_console_section = False

        # 在 Console 章节内的非空行都是错误
        if in_console_section and stripped and not stripped.startswith('|'):
            # 排除表格分隔行
            if not re.match(r'^[\s|:-]+$', stripped):
                errors.append(stripped[:120])

        # 全局匹配 [error] 前缀
        if re.match(r'\[error\]', stripped, re.IGNORECASE):
            errors.append(stripped[:120])

        # 匹配未捕获异常
        if 'Uncaught TypeError' in line or 'Uncaught ReferenceError' in line or \
           'Uncaught SyntaxError' in line:
            errors.append(stripped[:120])

        # 匹配显式 console.error 计数
        m = re.search(r'console\.error.*?(\d+)', line)
        if m and int(m.group(1)) > 0:
            errors.append(f"console.error count: {m.group(1)}")

    return len(errors), errors[:10]  # 最多返回 10 条示例


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    file_path = tool_input.get('file_path', '')
    if not file_path:
        sys.exit(0)

    if not is_test_report_path(file_path):
        sys.exit(0)

    content = tool_input.get('new_string', '') or tool_input.get('content', '')
    if not content:
        sys.exit(0)

    task_id = extract_task_id(file_path)
    error_count, examples = count_console_errors(content)

    if error_count > 0:
        log_and_output('validate-console-errors.py', 'console_errors_detected', {
            'file_path': file_path,
            'error_count': error_count,
            'examples': examples[:5],
        }, decision='BLOCK')
        sys.exit(1)

    log_hook_event('validate-console-errors.py', 'PASS', {
        'file': file_path,
        'task_id': task_id,
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
