"""
Basic Command Plugin Example
Demonstrates a simple user-triggered command plugin.
"""

from typing import Dict, Any

class Plugin:
    """Basic command plugin that responds to /hello command."""
    
    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.settings = context.get("settings", {})
        self.logger = context.get("logger")
    
    def execute(self, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the plugin command.
        
        Args:
            trigger_data: Contains user info, chat context, etc.
        
        Returns:
            Response data with message to send
        """
        # Get greeting from settings or use default
        greeting = self.settings.get("greeting_message", "Hello from AMO!")
        
        # Log execution for audit trail
        self.logger.info(f"Command /hello executed by user {trigger_data.get('user_id')}")
        
        return {
            "status": "success",
            "message": greeting,
            "type": "text"
        }
    
    def get_help(self) -> str:
        """Return help text for this command."""
        return "/hello - Sends a greeting message to the chat"


# Plugin entry point
def create_plugin(context: Dict[str, Any]) -> Plugin:
    """Factory function to create plugin instance."""
    return Plugin(context)
