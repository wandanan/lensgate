"""require-opus-model.py — PreToolUse Hook

阻止对 dev/tester/planner 类 Agent 使用非 opus 模型。
P1 规则：深度推理任务（代码编写、测试用例生成、架构分析）必须使用 opus。
"""

import sys
import os

try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass  # inline exec() context

from hook_util import start_watchdog, parse_tool_input, log_hook_event, log_and_output

# 需要 opus 的 subagent_type 关键词（不区分大小写）
REQUIRE_OPUS_KEYWORDS = [
    'dev', 'developer', 'dev-agent', 'dev_agent', 'devagent',
    'test', 'tester',
    'planner', 'plan', 'planning',
    'prototype', 'designer',
]


def requires_opus(tool_input):
    """判断此 Agent 是否需要 opus 模型。"""
    subagent_type = (tool_input.get('subagent_type') or '').lower()
    description = (tool_input.get('description') or '').lower()

    for kw in REQUIRE_OPUS_KEYWORDS:
        if kw in subagent_type or kw in description:
            return True
    return False


def main():
    start_watchdog(timeout=5.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    if not requires_opus(tool_input):
        sys.exit(0)

    model = (tool_input.get('model') or '').lower()
    subagent_type = tool_input.get('subagent_type', 'unknown')

    # opus 是正确选择
    if model == 'opus':
        sys.exit(0)

    # model 未指定 — 默认继承父模型，不阻断但提醒
    if not model:
        log_and_output('require-opus-model.py', 'model_not_specified', {
            'subagent_type': subagent_type,
        }, decision='WARN')
        sys.exit(0)

    # 指定了非 opus 模型 → 阻断
    log_and_output('require-opus-model.py', 'non_opus_model', {
        'subagent_type': subagent_type,
        'model': model,
    }, decision='BLOCK')
    sys.exit(1)


if __name__ == '__main__':
    main()
