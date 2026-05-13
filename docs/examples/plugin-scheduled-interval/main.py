"""
Scheduled Interval Plugin Example
Demonstrates a plugin that runs on a fixed interval (cron/interval).
"""

import datetime
from typing import Dict, Any, Optional

class Plugin:
    """Scheduled plugin that sends periodic messages."""
    
    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.settings = context.get("settings", {})
        self.logger = context.get("logger")
        self.db = context.get("db")
    
    def execute(self, trigger_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Execute the scheduled task.
        
        Args:
            trigger_data: Contains trigger info, timestamp, etc.
        
        Returns:
            Response data or None if no action needed
        """
        # Check if scheduled messages are enabled
        if not self.settings.get("enabled", True):
            self.logger.info("Scheduled execution skipped: plugin disabled in settings")
            return None
        
        # Get configuration
        target_group = self.settings.get("target_group")
        if not target_group:
            self.logger.error("Target group not configured")
            return {"status": "error", "error": "Target group not configured"}
        
        # Format message with timestamp
        template = self.settings.get("message_template", "⏰ Scheduled check at {timestamp}")
        timestamp = datetime.datetime.now().isoformat()
        message = template.format(timestamp=timestamp)
        
        # Log execution for monitoring
        self.logger.info(f"Scheduled execution at {timestamp}")
        
        # Update last run timestamp in plugin database
        self._update_last_run()
        
        return {
            "status": "success",
            "action": "send_message",
            "target": target_group,
            "message": message,
            "timestamp": timestamp
        }
    
    def _update_last_run(self):
        """Store last execution time in plugin database."""
        try:
            self.db.execute(
                "INSERT INTO run_history (executed_at) VALUES (?)",
                (datetime.datetime.now().isoformat(),)
            )
        except Exception as e:
            self.logger.warning(f"Could not update run history: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Return current plugin status for health monitoring."""
        return {
            "enabled": self.settings.get("enabled", True),
            "target_group": self.settings.get("target_group"),
            "interval_seconds": 300
        }


# Plugin entry point
def create_plugin(context: Dict[str, Any]) -> Plugin:
    """Factory function to create plugin instance."""
    return Plugin(context)
