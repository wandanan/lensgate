"""check-lifecycle-phase.py — PreToolUse Hook

检测 LIFECYCLE.md 中的可疑阶段跳跃（如 Phase 0 → Phase 2）。
仅警告，不阻断 — 始终 exit 0。
"""

import sys
import os
import re
import subprocess

try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass  # inline exec() context

from hook_util import start_watchdog, parse_tool_input, find_project_root, log_hook_event, log_and_output


def read_current_lifecycle(file_path):
    """读取 LIFECYCLE.md 当前内容中的阶段信息。"""
    if not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception:
        return None

    m = re.search(r'当前阶段[：:]\s*Phase\s+(\d+)', content)
    current_phase = int(m.group(1)) if m else None

    m = re.search(r'状态[：:]\s*(.+?)$', content, re.MULTILINE)
    current_status = m.group(1).strip() if m else None

    return {'phase': current_phase, 'status': current_status, 'content': content}


def detect_phase_in_new_content(old_phase, new_content):
    """检测新内容中目标阶段是否发生了跳跃。"""
    m = re.search(r'当前阶段[：:]\s*Phase\s+(\d+)', new_content)
    if not m:
        return None
    new_phase = int(m.group(1))
    if old_phase is not None and new_phase > old_phase + 1:
        return {'from': old_phase, 'to': new_phase}
    return None


def check_expected_outputs(domain, phase):
    """检查指定阶段的预期产出是否存在。"""
    root = find_project_root()
    checking = []
    if phase >= 1:
        prd = os.path.join(root, 'life_cycle_process', domain, 'phase-0-prd')
        if not os.path.isdir(prd):
            checking.append('phase-0-prd/ 目录不存在')
    if phase >= 2:
        docs = os.path.join(root, 'life_cycle_process', domain, 'phase-1-design', 'docs')
        if not os.path.isdir(docs):
            checking.append('phase-1-design/docs/ 目录不存在')
        interaction_spec = os.path.join(
            root, 'life_cycle_process', domain,
            'phase-1-design', 'system-interaction-spec.md'
        )
        if not os.path.isfile(interaction_spec):
            checking.append(
                'phase-1-design/system-interaction-spec.md 缺失 — '
                'Phase 2 开发需要系统交互规格作为行为基线（定义拖拽、快捷键、状态机、持久化等系统行为）。'
                '没有此文件，开发只会复刻原型视觉表面，产出的系统"看着像但用不了"。'
                '补救: 编排者读 PRD + 原型 + 设计文档，按 SKILL.md §6.3 引导性问题推导系统交互规格。'
            )
    if phase >= 3:
        phase2 = os.path.join(root, 'life_cycle_process', domain, 'phase-2-development')
        if not os.path.isdir(phase2):
            checking.append('phase-2-development/ 目录不存在')
    return checking


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    file_path = tool_input.get('file_path', '')
    if not file_path:
        sys.exit(0)

    if 'LIFECYCLE.md' not in file_path:
        sys.exit(0)

    # 读取当前状态
    current = read_current_lifecycle(file_path)
    if current is None or current['phase'] is None:
        sys.exit(0)

    # 检查新内容
    new_content = tool_input.get('new_string', '') or tool_input.get('content', '')
    if not new_content:
        sys.exit(0)

    skip = detect_phase_in_new_content(current['phase'], new_content)
    if skip is None:
        sys.exit(0)

    # 从路径提取 domain
    parts = file_path.replace('\\', '/').split('/')
    domain = None
    for i, p in enumerate(parts):
        if p == 'life_cycle_process' and i + 1 < len(parts):
            domain = parts[i + 1]
            break
    if domain is None:
        domain = '(当前域)'

    missing = check_expected_outputs(domain, skip['to'])

    log_and_output('check-lifecycle-phase.py', 'phase_jump', {
        'domain': domain,
        'from_phase': skip['from'],
        'to_phase': skip['to'],
        'missing_section': missing,
    }, decision='WARN')
    sys.exit(0)


if __name__ == '__main__':
    main()
