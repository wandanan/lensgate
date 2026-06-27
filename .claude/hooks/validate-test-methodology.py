"""validate-test-methodology.py — PostToolUse Hook v2

阻断「伪装成测试的静态代码审查」。

双源交叉验证:
  1. 读测试报告 (stdin)
  2. 读对应 test-cases/{task-id}.md 中的「验证方式要求」
  3. 比对抗 — 测试用例说「必需 E2E」但报告没有 Playwright → BLOCK
  4. 测试用例没有验证方式声明(legacy) → 回退到启发式检测

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

# ── 红牌关键词 ──
STATIC_DECLARATION_PATTERNS = [
    '静态代码审查', 'static code review',
    'static analysis only', 'static verification',
    '验证方式: 静态', '验证方式：静态',
]

# ── 各类验证方式的证据模式 ──
BUILD_EVIDENCE = [
    'npx tsc', 'tsc --noEmit', 'npm run build', 'vite build',
    'TypeScript.*No errors', 'built in', 'modules transformed',
]

E2E_EVIDENCE = [
    'playwright', 'Playwright',
    'page.goto', 'page.click', 'page.fill', 'page.locator',
    'page.screenshot', 'page.waitFor',
    'browser.new_page', 'browser.new_context',
    'screenshot', '截图',
    'e2e-scenarios.json',
    'run-e2e-interactions.py',
    'chromium', 'firefox', 'webkit',
    'expect(page',
    'npm run dev',
]

VISUAL_EVIDENCE = [
    'visual_test.py',
    'merge_images.py',
]

API_EVIDENCE = [
    'run-api-tests.py', 'api-scenarios.json',
    'HTTP/1.1', 'HTTP/2', 'status_code',
    'response.json', 'response.text',
    'curl -X', 'pytest', 'supertest',
    'assert response', 'assert status',
]

# ── 跳过关键词 — 匹配到证据但同时包含这些词的行不计为证据 ──
SKIP_KEYWORDS = [
    'SKIP', '跳过', '未执行', 'N/A', '未建立', '已跳过',
    '未完成',
    'baseline 未建立', '无 API Key', '未配置', '未安装',
    'Not applicable', 'not applicable',
    '建议安装', '需要安装', '请安装', '应安装',
    '未运行', '未启动', '未部署',
]

# ── 参考/建议类模式 — 与证据共存时取消该行的证据资格 ──
REFERENCE_PATTERNS = [
    '建议', '推荐', '推荐使用', '应当', '应该',
    'CI 环境', 'CI 中', 'CI pipeline', 'CI/CD',
    'planned', 'deferred', 'TODO', 'FIXME',
    '待补充', '待执行', '待验证',
    'future', 'later', 'next time', '后续',
    '可以考虑', '可考虑', '考虑使用', '考虑引入',
]

# ── 不可伪造证据 — 只有实际执行后才会出现的输出格式 ──
UNFORGEABLE_E2E = [
    'E2E Results:', 'steps PASS', 'steps FAILED',
    '[PASS]', '[FAIL]', 'Scenario:',
]
UNFORGEABLE_API = [
    'API Runtime Tests', '[INFO] 服务就绪', '[INFO] 启动服务',
]
UNFORGEABLE_VIS = [
    'test-output/', 'test-reports/screenshots/',
]

# ── 文件系统验证路径模板 ──
ARTIFACT_PATHS = {
    '浏览器交互测试': ['test-reports/{task_id}-e2e-scenarios.json'],
    'API运行时测试': ['test-reports/{task_id}-api-scenarios.json'],
    '视觉验证': ['test-reports/screenshots/', 'test-output/'],
}

# ── 测试深度签名 — L1-L5 五级 ──
# Hook 用这些签名判断报告达到哪个深度，不依赖 LLM 自评

# L1: 页面可访问 — 只能证明 URL 能打开
L1_SIGNATURES = [
    'page.goto(', 'goto(',
    'page.title()',
    'wait_for_load_state', 'waitForLoadState',
]

# L2: UI 元素存在 — 只能证明 DOM 中有元素
L2_SIGNATURES = [
    '.count(', 'count(',
    'assert_visible', 'assertVisible', 'toBeVisible',
    'wait_for_selector', 'waitForSelector',
    'is_visible', 'isVisible',
]

# L3: 交互可触发 — 用户操作后 UI 有响应
L3_SIGNATURES = [
    'page.click(', '.click(',
    'page.fill(', '.fill(',
    'page.type(', '.type(',
    'page.drag', '.drag(',
    'page.selectOption', '.selectOption(',
    'page.press(', '.press(',
    'page.hover(', '.hover(',
    '"action": "click"', "'action': 'click'",
    '"action": "fill"', "'action': 'fill'",
    '"action": "drag"', "'action': 'drag'",
    'page.keyboard.',
]

# L4: 数据流贯通 — 操作→持久化→刷新→验证
L4_SIGNATURES = [
    'page.reload(', '.reload(',
    'wait_for_response', 'waitForResponse',
    'wait_for_request', 'waitForRequest',
    'expect_response', 'expectResponse',
    'page.route(', 'page.route.',
    'page.waitForResponse',
    'page.waitForRequest',
    'page.on("request"', "page.on('request'",
    'request.url',
    'response.status',
    'response.body',
    'response.json',
    # 交互闭环: 操作后刷新验证持久化
    '刷新', 'refresh',
    '持久化', 'persist',
    '保留', 'remain', 'still',
]


def is_reference_line(line):
    """检查一行是否含引用/建议语言（非实际执行证据）。"""
    lower = line.lower()
    return any(p.lower() in lower for p in REFERENCE_PATTERNS)


def is_test_report_path(file_path):
    return 'test-reports/' in file_path and file_path.endswith('.md')


def extract_task_id_from_path(file_path):
    """从报告路径提取 task-id。e.g. test-reports/B01-wiki-layout.md → B01-wiki-layout"""
    basename = os.path.basename(file_path)
    return os.path.splitext(basename)[0]


def find_test_case(task_id):
    """查找 test-cases/{task_id}.md。返回 (path|None, content|None)"""
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')
    domains = find_domains()
    for d in domains:
        for sub in ['phase-2-development', 'phase-3-iteration']:
            tc_path = os.path.join(lcp, d, sub, 'test-cases', f'{task_id}.md')
            if os.path.isfile(tc_path):
                try:
                    with open(tc_path, 'r', encoding='utf-8', errors='replace') as f:
                        return tc_path, f.read()
                except Exception:
                    return tc_path, None
    return None, None


def parse_verification_requirements(tc_content):
    """解析测试用例中的「验证方式要求」表格。

    返回: {
        'has_section': bool,
        'requirements': {
            '编译检查': '必需'|'推荐'|'不需要',
            '浏览器交互测试': '必需'|'推荐'|'不需要',
            '视觉验证': '必需'|'推荐'|'不需要',
            'API运行时测试': '必需'|'推荐'|'不需要',
        }
    }
    未声明的方法默认 '未声明'。
    """
    result = {
        'has_section': False,
        'requirements': {},
    }

    if not tc_content:
        return result

    # 找 "## 验证方式要求" 章节
    section_match = re.search(
        r'##\s*验证方式要求.*?\n(.*?)(?=\n##\s|\Z)',
        tc_content, re.DOTALL
    )
    if not section_match:
        return result

    result['has_section'] = True
    section = section_match.group(1)

    # 解析表格行
    # | 编译检查 | ✅ 必需 | ... |
    # | 浏览器交互测试 | ⚠️ 推荐 | ... |
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
        # 匹配 | 方法名 | 等级 | ... |
        m = re.match(r'\|\s*([^|]+?)\s*\|\s*([✅⚠️⬜][^|]*?)\s*\|', line)
        if not m:
            continue

        method_raw = m.group(1).strip()
        level_raw = m.group(2).strip()

        # 映射方法名
        method = None
        for key, val in method_map.items():
            if key in method_raw:
                method = val
                break

        if not method:
            continue

        # 解析等级
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


def verify_artifacts(task_id, categories, project_root):
    """检查测试产物文件是否实际存在于文件系统。

    Returns: {category: (exists: bool, paths: list)}
    """
    results = {}
    for cat_name in categories:
        templates = ARTIFACT_PATHS.get(cat_name, [])
        found_paths = []
        for tmpl in templates:
            path = tmpl.replace('{task_id}', task_id)
            full_path = os.path.join(project_root, 'life_cycle_process',
                                     *[p for p in path.split('/') if p])
            # 目录存在且有文件
            if full_path.endswith('/') or full_path.endswith('\\'):
                if os.path.isdir(full_path):
                    try:
                        files = [f for f in os.listdir(full_path)
                                 if os.path.isfile(os.path.join(full_path, f))]
                        if files:
                            found_paths.append(f'{path} ({len(files)} files)')
                    except OSError:
                        pass
            elif os.path.isfile(full_path):
                found_paths.append(path)
        results[cat_name] = (len(found_paths) > 0, found_paths)
    return results


def check_evidence(content, categories, task_id=None, project_root=None):
    """三层证据检测。

    Tier 1 — 不可伪造证据：只有实际执行后才会出现的输出格式
    Tier 2 — 上下文证据：原证据模式，但排除引用/建议行
    Tier 3 — 文件系统产物：场景 JSON / 截图目录是否存在

    categories: list of 类别名
    返回: {category: (has_evidence: bool, found: list, tier: int)}
    tier: 1=unforgeable, 2=contextual, 3=filesystem, 0=none
    """
    results = {}
    all_evidence = {
        '编译检查': BUILD_EVIDENCE,
        '浏览器交互测试': E2E_EVIDENCE,
        '视觉验证': VISUAL_EVIDENCE,
        'API运行时测试': API_EVIDENCE,
    }
    unforgeable_map = {
        '浏览器交互测试': UNFORGEABLE_E2E,
        'API运行时测试': UNFORGEABLE_API,
        '视觉验证': UNFORGEABLE_VIS,
        '编译检查': ['built in', 'modules transformed', 'No errors'],
    }

    lines = content.split('\n')

    for cat_name in categories:
        if cat_name not in all_evidence:
            continue

        # ── Tier 1: 不可伪造证据 ──
        unforgeable = unforgeable_map.get(cat_name, [])
        uf_found = []
        for line in lines:
            is_skip = any(kw.lower() in line.lower() for kw in SKIP_KEYWORDS)
            if is_skip:
                continue
            for p in unforgeable:
                if p in line or p.lower() in line.lower():
                    uf_found.append(p)
        if uf_found:
            results[cat_name] = (True, list(set(uf_found)), 1)
            continue

        # ── Tier 2: 上下文证据（排除引用/建议行）──
        patterns = all_evidence[cat_name]
        ctx_found = []
        for line in lines:
            is_skip = any(kw.lower() in line.lower() for kw in SKIP_KEYWORDS)
            if is_skip:
                continue
            if is_reference_line(line):
                continue  # 引用/建议行不计为证据
            for p in patterns:
                if p in line or p.lower() in line.lower():
                    ctx_found.append(p)
        if ctx_found:
            results[cat_name] = (True, list(set(ctx_found)), 2)
            continue

        # ── Tier 3: 文件系统产物 ──
        if task_id and project_root:
            art = verify_artifacts(task_id, [cat_name], project_root)
            exists, paths = art.get(cat_name, (False, []))
            if exists:
                results[cat_name] = (True, paths, 3)
                continue

        results[cat_name] = (False, [], 0)

    return results


def parse_execution_summary(content):
    """解析测试报告中的「验证执行摘要」checklist。

    报告格式:
        ## 验证执行摘要
        | 验证类型 | 状态 | 工具/命令 | 结果摘要 |
        |----------|------|-----------|----------|
        | 编译检查 | [x] 已完成 | ... | ... |
        | 视觉验证 | [ ] 未完成 | visual_test.py | 无 API Key |

    返回: {
        'has_summary': bool,
        'items': {
            '编译检查': {'checked': bool, 'status_text': str},
            '视觉验证': {'checked': bool, 'status_text': str},
            ...
        }
    }
    """
    result = {'has_summary': False, 'items': {}}

    # 找 "## 验证执行摘要" 章节
    section_match = re.search(
        r'##\s*验证执行摘要.*?\n(.*?)(?=\n##\s|\n---|\Z)',
        content, re.DOTALL
    )
    if not section_match:
        return result

    result['has_summary'] = True
    section = section_match.group(1)

    # 映射表格中的验证类型名称 → 标准名称
    name_map = {
        '编译检查': '编译检查',
        '浏览器交互测试': '浏览器交互测试',
        'E2E': '浏览器交互测试',
        'e2e': '浏览器交互测试',
        '视觉验证': '视觉验证',
        '视觉回归': '视觉验证',
        'API运行时测试': 'API运行时测试',
        'API': 'API运行时测试',
        'api': 'API运行时测试',
    }

    for line in section.split('\n'):
        # 匹配 | 验证类型 | [x]/[ ] 状态文字 | ... |
        m = re.match(
            r'\|\s*([^|]+?)\s*\|\s*\[(x|X|\s)\]\s*([^|]*?)\s*\|',
            line
        )
        if not m:
            continue

        type_raw = m.group(1).strip()
        checked = m.group(2).lower() == 'x'
        status_text = m.group(3).strip()

        # 映射类型名
        method = None
        for key, val in name_map.items():
            if key.lower() in type_raw.lower():
                method = val
                break

        if method:
            result['items'][method] = {
                'checked': checked,
                'status_text': status_text,
            }

    return result


def detect_static_declaration(content):
    for pattern in STATIC_DECLARATION_PATTERNS:
        if pattern.lower() in content.lower():
            return True, pattern
    return False, None


def count_line_refs(content):
    count = 0
    examples = []
    for m in re.finditer(r'第\s*(\d+)\s*行', content):
        count += 1
        if len(examples) < 5:
            examples.append(m.group(0))
    for m in re.finditer(r'lines?\s+(\d+)', content):
        count += 1
        if len(examples) < 5:
            examples.append(m.group(0))
    return count, examples


def check_test_depth(content, required_cats):
    """测试深度机械检测。

    报告即使有 Tier 1 不可伪造证据，也可能只是 L1-L2 的浅层测试。
    这个函数检查报告内容是否包含 L3（交互触发）和 L4（数据贯通）的签名。

    MangaPlay 案例: "E2E Results: 36/36 steps PASS" 但 36 步全是
    page.goto + count > 0 (L1+L2) → 深度不足。

    返回: (sufficient: bool, max_depth: int, missing_signatures: list)
    """
    # 只对需要交互验证的类别做深度检查
    needs_depth_check = any(
        c in required_cats for c in ['浏览器交互测试', '视觉验证']
    )
    if not needs_depth_check:
        return True, 5, []

    # 排除 reference/skip 行
    lines = []
    for line in content.split('\n'):
        if is_reference_line(line):
            continue
        is_skip = any(kw.lower() in line.lower() for kw in SKIP_KEYWORDS)
        if not is_skip:
            lines.append(line)

    text = '\n'.join(lines)

    def _has_any(patterns):
        return any(p in text for p in patterns)

    l1 = _has_any(L1_SIGNATURES)
    l2 = _has_any(L2_SIGNATURES)
    l3 = _has_any(L3_SIGNATURES)
    l4 = _has_any(L4_SIGNATURES)

    if l4:
        return True, 4, []
    if l3:
        return True, 3, []  # L3 可接受，但不充分（有 L4 更好，不强行 BLOCK）
    if l2:
        return False, 2, ['L2 级别（仅检查元素存在），缺少交互触发 (L3) 和数据贯通 (L4) 验证']
    if l1:
        return False, 1, ['L1 级别（仅页面可访问），缺少元素/交互/数据验证']
    # 没有 E2E 内容 → 可能不是 E2E 报告，不做深度检查
    if not l1 and not l2:
        return True, 0, []
    return True, 0, []


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

    # ── 提取 task-id，查找测试用例 ──
    task_id = extract_task_id_from_path(file_path)
    tc_path, tc_content = find_test_case(task_id)
    project_root = find_project_root()

    # ── 解析测试用例中的验证要求 ──
    tc_req = parse_verification_requirements(tc_content)

    # ── 收集需要检查的验证类别 ──
    all_categories = ['编译检查', '浏览器交互测试', '视觉验证', 'API运行时测试']

    if tc_req['has_section']:
        # 有验证声明 → 以声明为准
        required_cats = [
            cat for cat in all_categories
            if tc_req['requirements'].get(cat) == '必需'
        ]
        recommended_cats = [
            cat for cat in all_categories
            if tc_req['requirements'].get(cat) == '推荐'
        ]
        # 三层证据检测
        evidence_results = check_evidence(content, all_categories,
                                          task_id=task_id,
                                          project_root=project_root)

        # ── 决策：所有「必需」的类别都必须有证据 ──
        # Tier 0 = 无证据 → BLOCK
        # Tier 2 = 仅上下文证据（对 E2E/VIS 不够，编译检查够用）→ BLOCK
        # Tier 1/3 = 不可伪造或文件系统 → PASS
        TIER2_SUFFICIENT = {'编译检查', 'API运行时测试'}  # 这些类别上下文证据可接受
        missing_required = []
        for cat in required_cats:
            has_ev, found_list, tier = evidence_results.get(cat, (False, [], 0))
            if not has_ev:
                missing_required.append((cat, 'no_evidence', []))
            elif tier == 2 and cat not in TIER2_SUFFICIENT:
                # E2E/VIS 需要不可伪造或文件系统证据
                missing_required.append((cat, 'only_contextual', found_list))

        if missing_required:
            cat_descriptions = {
                '编译检查': '编译检查 (tsc + vite build)',
                '浏览器交互测试': '浏览器交互测试 (Playwright E2E)',
                '视觉验证': '视觉验证 (截图 + visual_test.py)',
                'API运行时测试': 'API运行时测试 (HTTP 请求/响应断言)',
            }
            missing_desc = [cat_descriptions.get(c, c) for c, _, _ in missing_required]
            evidence_desc = []
            for cat, (has_ev, found_list, tier) in evidence_results.items():
                level = tc_req['requirements'].get(cat, '未声明')
                tier_label = {0: '无', 1: '不可伪造', 2: '上下文', 3: '文件系统'}.get(tier, '?')
                status = '✅ 有' if has_ev and (tier == 1 or tier == 3 or cat in TIER2_SUFFICIENT) else '⚠️ 弱'
                ev_line = f'{status} {cat} (要求: {level}, 证据层级: {tier_label})'
                if found_list:
                    ev_line += f' — 命中: {", ".join(str(x) for x in found_list[:3])}'
                evidence_desc.append(ev_line)

            # 判断是完全没有证据还是只有弱证据
            only_contextal = [c for c, reason, _ in missing_required if reason == 'only_contextual']
            if only_contextal:
                log_and_output('validate-test-methodology.py', 'no_unforgeable_evidence', {
                    'file_path': file_path,
                    'tc_path': tc_path or '未找到',
                    'missing_unforgeable': [cat_descriptions.get(c, c) for c in only_contextal],
                }, decision='BLOCK')
            else:
                log_and_output('validate-test-methodology.py', 'missing_required_verification', {
                    'file_path': file_path,
                    'tc_path': tc_path or '未找到',
                    'missing': missing_desc,
                    'evidence': evidence_desc,
                }, decision='BLOCK')
            sys.exit(1)

        # 推荐项缺失 → 警告但不阻断
        missing_recommended = []
        for cat in recommended_cats:
            has_ev, _ = evidence_results.get(cat, (False, []))
            if not has_ev:
                missing_recommended.append(cat)

        if missing_recommended:
            log_and_output('validate-test-methodology.py', 'missing_recommended_verification', {
                'file_path': file_path,
                'missing': missing_recommended,
            }, decision='WARN')

        # ── 验证执行摘要交叉检查（Tester 自己的 checklist）──
        summary = parse_execution_summary(content)

        if summary['has_summary']:
            # 检查必需验证在摘要中是否被标记为 [ ] 未完成
            summary_failures = []
            for cat in required_cats:
                item = summary['items'].get(cat)
                if item is None:
                    # 摘要中缺少该验证类型的行 → 可能遗漏
                    summary_failures.append(
                        (cat, 'missing_row', '摘要表格中缺少此行')
                    )
                elif not item['checked']:
                    # Tester 自己承认没做 → 铁证
                    reason = item.get('status_text', '未说明原因')
                    summary_failures.append(
                        (cat, 'unchecked', reason)
                    )

            if summary_failures:
                cat_descriptions = {
                    '编译检查': '编译检查 (tsc + vite build)',
                    '浏览器交互测试': '浏览器交互测试 (Playwright E2E)',
                    '视觉验证': '视觉验证 (截图 + visual_test.py)',
                    'API运行时测试': 'API运行时测试 (HTTP 请求/响应断言)',
                }
                failures_desc = []
                for cat, fail_type, detail in summary_failures:
                    desc = cat_descriptions.get(cat, cat)
                    if fail_type == 'missing_row':
                        failures_desc.append(f'{desc} — 摘要表格中缺少此行')
                    else:
                        failures_desc.append(f'{desc} — Tester 标记为 [ ] 未完成（{detail}）')

                log_and_output('validate-test-methodology.py', 'summary_checklist_mismatch', {
                    'file_path': file_path,
                    'tc_path': tc_path or '未找到',
                    'failures': failures_desc,
                }, decision='BLOCK')
                sys.exit(1)
        else:
            # 报告缺少验证执行摘要 → 警告（不阻断，因为旧报告可能没有）
            log_and_output('validate-test-methodology.py', 'missing_execution_summary', {
                'file_path': file_path,
            }, decision='WARN')

        # ── 测试深度机械检测 ──
        # 证据存在 ≠ 深度足够。MangaPlay: "36/36 PASS" 全是 L1+L2
        depth_ok, max_depth, depth_issues = check_test_depth(content, required_cats)
        if not depth_ok:
            cat_descriptions = {
                '浏览器交互测试': '浏览器交互测试 (Playwright E2E)',
                '视觉验证': '视觉验证 (截图 + visual_test.py)',
            }
            log_and_output('validate-test-methodology.py', 'insufficient_test_depth', {
                'file_path': file_path,
                'tc_path': tc_path or '未找到',
                'max_depth': f'L{max_depth}',
                'issues': depth_issues,
                'hint': '报告有执行证据但层级仅为 L{max_depth}。L1+L2（页面打开+元素存在）不能判 PASS。需要 L4（数据贯通: 操作→持久化→刷新验证）或至少 L3（交互触发: 点击→UI 响应）。',
            }, decision='BLOCK')
            sys.exit(1)

        # 全部通过
        log_hook_event('validate-test-methodology.py', 'PASS', {
            'file': file_path,
            'task_id': task_id,
            'reason': 'all_required_evidence_present',
            'tc_requirements': tc_req['requirements'],
        })
        sys.exit(0)

    else:
        # ── 无验证声明 (legacy test cases) → 回退到启发式检测 ──
        is_declared_static, static_pattern = detect_static_declaration(content)
        line_ref_count, line_examples = count_line_refs(content)
        evidence_results = check_evidence(content, all_categories,
                                          task_id=task_id,
                                          project_root=project_root)
        has_any_tools = any(v[0] for v in evidence_results.values())

        should_block = False
        block_reason = None

        if is_declared_static and not has_any_tools:
            should_block = True
            block_reason = 'explicit_static_declaration'
        elif line_ref_count >= 10 and not has_any_tools:
            should_block = True
            block_reason = 'dense_line_refs_no_runtime_tools'

        if not should_block:
            log_hook_event('validate-test-methodology.py', 'PASS_LEGACY', {
                'file': file_path,
                'task_id': task_id,
                'reason': 'heuristic_pass',
                'note': 'test_case_lacks_verification_section',
            })
            sys.exit(0)

        if block_reason == 'explicit_static_declaration':
            log_and_output('validate-test-methodology.py', 'explicit_static_declaration', {
                'file_path': file_path,
                'static_pattern': static_pattern or '',
            }, decision='BLOCK')
        else:
            log_and_output('validate-test-methodology.py', 'dense_line_refs_no_runtime_tools', {
                'file_path': file_path,
                'line_ref_count': line_ref_count,
            }, decision='BLOCK')
        sys.exit(1)


if __name__ == '__main__':
    main()
