"""
Basic Command Plugin Example
Demonstrates a simple user-triggered command plugin.
"""

from typing import Dict, Any


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """
    Handle command invocation.

    Args:
        context: Command context with keys:
            - plugin_id: Plugin name
            - run_id: Unique execution ID
            - chat_id: Telegram Chat-ID
            - message_id: Message ID
            - message_thread_id: Thread ID (optional)
            - user_id: Telegram User-ID
            - role: User role (ignore/normal/vip/admin/owner)
            - command_name: Command that was invoked
            - argument: Command argument text
            - attachments: List of attachment dicts
            - reply_to_image: Image context for analysis
        host_api: Provides send_message, reply methods
    """
    # Use the argument from context if user provided one, or use default greeting
    argument = context.get("argument", "")
    greeting = argument if argument else "Hello from AMO!"

    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    # Reply to the command
    await host_api.reply(chat_id, message_id, greeting)
