"""validate-test-case.py — PreToolUse Hook

阻断格式不规范的测试用例写入。这是感知层的「输入门禁」——
确保每个 test-cases/{task-id}.md 在写入时就符合规范。

触发: Write|Edit 到 test-cases/*.md
检查:
  1. 必须包含「## 验证方式要求」章节 — 缺失 → BLOCK
  2. 所有 TC-* ID 必须遵循 TC-{task}-{VTYPE}-{NNN} 命名规范 — 违规 → BLOCK
  3. TC ID 中的 VTYPE 必须与验证方式要求一致 — 不一致 → BLOCK

验证类型代号 (VTYPE):
  BLD — 编译检查 (tsc + vite build)
  E2E — 浏览器交互测试 (Playwright)
  VIS — 视觉验证 (screenshot + visual_test.py)
  API — API 运行时测试 (HTTP 请求/响应)
  LOG — 逻辑/单元测试 (纯代码验证)

设计原则: 任何异常/超时一律 exit 0（宁可漏过不可误杀）
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

# ── 合法验证类型代号 ──
VALID_VTYPES = {'BLD', 'E2E', 'VIS', 'API', 'LOG'}

VTYPE_LABELS = {
    'BLD': '编译检查 (tsc + vite build)',
    'E2E': '浏览器交互测试 (Playwright)',
    'VIS': '视觉验证 (screenshot + visual_test.py)',
    'API': 'API 运行时测试 (HTTP 请求/响应)',
    'LOG': '逻辑/单元测试 (纯代码)',
}

# VTYPE 对应的验证方式要求键名
VTYPE_TO_REQUIREMENT = {
    'BLD': '编译检查',
    'E2E': '浏览器交互测试',
    'VIS': '视觉验证',
    'API': 'API运行时测试',
    'LOG': None,  # 逻辑测试无特定验证方式要求
}

# TC ID 正则: TC-{task}-{VTYPE}-{NNN}
# task 部分: 字母数字加连字符 (如 B01, B01-wiki-layout)
# VTYPE: BLD|E2E|VIS|API|LOG
# NNN: 3 位数字
TC_ID_PATTERN = re.compile(
    r'TC-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-(BLD|E2E|VIS|API|LOG)-(\d{3})'
)


def is_test_case_path(file_path):
    return 'test-cases/' in file_path and file_path.endswith('.md')


def parse_verification_section(content):
    """解析「验证方式要求」章节。返回同 validate-test-methodology.py 的格式。"""
    result = {'has_section': False, 'requirements': {}}

    section_match = re.search(
        r'##\s*验证方式要求.*?\n(.*?)(?=\n##\s|\Z)',
        content, re.DOTALL
    )
    if not section_match:
        return result

    result['has_section'] = True
    section = section_match.group(1)

    method_map = {
        '编译检查': '编译检查',
        '浏览器交互测试': '浏览器交互测试',
        '浏览器交互测试 (E2E)': '浏览器交互测试',
        '交互测试': '浏览器交互测试',
        'E2E 测试': '浏览器交互测试',
        '视觉验证': '视觉验证',
        '视觉回归测试': '视觉验证',
        'API运行时测试': 'API运行时测试',
        'API 运行时测试': 'API运行时测试',
        '接口测试': 'API运行时测试',
    }

    for line in section.split('\n'):
        m = re.match(r'\|\s*([^|]+?)\s*\|\s*([✅⚠️⬜][^|]*?)\s*\|', line)
        if not m:
            continue

        method_raw = m.group(1).strip()
        level_raw = m.group(2).strip()

        method = None
        for key, val in method_map.items():
            if key in method_raw:
                method = val
                break
        if not method:
            continue

        if '必需' in level_raw:
            level = '必需'
        elif '推荐' in level_raw:
            level = '推荐'
        elif '不需要' in level_raw:
            level = '不需要'
        else:
            level = '未声明'

        result['requirements'][method] = level

    return result


def extract_tc_ids(content):
    """提取所有 TC ID。返回 [(full_id, task_part, vtype, number), ...]"""
    ids = []
    for m in re.finditer(r'TC-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-(BLD|E2E|VIS|API|LOG)-(\d{3})', content):
        ids.append((m.group(0), m.group(1), m.group(2), m.group(3)))
    return ids


def find_legacy_tc_ids(content):
    """查找旧格式的 TC ID (TC-{task}-{NNN} 无 VTYPE)。"""
    # 匹配 TC-XXX-NNN 但不是 TC-XXX-VTYPE-NNN 的
    legacy = []
    # 先找所有 TC- 开头的
    for m in re.finditer(r'TC-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-(\d{3,4})\b', content):
        full = m.group(0)
        # 确认它不是新格式 (前面没有 VTYPE)
        # 新格式: TC-task-VTYPE-NNN, 旧格式: TC-task-NNN
        # 检查这个匹配后面没有另一个 -VTYPE-NNN
        task_part = m.group(1)
        num_part = m.group(2)
        # 如果前面有 VTYPE 关键词则跳过
        if re.match(r'TC-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-(BLD|E2E|VIS|API|LOG)-\d{3}', full):
            continue
        legacy.append(full)
    return legacy


def main():
    start_watchdog(timeout=8.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    file_path = tool_input.get('file_path', '')
    if not file_path:
        sys.exit(0)

    if not is_test_case_path(file_path):
        sys.exit(0)

    content = tool_input.get('new_string', '') or tool_input.get('content', '')
    if not content:
        sys.exit(0)

    # ── 检查 1: 必须有验证方式要求章节 ──
    req = parse_verification_section(content)
    if not req['has_section']:
        log_and_output('validate-test-case.py', 'missing_verification_section', {
            'file_path': file_path,
        }, decision='BLOCK')
        sys.exit(1)

    # ── 检查 2: 不能有旧格式的 TC ID ──
    legacy_ids = find_legacy_tc_ids(content)
    if legacy_ids:
        log_and_output('validate-test-case.py', 'legacy_tc_id_format', {
            'file_path': file_path,
            'legacy_ids': legacy_ids[:10],
        }, decision='BLOCK')
        sys.exit(1)

    # ── 检查 3: VTYPE 与验证方式要求的一致性 ──
    tc_ids = extract_tc_ids(content)
    if tc_ids:
        inconsistencies = []
        used_vtypes = set(vtype for _, _, vtype, _ in tc_ids)

        for vtype in used_vtypes:
            req_key = VTYPE_TO_REQUIREMENT.get(vtype)
            if req_key is None:
                continue  # LOG 类型不检查
            req_level = req['requirements'].get(req_key, '未声明')
            if req_level in ('不需要', '未声明'):
                inconsistencies.append({
                    'vtype': vtype,
                    'label': VTYPE_LABELS.get(vtype, vtype),
                    'method': req_key,
                    'actual_level': req_level,
                })

        if inconsistencies:
            formatted = [
                f'使用了 {inc["vtype"]} ({inc["label"]})，'
                f'但「{inc["method"]}」标注为「{inc["actual_level"]}」'
                for inc in inconsistencies
            ]
            log_and_output('validate-test-case.py', 'vtype_requirement_mismatch', {
                'file_path': file_path,
                'inconsistencies': formatted,
            }, decision='BLOCK')
            sys.exit(1)

    # ── 全部通过 ──
    log_hook_event('validate-test-case.py', 'PASS', {
        'file': file_path,
        'has_section': True,
        'tc_count': len(tc_ids),
        'vtypes_used': list(set(v for _, _, v, _ in tc_ids)),
    })
    sys.exit(0)


if __name__ == '__main__':
    main()
