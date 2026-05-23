"""
Scheduled Interval Plugin Example
Demonstrates a plugin that runs on a fixed interval (cron/interval).

Note: Scheduled runtime context does NOT include settings, secrets,
or chat configuration. Use only the fields provided in the context.
"""

import datetime


async def handle_schedule(context, host_api):
    """
    Execute the scheduled task.

    Args:
        context: Contains trigger_type, plugin_id, run_id, scheduled_at
        host_api: Provides logging; no message sending available without target

    Returns:
        dict: Summary of execution (logged/returned, not sent to chat)
    """
    # Log execution details to stdout (captured by runner)
    timestamp = datetime.datetime.now().isoformat()
    plugin_id = getattr(context, 'plugin_id', 'unknown')
    run_id = getattr(context, 'run_id', 'unknown')
    trigger_type = getattr(context, 'trigger_type', 'unknown')
    scheduled_at = getattr(context, 'scheduled_at', 'unknown')

    summary = {
        "status": "executed",
        "plugin_id": plugin_id,
        "run_id": run_id,
        "trigger_type": trigger_type,
        "scheduled_at": str(scheduled_at),
        "executed_at": timestamp,
        "message": f"Scheduled task completed for plugin {plugin_id}"
    }

    # Print for logging/debugging (captured by sandbox)
    print(f"[ScheduledPlugin] run_id={run_id} plugin_id={plugin_id} at={timestamp}")

    # Return summary since scheduled context has no target chat/settings
    return summary
