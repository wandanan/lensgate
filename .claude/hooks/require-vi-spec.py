"""require-vi-spec.py — PreToolUse Hook

阻止在无 VI 规范的情况下启动 Prototype Designer Agent。
当 Agent 调用的 subagent_type 包含 "prototype" 时，
验证 life_cycle_process/{domain}/phase-1-design/vi-spec.html 存在。

VI 规范是产品级视觉决策，必须由编排者在启动原型设计师之前完成。
VI 规范是自包含 HTML 文件，既是规格文档也是可视化预览。
"""

import sys
import os

try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass

from hook_util import (
    start_watchdog, parse_tool_input, find_project_root,
    find_domains, log_hook_event, log_and_output
)

# 被视为 "Prototype Designer Agent" 的 subagent_type 关键词（不区分大小写）
PROTOTYPE_KEYWORDS = ['prototype', 'prototype-designer', 'prototype_designer', 'prototypedesigner']


def is_prototype_agent(tool_input):
    """判断是否为 Prototype Designer Agent 调用。"""
    subagent_type = (tool_input.get('subagent_type') or '').lower()
    description = (tool_input.get('description') or '').lower()

    for kw in PROTOTYPE_KEYWORDS:
        if kw in subagent_type:
            return True

    if 'prototype' in description and ('design' in description or '原型' in description):
        return True

    return False


def find_vi_spec():
    """查找 VI 规范文件。返回 path 或 None。"""
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')
    if not os.path.isdir(lcp):
        return None

    for domain in find_domains():
        vi_path = os.path.join(lcp, domain, 'phase-1-design', 'vi-spec.html')
        if os.path.isfile(vi_path):
            return vi_path

    return None


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    if not is_prototype_agent(tool_input):
        sys.exit(0)

    vi_path = find_vi_spec()
    if vi_path is None:
        log_and_output('require-vi-spec.py', 'no_vi_spec', {
            'subagent_type': tool_input.get('subagent_type', ''),
            'description': tool_input.get('description', ''),
        }, decision='BLOCK')
        sys.exit(1)

    log_hook_event('require-vi-spec.py', 'PASS', {
        'vi_spec_path': vi_path,
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
