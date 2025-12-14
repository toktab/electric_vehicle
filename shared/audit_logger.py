# ============================================================================
# Audit Logger - Structured Event Tracking
# ============================================================================

import os
import json
import threading
from datetime import datetime

AUDIT_LOG_FILE = "data/audit_log.txt"
AUDIT_ENABLED = True

class AuditLogger:
    """
    Audit logger per specification:
    a. Event date and time
    b. Who and from where (IP address)
    c. What action is performed
    d. Event parameters or description
    """
    
    def __init__(self, log_file=AUDIT_LOG_FILE):
        self.log_file = log_file
        self.lock = threading.Lock()
        self.enabled = AUDIT_ENABLED
        
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        if self.enabled:
            print(f"[AuditLogger] Enabled - logging to {log_file}")
    
    def log_event(self, source_ip, event_type, action, details=None):
        """
        Log audit event
        
        Args:
            source_ip: IP address source (e.g., "192.168.1.10")
            event_type: Event type (e.g., "AUTHENTICATION", "CHARGING", "FAULT")
            action: Action (e.g., "AUTH_SUCCESS", "CHARGE_START", "CP_FAULT")
            details: Additional information (dict or str)
        """
        if not self.enabled:
            return
        
        timestamp = datetime.now().isoformat()
        
        entry = {
            "timestamp": timestamp,
            "source_ip": source_ip,
            "event_type": event_type,
            "action": action,
            "details": details or {}
        }
        
        with self.lock:
            try:
                with open(self.log_file, 'a') as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as e:
                print(f"[AuditLogger] Error writing log: {e}")
    
    def log_authentication(self, source_ip, cp_id, success, reason=None):
        """Log authentication attempt"""
        action = "AUTH_SUCCESS" if success else "AUTH_FAILED"
        details = {"cp_id": cp_id, "success": success}
        if not success and reason:
            details["reason"] = reason
        self.log_event(source_ip, "AUTHENTICATION", action, details)
    
    def log_charging_session(self, source_ip, cp_id, driver_id, action, kwh=None, amount=None):
        """Log charging session event"""
        details = {"cp_id": cp_id, "driver_id": driver_id}
        if kwh is not None:
            details["kwh_delivered"] = kwh
        if amount is not None:
            details["amount_euro"] = amount
        self.log_event(source_ip, "CHARGING", action, details)
    
    def log_fault(self, source_ip, cp_id, fault_type, description=None):
        """Log fault or error"""
        details = {"cp_id": cp_id, "fault_type": fault_type}
        if description:
            details["description"] = description
        self.log_event(source_ip, "SYSTEM_FAULT", fault_type, details)
    
    def log_state_change(self, source_ip, entity_id, old_state, new_state):
        """Log state change"""
        details = {"entity_id": entity_id, "old_state": old_state, "new_state": new_state}
        self.log_event(source_ip, "STATE_CHANGE", "STATE_TRANSITION", details)
    
    def get_recent_logs(self, limit=100):
        """Get recent audit logs"""
        logs = []
        try:
            with open(self.log_file, 'r') as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    if line.strip():
                        try:
                            logs.append(json.loads(line))
                        except:
                            continue
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[AuditLogger] Error reading logs: {e}")
        return logs

# Global instance
_audit_logger = None

def get_audit_logger():
    """Get global audit logger"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger

# Shortcut functions
def log_auth(ip, cp_id, success, reason=None):
    get_audit_logger().log_authentication(ip, cp_id, success, reason)

def log_charge(ip, cp_id, driver_id, action, kwh=None, amount=None):
    get_audit_logger().log_charging_session(ip, cp_id, driver_id, action, kwh, amount)

def log_fault(ip, cp_id, fault_type, desc=None):
    get_audit_logger().log_fault(ip, cp_id, fault_type, desc)

def log_state(ip, entity_id, old, new):
    get_audit_logger().log_state_change(ip, entity_id, old, new)