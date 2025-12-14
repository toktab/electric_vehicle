# ============================================================================
# EVCharging System - Protocol Handler (STX/DATA/ETX/LRC)
# ============================================================================

from config import STX, ETX, DELIMITER
from shared.encryption import EncryptionManager  # NEW
import json  # NEW


class Protocol:
    """Message format: <STX><DATA><ETX><LRC>"""

    @staticmethod
    def calculate_lrc(data):
        """Calculate LRC (XOR of all bytes)"""
        lrc = 0
        for byte in data:
            lrc ^= byte
        return bytes([lrc])

    @staticmethod
    def encode(message, encryption_key=None):  # NEW: encryption_key param
        """Encode message: <STX><DATA><ETX><LRC>"""
        message_bytes = message.encode() if isinstance(message, str) else message
        
        # NEW: Encrypt if key provided
        if encryption_key:
            encrypted = EncryptionManager.encrypt(message_bytes.decode(), encryption_key)
            message_bytes = json.dumps({"encrypted": encrypted}).encode()
        
        data = STX + message_bytes + ETX
        lrc = Protocol.calculate_lrc(data)
        return data + lrc

    @staticmethod
    def decode(raw_data, encryption_key=None):  # NEW: encryption_key param
        """Decode message from <STX><DATA><ETX><LRC>"""
        if len(raw_data) < 4:
            return None, False

        if raw_data[0:1] != STX:
            return None, False

        try:
            etx_index = raw_data.index(ETX[0], 1)
        except ValueError:
            return None, False

        data_part = raw_data[1:etx_index]
        received_lrc = raw_data[etx_index + 1:etx_index + 2]

        if len(received_lrc) != 1:
            return None, False

        message_to_check = raw_data[:etx_index + 1]
        calculated_lrc = Protocol.calculate_lrc(message_to_check)

        if calculated_lrc != received_lrc:
            return None, False

        try:
            message = data_part.decode('utf-8')
            
            # NEW: Decrypt if encrypted
            if encryption_key:
                try:
                    msg_dict = json.loads(message)
                    if "encrypted" in msg_dict:
                        message = EncryptionManager.decrypt(msg_dict["encrypted"], encryption_key)
                except:
                    pass  # Not encrypted, use as-is
            
            return message, True
        except Exception:
            return None, False

    @staticmethod
    def parse_message(message):
        """Parse decoded message into fields"""
        return message.split('#')

    @staticmethod
    def build_message(*fields):
        """Build message from fields"""
        return '#'.join(str(f) for f in fields)


# Message types (unchanged)
class MessageTypes:
    REGISTER = "REGISTER"
    HEARTBEAT = "HEARTBEAT"
    SUPPLY_START = "SUPPLY_START"
    SUPPLY_UPDATE = "SUPPLY_UPDATE"
    SUPPLY_END = "SUPPLY_END"
    FAULT = "FAULT"
    RECOVERY = "RECOVERY"
    REQUEST_CHARGE = "REQUEST_CHARGE"
    QUERY_AVAILABLE_CPS = "QUERY_AVAILABLE_CPS"
    END_CHARGE = "END_CHARGE"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    AUTHORIZE = "AUTHORIZE"
    DENY = "DENY"
    STOP_COMMAND = "STOP_COMMAND"
    RESUME_COMMAND = "RESUME_COMMAND"
    END_SUPPLY = "END_SUPPLY"
    TICKET = "TICKET"
    AVAILABLE_CPS = "AVAILABLE_CPS"
    HEALTH_CHECK = "HEALTH_CHECK"
    HEALTH_OK = "HEALTH_OK"
    HEALTH_KO = "HEALTH_KO"