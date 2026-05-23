"""
Basic Command Plugin Example
Demonstrates a simple user-triggered command plugin.
"""


async def handle_command(context, host_api):
    """
    Handle command invocation.

    Args:
        context: Contains command_name, chat_id, message_id, user_id, settings, etc.
        host_api: Provides send_message, reply methods
    """
    # Get greeting from settings or use default
    greeting = context.settings.get("greeting_message", "Hello from AMO!")

    # Reply to the command
    await host_api.reply(context.chat_id, context.message_id, greeting)
