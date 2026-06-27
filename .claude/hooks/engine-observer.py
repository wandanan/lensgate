"""PostToolUse Hook: Pipeline Engine enforcement.

Agent 完成后自动推进任务状态，执行 consequences。
引擎自主运行，不依赖编排者记忆。

自动转换：
  dev_done    → tester_ready  (同时生成 Tester Brief + 标记 test-cases)
  tester_done → verifying     (同时执行交叉验证)

触发: Agent tool call
"""

import sys
import os

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:
    pass

from hook_util import (
    start_watchdog,
    parse_tool_input,
    extract_task_id,
    find_project_root,
    log_hook_event,
)


def main():
    start_watchdog(timeout=8.0)
    tool_input = parse_tool_input()
    if tool_input is None:
        sys.exit(0)

    description = tool_input.get("description", "")
    task_id = extract_task_id(description)
    if task_id is None:
        sys.exit(0)

    try:
        root = find_project_root()
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, skill_dir)
        from engine.pipeline import PipelineEngine
        from engine.state_store import StateStore

        store = StateStore(root)
        state = store.get_state(task_id)
        if state is None:
            sys.exit(0)

        domain = state.get("domain", "default")
        engine = PipelineEngine(root, domain, observer_mode=False)

        # Log the Agent completion event
        engine.event_log.log(
            "agent_completed",
            task_id=task_id,
            description=description,
        )

        # Auto-advance: dev_done → tester_ready, tester_done → verifying
        # In enforcement mode, this actually transitions state + runs consequences
        result = engine.auto_advance(task_id, reason="agent_completed")

        log_hook_event(
            "engine-observer.py",
            "ADVANCE" if result.get("action") != "none" else "INFO",
            {
                "task_id": task_id,
                "auto_advance": result,
            },
        )

    except Exception as e:
        log_hook_event("engine-observer.py", "ERROR", {"error": str(e)})

    sys.exit(0)


if __name__ == "__main__":
    main()
