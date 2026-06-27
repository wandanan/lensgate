"""validate-int-coverage.py — PostToolUse Hook

检查 INT/E2E 用例覆盖度 — 逐用例 ID 匹配验证。

三层检测：
  1. 报告中是否有「INT 用例覆盖映射」表（Step 2.5 产出）
  2. 每个 TC-*-INT-NNN / TC-*-E2E-NNN ID 是否在映射表中有对应行
  3. 每个 ID 的状态是否为 ✅（不是 ❌）

覆盖 < 100% → BLOCK。

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
    find_domains, log_hook_event, log_and_output
)


def is_test_report_path(file_path):
    return 'test-reports/' in file_path and file_path.endswith('.md')


def extract_task_id(file_path):
    basename = os.path.basename(file_path)
    return os.path.splitext(basename)[0]


def find_test_cases_path(task_id):
    """查找 test-cases/{task_id}.md"""
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')
    for d in find_domains():
        for sub in ['phase-2-development', 'phase-3-iteration']:
            tc = os.path.join(lcp, d, sub, 'test-cases', f'{task_id}.md')
            if os.path.isfile(tc):
                return tc
    return None


def extract_int_e2e_ids(tc_content):
    """从 test-cases 文件中提取所有 INT/E2E 用例 ID。

    匹配模式: TC-{id}-INT-{NNN}, TC-{id}-E2E-{NNN}
    返回: set of str (去重后的 ID 集合)
    """
    if not tc_content:
        return set()
    ids = re.findall(r'TC-?[\w-]+-(?:INT|E2E)-\d+', tc_content)
    return set(ids)


REFERENCE_PATTERNS = [
    '建议', '推荐', '推荐使用', 'CI 环境', 'CI 中',
    'planned', 'deferred', 'TODO', '待补充', '待执行', '待验证',
    '后续', '可以考虑', '考虑使用',
]


def parse_mapping_table(report_content):
    """从报告中解析 INT 用例覆盖映射表。

    查找 '## INT 用例覆盖映射' 章节下的表格，
    提取每个用例 ID 及其状态（✅/❌）。

    返回: dict {case_id: passed_bool}
    """
    if not report_content:
        return {}

    # 查找映射表章节
    header_match = re.search(
        r'##\s*INT\s*用例覆盖映射',
        report_content, re.IGNORECASE
    )
    if not header_match:
        return {}

    # 提取该章节内容（到下一个 ## 或文件结束）
    start = header_match.end()
    next_section = re.search(r'\n##\s', report_content[start:])
    if next_section:
        section = report_content[start:start + next_section.start()]
    else:
        section = report_content[start:]

    # 解析表格行 — 格式: | TC-xx-INT-001 | 描述 | 步骤 | ✅/❌ |
    results = {}
    for line in section.split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.split('|')]
        cells = [c for c in cells if c]  # 去空

        # 查找包含 TC- 的单元格
        case_id = None
        passed = False
        for cell in cells:
            # 匹配用例 ID
            id_match = re.search(r'TC-?[\w-]+-(?:INT|E2E)-\d+', cell)
            if id_match:
                case_id = id_match.group(0)
            # 检查状态
            if '✅' in cell or 'PASS' in cell.upper():
                passed = True

        if case_id:
            results[case_id] = passed

    return results


def count_actual_clicks(report_content):
    """统计 test-report 中实际点击验证的元素数（回退方案）。

    仅在报告没有映射表时使用。
    返回: int
    """
    if not report_content:
        return 0

    def _is_ref(line):
        lower = line.lower()
        return any(p.lower() in lower for p in REFERENCE_PATTERNS)

    click_count = 0
    for line in report_content.split('\n'):
        stripped = line.strip().lower()
        if _is_ref(stripped):
            continue
        if '"action": "click"' in stripped or "'action': 'click'" in stripped:
            click_count += 1
        elif '点击' in stripped and ('✅' in stripped or 'pass' in stripped or '→' in stripped):
            click_count += 1
        elif 'page.click' in stripped:
            click_count += 1
        elif 'page.locator' in stripped and '.click(' in stripped:
            click_count += 1
        elif re.match(r'.*\bclick\b.*\b(assert|verify|check|pass)\b', stripped):
            click_count += 1

    return click_count


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

    # 查找对应的 test-cases 文件
    tc_path = find_test_cases_path(task_id)
    if tc_path is None:
        log_hook_event('validate-int-coverage.py', 'SKIP', {
            'file': file_path,
            'task_id': task_id,
            'reason': 'no_test_cases_file',
        })
        sys.exit(0)

    try:
        with open(tc_path, 'r', encoding='utf-8', errors='replace') as f:
            tc_content = f.read()
    except Exception:
        sys.exit(0)

    required_ids = extract_int_e2e_ids(tc_content)
    if not required_ids:
        log_hook_event('validate-int-coverage.py', 'SKIP', {
            'file': file_path,
            'task_id': task_id,
            'reason': 'no_int_e2e_test_cases',
        })
        sys.exit(0)

    # === 层级 1: 解析映射表（精确匹配） ===
    mapping = parse_mapping_table(content)

    if mapping:
        # 映射表存在 — 逐 ID 验证
        covered_pass = {cid for cid, passed in mapping.items() if passed}
        covered_fail = {cid for cid, passed in mapping.items() if not passed}

        # 检查每个 required ID 是否在映射表中
        missing = required_ids - covered_pass - covered_fail
        failed = required_ids & covered_fail
        passed = required_ids & covered_pass

        uncovered = missing | failed
        coverage = len(passed) / len(required_ids) if required_ids else 1.0

        if uncovered:
            log_and_output('validate-int-coverage.py', 'int_coverage_insufficient', {
                'file_path': file_path,
                'tc_path': tc_path,
                'required_count': len(required_ids),
                'passed_count': len(passed),
                'failed_count': len(failed),
                'missing_count': len(missing),
                'coverage': f'{coverage:.0%}',
                'failed_ids': sorted(failed),
                'missing_ids': sorted(missing),
            }, decision='BLOCK')
            sys.exit(1)

        log_hook_event('validate-int-coverage.py', 'PASS', {
            'file': file_path,
            'task_id': task_id,
            'required_count': len(required_ids),
            'passed_count': len(passed),
            'coverage': '100%',
            'method': 'mapping_table',
        })
        sys.exit(0)

    # === 层级 2: 回退到点击计数（旧逻辑兼容） ===
    actual_clicks = count_actual_clicks(content)
    threshold = max(1, int(len(required_ids) * 0.8))

    if actual_clicks < threshold:
        log_and_output('validate-int-coverage.py', 'int_coverage_insufficient', {
            'file_path': file_path,
            'tc_path': tc_path,
            'required_count': len(required_ids),
            'actual_clicks': actual_clicks,
            'threshold': threshold,
            'note': 'report_missing_mapping_table',
        }, decision='BLOCK')
        sys.exit(1)

    log_hook_event('validate-int-coverage.py', 'PASS', {
        'file': file_path,
        'task_id': task_id,
        'required_count': len(required_ids),
        'actual_clicks': actual_clicks,
        'method': 'click_count_fallback',
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
