"""PreToolUse hook: 阻断主智能体直接编辑业务源码。

规则：主智能体禁止直接 Edit/Write 项目根目录下的功能域源码目录。
功能域名 = life_cycle_process/ 第一层子目录名。
对应禁写区域 = 项目根目录下与功能域同名的目录。

例如：life_cycle_process/frontend/ 存在 -> 根目录 frontend/ 为禁写区域。

设计原则：
- 任何异常/超时一律放行（exit 0），宁可漏过不可误杀
- 拦截时 stdout + stderr 双写，确保告警可见
- 所有 I/O 操作均有超时保护
"""

import json
import sys
import os
import subprocess
import threading

# 将脚本目录加入路径以导入 hook_util
try: sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError: pass

from hook_util import log_and_output

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 全局超时兜底：8 秒后无论如何退出（放行），防止任何原因导致的挂起
_timer = threading.Timer(8.0, lambda: os._exit(0))
_timer.daemon = True
_timer.start()


def read_stdin(timeout=3.0):
    """读取 stdin，带超时保护。超时或异常返回 None。"""
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


def get_project_root():
    """获取项目根目录。git 仓库用 git 定位，否则用 cwd。"""
    # 先检测是否在 git 仓库中，避免 git stderr 泄漏
    try:
        check = subprocess.run(
            ['git', 'rev-parse', '--is-inside-work-tree'],
            capture_output=True, text=True, timeout=3
        )
        if check.returncode != 0:
            return os.getcwd()
    except Exception:
        return os.getcwd()
    try:
        r = subprocess.run(
            ['git', 'rev-parse', '--git-common-dir'],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout.strip():
            return os.path.normpath(os.path.dirname(r.stdout.strip()))
    except Exception:
        pass
    return os.getcwd()


def get_domains(project_root):
    """从 life_cycle_process/ 第一层子目录读取功能域名"""
    lcp = os.path.join(project_root, 'life_cycle_process')
    if not os.path.isdir(lcp):
        return set()
    domains = set()
    try:
        for entry in os.listdir(lcp):
            if os.path.isdir(os.path.join(lcp, entry)):
                domains.add(entry)
    except OSError:
        pass
    return domains


def is_business_code(file_path, project_root):
    """文件是否在项目根目录下的功能域源码目录中"""
    path = file_path.replace('\\', '/')
    domains = get_domains(project_root)
    for domain in domains:
        prefix = domain + '/'
        if path.startswith(prefix) or path == domain:
            return True
    return False


def main():
    # 步骤 1: 读取 stdin（带超时）
    raw_input = read_stdin(timeout=3.0)
    if raw_input is None:
        sys.exit(0)
    if not raw_input.strip():
        sys.exit(0)

    # 步骤 2: 解析 JSON
    try:
        tool_input = json.loads(raw_input)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    file_path = tool_input.get('file_path', '')
    if not file_path:
        sys.exit(0)

    # 步骤 3: 获取项目根目录
    try:
        project_root = get_project_root()
    except Exception:
        sys.exit(0)

    # 步骤 4: 判断并拦截
    try:
        blocked = is_business_code(file_path, project_root)
    except Exception:
        sys.exit(0)

    if blocked:
        domains = get_domains(project_root)
        domain_list = ', '.join(sorted(domains)) if domains else '(none)'

        log_and_output('block-direct-edit.py', 'direct_edit_blocked', {
            'file_path': file_path,
            'domain_list': domain_list,
        }, decision='BLOCK')
        sys.exit(1)

    sys.exit(0)


# ── 行内日志（自包含，不依赖 hook_util）────────────────────

def _hook_log(hook_name, decision, detail=None):
    """写 Hook 事件到 life_cycle_process/{domain}/hook-logs/{hook}.jsonl"""
    try:
        import datetime
        root = get_project_root()
        lcp = os.path.join(root, 'life_cycle_process')
        domain = 'default'
        if os.path.isdir(lcp):
            for d in sorted(os.listdir(lcp)):
                if os.path.isdir(os.path.join(lcp, d)):
                    domain = d
                    break
        log_dir = os.path.join(lcp, domain, 'hook-logs')
        os.makedirs(log_dir, exist_ok=True)
        entry = {
            'ts': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'hook': hook_name,
            'decision': decision,
        }
        if detail:
            entry['detail'] = detail
        log_file = os.path.join(log_dir, f'{hook_name}.jsonl')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


if __name__ == '__main__':
    main()