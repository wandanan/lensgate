"""gate-agent-launch.py — PreToolUse Hook

Agent 启动前自动检查任务状态门禁。
引擎驱动，不依赖编排者记忆。

规则：
  - Dev Agent   → 任务必须处于 dev_ready
  - Tester Agent → 任务必须处于 tester_ready
  - 其他 Agent   → 不拦截

状态不对 → BLOCK，告诉编排者当前状态和期望状态。

触发: Agent tool call
"""

import sys
import os
import re

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:
    pass

from hook_util import (
    start_watchdog, parse_tool_input, extract_task_id,
    find_project_root, log_hook_event, log_and_output
)

# Agent 类型 → 期望的任务状态
AGENT_EXPECTED_STATE = {
    "dev": "dev_ready",
    "tester": "tester_ready",
    "tester-functional": "tester_ready",
    "tester-e2e": "tester_ready",
    "tester-layout": "tester_ready",
    "tester-beauty": "tester_ready",
}


def detect_agent_type(description, prompt):
    """从 Agent 描述和 prompt 中检测 Agent 类型。"""
    text = (description or "") + " " + (prompt or "")[:800]
    lower = text.lower()

    if "tester-e2e" in lower or "e2e 测试" in lower or "e2e test" in lower:
        return "tester-e2e"
    if "tester-layout" in lower or "布局测试" in lower:
        return "tester-layout"
    if "tester-beauty" in lower or "视觉美观" in lower or "beauty" in lower:
        return "tester-beauty"
    if "tester-functional" in lower or "功能测试" in lower:
        return "tester-functional"
    if "tester" in lower or "测试" in lower or "test" in lower:
        return "tester"
    if "dev" in lower or "开发" in lower or "implement" in lower or "coding" in lower:
        return "dev"

    return None


def get_engine_and_state(task_id):
    """获取引擎实例和任务当前状态。"""
    root = find_project_root()
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, skill_dir)

    from engine.pipeline import PipelineEngine
    from engine.state_store import StateStore

    store = StateStore(root)
    state = store.get_state(task_id)
    if state is None:
        return None, None, None

    domain = state.get("domain", "default")
    engine = PipelineEngine(root, domain, observer_mode=False)
    return engine, state, domain


def main():
    start_watchdog(timeout=5.0)

    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    description = tool_input.get("description", "")
    prompt = tool_input.get("prompt", "")

    task_id = extract_task_id(description, prompt)
    if task_id is None:
        sys.exit(0)

    agent_type = detect_agent_type(description, prompt)
    if agent_type is None:
        sys.exit(0)

    expected_state = AGENT_EXPECTED_STATE.get(agent_type)
    if expected_state is None:
        sys.exit(0)

    try:
        engine, state, domain = get_engine_and_state(task_id)
    except Exception as e:
        log_hook_event("gate-agent-launch.py", "ERROR", {"error": str(e)})
        sys.exit(0)

    if state is None:
        # 任务未注册，不拦截（可能还没到 Phase 2）
        sys.exit(0)

    current_state = state.get("current_state", "unknown")

    if current_state == expected_state:
        # 状态正确，放行
        log_hook_event("gate-agent-launch.py", "PASS", {
            "task_id": task_id,
            "agent_type": agent_type,
            "current_state": current_state,
        })
        sys.exit(0)

    # 状态不对，检查是否已经过了期望状态（不需要回退）
    state_order = [
        "pending", "dev_ready", "dev_launched", "dev_done",
        "tester_ready", "tester_launched", "tester_done",
        "verifying", "verified", "verification_failed"
    ]

    try:
        current_idx = state_order.index(current_state)
        expected_idx = state_order.index(expected_state)
    except ValueError:
        current_idx = -1
        expected_idx = -1

    if current_idx > expected_idx:
        # 已经过了期望状态（比如 Tester 已经启动了），不拦截
        log_hook_event("gate-agent-launch.py", "PASS_SKIP", {
            "task_id": task_id,
            "agent_type": agent_type,
            "current_state": current_state,
            "reason": "already_past_expected_state",
        })
        sys.exit(0)

    # 状态还没到期望状态，阻断
    state_labels = {
        "pending": "待执行（等待 test-cases 创建）",
        "dev_ready": "开发就绪（等待启动 Dev Agent）",
        "dev_launched": "开发中（Dev Agent 执行中）",
        "dev_done": "开发完成（等待引擎自动生成 Tester Brief）",
        "tester_ready": "测试就绪（等待启动 Tester Agent）",
        "tester_launched": "测试中（Tester Agent 执行中）",
        "tester_done": "测试完成（等待交叉验证）",
        "verifying": "验证中",
        "verified": "已验收",
        "verification_failed": "验证失败",
    }

    agent_labels = {
        "dev": "Dev Agent",
        "tester": "Tester Agent",
        "tester-functional": "Tester-Functional Agent",
        "tester-e2e": "Tester-E2E Agent",
        "tester-layout": "Tester-Layout Agent",
        "tester-beauty": "Tester-Beauty Agent",
    }

    action_hints = {
        "pending": "先创建 test-cases/{task_id}.md，引擎会自动将状态推进到 dev_ready。",
        "dev_ready": "当前可以启动 Dev Agent。状态已就绪。",
        "dev_launched": "Dev Agent 正在执行。请等待引擎完成自动转换后再启动 Tester。",
        "dev_done": "Dev Agent 刚完成。引擎会自动推进到 tester_ready，请等待。",
        "tester_ready": "当前可以启动 Tester Agent。状态已就绪。",
    }

    log_and_output('gate-agent-launch.py', 'wrong_agent_state', {
        'task_id': task_id,
        'agent_label': agent_labels.get(agent_type, agent_type),
        'current_state': current_state,
        'current_state_label': state_labels.get(current_state, '未知'),
        'expected_state': expected_state,
        'expected_state_label': state_labels.get(expected_state, '未知'),
        'action_hint': action_hints.get(current_state, ''),
    }, decision='BLOCK')
    sys.exit(1)


if __name__ == "__main__":
    main()
