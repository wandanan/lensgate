"""dev-lifecycle Hook 共享基础设施。

所有 Hook 脚本遵循相同的安全模式：
- 8s 全局看门狗 — 任何原因导致挂起时 os._exit(0)（宁放过不误杀）
- 3s stdin 读取超时 — 超时放行
- 任何异常 → exit 0（pass through）
- stdout + stderr 双写 — 确保消息可见

用法：
    from hook_util import start_watchdog, read_stdin, parse_tool_input
    from hook_util import find_project_root, find_domains, PROJECT_ROOT

    def main():
        start_watchdog()
        tool_input = parse_tool_input()
        if tool_input is None:
            sys.exit(0)
        # ... hook logic ...
"""

import json
import os
import re
import sys
import subprocess
import threading

# UTF-8 编码（Windows 兼容）
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 模块加载时解析项目根目录（缓存）
_PROJECT_ROOT = None
_DOMAINS = None

# ── 共享证据检测常量 ──
REFERENCE_PATTERNS = [
    '建议', '推荐', '推荐使用', '应当', '应该', '需要安装',
    'CI 环境', 'CI 中', 'CI pipeline', 'CI/CD',
    'planned', 'deferred', 'TODO', 'FIXME',
    '待补充', '待执行', '待验证',
    'future', 'later', 'next time', '后续',
    '可以考虑', '可考虑', '考虑使用', '考虑引入',
]


def is_reference_line(line):
    """检查一行是否含引用/建议语言（非实际执行证据）。"""
    lower = line.lower()
    return any(p.lower() in lower for p in REFERENCE_PATTERNS)


def start_watchdog(timeout=8.0):
    """启动全局看门狗。超时后强制 os._exit(0) 放行。"""
    timer = threading.Timer(timeout, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    return timer


def read_stdin(timeout=3.0):
    """从 stdin 读取，带超时保护。超时或异常返回 None。"""
    result = []

    def _read():
        try:
            result.append(sys.stdin.read())
        except Exception:
            pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    return result[0] if result else None


def parse_tool_input():
    """读取并解析 stdin JSON 为 tool call 参数。失败返回 None。"""
    raw = read_stdin()
    if raw is None:
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, EOFError):
        return None


def is_git_repo():
    """检测当前目录是否在 git 仓库中。结果缓存。"""
    global _IS_GIT_REPO
    if '_IS_GIT_REPO' in globals() and _IS_GIT_REPO is not None:
        return _IS_GIT_REPO
    try:
        r = subprocess.run(
            ['git', 'rev-parse', '--is-inside-work-tree'],
            capture_output=True, text=True, timeout=3
        )
        _IS_GIT_REPO = r.returncode == 0
    except Exception:
        _IS_GIT_REPO = False
    return _IS_GIT_REPO


def find_project_root():
    """获取项目根目录。git 仓库用 git 定位，否则用 cwd。结果缓存。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is not None:
        return _PROJECT_ROOT
    if is_git_repo():
        try:
            r = subprocess.run(
                ['git', 'rev-parse', '--git-common-dir'],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                _PROJECT_ROOT = os.path.normpath(os.path.dirname(r.stdout.strip()))
                return _PROJECT_ROOT
        except Exception:
            pass
    _PROJECT_ROOT = os.getcwd()
    return _PROJECT_ROOT


def find_domains():
    """从 life_cycle_process/ 第一层子目录读取所有功能域名。结果缓存。"""
    global _DOMAINS
    if _DOMAINS is not None:
        return _DOMAINS
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')
    _DOMAINS = set()
    if not os.path.isdir(lcp):
        return _DOMAINS
    try:
        for entry in os.listdir(lcp):
            if os.path.isdir(os.path.join(lcp, entry)):
                _DOMAINS.add(entry)
    except OSError:
        pass
    return _DOMAINS


def is_business_code(file_path):
    """判断文件是否在功能域源码目录下。"""
    path = file_path.replace('\\', '/')
    for domain in find_domains():
        prefix = domain + '/'
        if path.startswith(prefix) or path == domain:
            return True
    return False


def find_phase2_dir(domain=None):
    """查找 phase-2-development 目录。如未指定 domain，在所有域中搜索。"""
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')

    domains = [domain] if domain else find_domains()
    for d in domains:
        phase2 = os.path.join(lcp, d, 'phase-2-development')
        if os.path.isdir(phase2):
            return phase2, d
    return None, None


def find_test_report(task_id, domain=None):
    """搜索 test-reports/{task_id}.md 并检查是否包含 PASS。"""
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')

    domains = [domain] if domain else find_domains()
    for d in domains:
        for sub in ['phase-2-development', 'phase-3-iteration']:
            report = os.path.join(lcp, d, sub, 'test-reports', f'{task_id}.md')
            if os.path.isfile(report):
                if _file_contains(report, '判定：PASS'):
                    return report, True
                return report, False
    return None, None


def find_test_cases(task_id, domain=None):
    """搜索 test-cases/{task_id}.md。"""
    root = find_project_root()
    lcp = os.path.join(root, 'life_cycle_process')

    domains = [domain] if domain else find_domains()
    for d in domains:
        for sub in ['phase-2-development', 'phase-3-iteration']:
            tc = os.path.join(lcp, d, sub, 'test-cases', f'{task_id}.md')
            if os.path.isfile(tc):
                return tc
    return None


def _file_contains(path, text, max_lines=80):
    """检查文件前 max_lines 行是否包含 text。"""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                if text in line:
                    return True
    except Exception:
        pass
    return False


def extract_task_id(description, prompt=None):
    """从 Agent description 或 prompt 中提取 task ID。
    匹配模式："Task {id}" 或 "task-{id}" 或 "{task-id}"。
    返回 task ID 字符串或 None。
    """
    import re
    # 合并 search text
    texts = []
    if description:
        texts.append(description)
    if prompt:
        # 只取 prompt 前 500 字符（足够了）
        texts.append(prompt[:500])

    for text in texts:
        # "Task 3" or "Task task-0-handoff" or "task-1-2-handoff"
        m = re.search(r'[Tt]ask\s+(\S+)', text)
        if m:
            return m.group(1)
        # "task-0-handoff", "task-1-2", "task-queuefull"
        m = re.search(r'(task-[\w-]+)', text)
        if m:
            return m.group(1)
    return None


def blocked_msg(title, lines):
    """生成统一的阻断消息格式。"""
    border = '=' * 60
    parts = ['', border, f'  ⛔ {title}', border, '']
    parts.extend(f'  {line}' for line in lines)
    parts.extend(['', border, ''])
    return '\n'.join(parts)


def warn_msg(title, lines):
    """生成统一的警告消息格式。"""
    border = '-' * 56
    parts = ['', border, f'  ⚠️  {title}', border, '']
    parts.extend(f'  {line}' for line in lines)
    parts.extend(['', border, ''])
    return '\n'.join(parts)


def info_msg(title, lines):
    """生成统一的提醒消息格式。"""
    border = '━' * 56
    parts = ['', border, f'  📌 {title}', border, '']
    parts.extend(f'  {line}' for line in lines)
    parts.extend(['', border, ''])
    return '\n'.join(parts)


# ── 集中消息配置系统 ─────────────────────────────────────────────
# hook-messages.yaml 集中管理所有 Hook 提醒消息
# 运行时由 load_messages() 加载，render_message() 渲染


_messages_cache = None


def _parse_simple_yaml(text):
    """简易 YAML 解析器，处理 hook-messages.yaml 的缩进结构。

    支持: 键值对、块标量(|)、单/双引号字符串、整数、布尔值。
    不支持: 流映射、锚点、标签等高级特性。
    """
    result = {}
    stack = [(0, result)]
    multiline_key = None
    multiline_indent = 0
    multiline_parent = None
    multiline_parts = []

    for line in text.split('\n'):
        # 跳过注释和空行
        stripped = line.lstrip()
        if not stripped or stripped.startswith('#'):
            if multiline_key:
                multiline_parts.append('')
            continue

        indent = len(line) - len(stripped)

        # 多行字符串收集
        if multiline_key:
            if indent >= multiline_indent:
                multiline_parts.append(line[multiline_indent:])
                continue
            else:
                multiline_parent[multiline_key] = '\n'.join(multiline_parts).rstrip('\n')
                multiline_key = None
                multiline_parts = []

        # 解析键值对
        colon_idx = stripped.find(':')
        if colon_idx < 0:
            continue

        key = stripped[:colon_idx].strip()
        value_part = stripped[colon_idx + 1:].strip()

        # 找到父容器
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        parent = stack[-1][1]

        if value_part == '|':
            # 块标量: 收集后续缩进行，完成后直接赋值为字符串
            multiline_key = key
            multiline_indent = indent + 2
            multiline_parent = parent
            multiline_parts = []
        elif not value_part:
            # 新的嵌套字典
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            # 标量值
            val = value_part
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            elif val == 'true':
                val = True
            elif val == 'false':
                val = False
            elif val.lstrip('-').isdigit():
                val = int(val)
            parent[key] = val

    # 收尾: 未关闭的多行字符串
    if multiline_key:
        multiline_parent[multiline_key] = '\n'.join(multiline_parts).rstrip('\n')

    return result


def load_messages(config_path=None):
    """加载 hook-messages.yaml，缓存到全局变量。"""
    global _messages_cache
    if _messages_cache is not None:
        return _messages_cache

    if config_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'hook-messages.yaml')
        if not os.path.isfile(config_path):
            parent = os.path.dirname(script_dir)
            config_path = os.path.join(parent, 'script', 'hook-messages.yaml')

    if not os.path.isfile(config_path):
        _messages_cache = {}
        return _messages_cache

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            text = f.read()
        _messages_cache = _parse_simple_yaml(text)
    except Exception:
        _messages_cache = {}

    return _messages_cache


def render_message(hook_name, reason, variables):
    """从配置中取出消息模板，填充变量。

    Returns:
        (level, title, rendered_text) 或 None（配置中找不到时）
    """
    messages = load_messages()
    hook_config = messages.get(hook_name)
    if not hook_config:
        return None

    entry = hook_config.get(reason)
    if not entry:
        return None

    level = entry.get('level', 'block')
    title = entry.get('title', '')
    template = entry.get('message', '')

    # 先把 {{xxx}} 替换成占位符，避免干扰变量替换
    template = re.sub(r'\{\{(\w+)\}\}', r'__BRACE_\1__', template)

    # 变量替换
    def replacer(match):
        var_name = match.group(1)
        val = variables.get(var_name)
        if val is None:
            return match.group(0)  # 保留未替换的占位符
        if isinstance(val, list):
            lines = []
            for item in val:
                if isinstance(item, dict):
                    tid = item.get('task_id', '?')
                    status = item.get('status', '?')
                    desc = item.get('desc', item.get('description', ''))
                    lines.append(f'❌ {tid} ({status}) → {desc}')
                else:
                    lines.append(f'❌ {item}')
            return '\n'.join(lines)
        return str(val)

    rendered = re.sub(r'\{(\w+)\}', replacer, template)

    # 恢复 {{xxx}} → {xxx}
    rendered = re.sub(r'__BRACE_(\w+)__', r'{\1}', rendered)

    return level, title, rendered


def log_and_output(hook_name, reason, variables, decision):
    """统一：渲染消息 → 输出到 stdout+stderr → 写入 hook log（含消息文本）。

    hook_name 用于日志文件名（如 'validate-test-case.py'），
    YAML 查找时自动去掉 .py 后缀（如 'validate-test-case'）。

    Returns:
        bool — True 表示消息已渲染输出，False 表示配置中找不到（调用方应 fallback）
    """
    yaml_key = hook_name.removesuffix('.py')
    result = render_message(yaml_key, reason, variables)
    if result is None:
        # 配置中找不到 → log 事件但返回 False 让调用方 fallback
        log_hook_event(hook_name, decision, {'reason': reason, **variables})
        return False

    level, title, rendered = result

    # 格式化输出
    if level == 'block':
        border = '=' * 60
        output = f'\n{border}\n  ⛔ {title}\n{border}\n{rendered}\n{border}\n'
    elif level == 'warn':
        border = '-' * 56
        output = f'\n{border}\n  ⚠️  {title}\n{border}\n{rendered}\n{border}\n'
    else:
        border = '━' * 56
        output = f'\n{border}\n  📌 {title}\n{border}\n{rendered}\n{border}\n'

    # stdout + stderr 双写
    sys.stdout.write(output)
    sys.stdout.flush()
    sys.stderr.write(output)
    sys.stderr.flush()

    # 写入 hook log（含完整消息文本）
    log_hook_event(hook_name, decision, {
        'reason': reason,
        **variables,
        'message': rendered,
    })

    return True


# ── Hook 日志系统 ──────────────────────────────────────────────
# 所有 Hook 在执行时自动写入结构化日志到 life_cycle_process/{domain}/hook-logs/
# 用户可通过日志验证 Hook 是否真的在运行


def _get_log_dir():
    """获取 hook-logs 目录路径。自动创建。"""
    root = find_project_root()
    domains = find_domains()
    # 写入第一个找到的域（通常只有一个域在工作）
    if domains:
        domain = sorted(domains)[0]
    else:
        domain = 'default'
    log_dir = os.path.join(root, 'life_cycle_process', domain, 'hook-logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        pass
    return log_dir


def log_hook_event(hook_name, decision, detail=None):
    """记录 Hook 事件到日志文件。

    Args:
        hook_name: Hook 脚本名 (如 'gate-test-report.py')
        decision: 判定 — 'PASS' | 'BLOCK' | 'REMIND' | 'WARN' | 'ERROR'
        detail: 附加信息 dict (如 {'task_id': 'task-0', 'reason': '...'})

    日志格式: 每行一个 JSON 对象 (JSONL)，追加写入。
    绝不抛异常 — Hook 日志只是辅助，不能影响 Hook 正常功能。
    """
    try:
        import datetime
        log_dir = _get_log_dir()
        log_file = os.path.join(log_dir, f'{hook_name}.jsonl')

        entry = {
            'ts': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'hook': hook_name,
            'decision': decision,
        }
        if detail:
            entry['detail'] = detail

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass  # 日志失败静默，不影响 Hook 功能
