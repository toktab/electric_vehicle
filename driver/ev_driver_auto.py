# driver/ev_driver_auto.py
# ============================================================================
# EVCharging System - AUTOMATED DRIVER (File-based)
# ============================================================================

import socket
import threading
import time
import sys
import os
from config import WAIT_BETWEEN_REQUESTS
from shared.protocol import Protocol, MessageTypes
from shared.kafka_client import KafkaClient


class EVDriverAuto:
    def __init__(self, driver_id, central_host="localhost", central_port=5000, requests_file=None):
        self.driver_id = driver_id
        self.central_host = central_host
        self.central_port = central_port
        self.requests_file = requests_file
        
        self.central_socket = None
        self.running = True
        self.lock = threading.Lock()
        
        self.status = "IDLE"
        self.current_cp = None
        
        self.charging_requests = []
        self.current_request_number = 0
        
        self.kafka = KafkaClient(f"EV_Driver_{driver_id}")
        
        print(f"\n{'='*70}")
        print(f"🚗 AUTOMATED DRIVER: {driver_id}")
        print(f"{'='*70}")

    def load_requests_from_file(self):
        """Load charging requests from file"""
        if not self.requests_file or not os.path.exists(self.requests_file):
            print(f"❌ File not found: {self.requests_file}")
            return False
        
        print(f"\n📂 Reading file: {self.requests_file}")
        
        try:
            with open(self.requests_file, 'r') as f:
                line_number = 0
                for line in f:
                    line_number += 1
                    line = line.strip()
                    
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = [p.strip() for p in line.split(',')]
                    
                    if len(parts) >= 2:
                        cp_id = parts[0]
                        kwh_needed = float(parts[1])
                        
                        self.charging_requests.append({
                            "cp_id": cp_id,
                            "kwh_needed": kwh_needed
                        })
                        
                        print(f"  ✓ Request {len(self.charging_requests)}: {cp_id}, {kwh_needed} kWh")
                    else:
                        print(f"  ⚠️  Skipping invalid line {line_number}: {line}")
            
            print(f"\n✅ Loaded {len(self.charging_requests)} charging requests")
            return len(self.charging_requests) > 0
        
        except Exception as e:
            print(f"❌ Error reading file: {e}")
            return False

    def connect_to_central(self):
        """Connect to CENTRAL"""
        print(f"\n🔌 Connecting to CENTRAL at {self.central_host}:{self.central_port}...")
        
        try:
            self.central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.central_socket.connect((self.central_host, self.central_port))
            
            register_msg = Protocol.encode(
                Protocol.build_message(MessageTypes.REGISTER, "DRIVER", self.driver_id)
            )
            self.central_socket.send(register_msg)
            
            print(f"✅ Connected and registered with CENTRAL")
            
            listener_thread = threading.Thread(target=self._listen_to_central, daemon=True)
            listener_thread.start()
            
            return True
        
        except Exception as e:
            print(f"❌ Failed to connect: {e}")
            return False

    def _listen_to_central(self):
        """Listen for messages from CENTRAL"""
        buffer = b''
        
        try:
            while self.running:
                data = self.central_socket.recv(4096)
                if not data:
                    break
                
                buffer += data
                
                while len(buffer) > 0:
                    message, is_valid = Protocol.decode(buffer)
                    
                    if is_valid:
                        etx_pos = buffer.find(b'\x03')
                        buffer = buffer[etx_pos + 2:]
                        
                        fields = Protocol.parse_message(message)
                        msg_type = fields[0]
                        
                        if msg_type == MessageTypes.AUTHORIZE:
                            self._handle_authorization(fields)
                        elif msg_type == MessageTypes.DENY:
                            self._handle_denial(fields)
                        elif msg_type == MessageTypes.TICKET:
                            self._handle_ticket(fields)
                        elif msg_type == MessageTypes.SUPPLY_UPDATE:
                            self._handle_supply_update(fields)
                    else:
                        break
        
        except Exception as e:
            print(f"❌ Connection lost: {e}")

    def _handle_authorization(self, fields):
        """CENTRAL says YES"""
        cp_id = fields[2]
        kwh_needed = fields[3]
        price = fields[4]
        
        with self.lock:
            self.status = "CHARGING"
            self.current_cp = cp_id
        
        print(f"\n✅ AUTHORIZED to charge at {cp_id}")
        print(f"   Charging {kwh_needed} kWh @ {price}€/kWh")
        print(f"   ⚡ Charging in progress...\n")

    def _handle_denial(self, fields):
        """CENTRAL says NO"""
        cp_id = fields[2] if len(fields) > 2 else "?"
        reason = fields[3] if len(fields) > 3 else "UNKNOWN"
        
        with self.lock:
            self.status = "IDLE"
            self.current_cp = None
        
        print(f"\n❌ DENIED at {cp_id} - Reason: {reason}")
        print(f"   Moving to next request...\n")
        
        self._schedule_next_request()

    def _handle_ticket(self, fields):
        """Charging completed - received ticket"""
        cp_id = fields[1] if len(fields) > 1 else "?"
        total_kwh = fields[2] if len(fields) > 2 else "?"
        total_amount = fields[3] if len(fields) > 3 else "?"
        
        with self.lock:
            self.status = "IDLE"
            self.current_cp = None
        
        print(f"\n{'='*70}")
        print(f"🎫 CHARGING TICKET")
        print(f"{'='*70}")
        print(f"   Driver: {self.driver_id}")
        print(f"   CP: {cp_id} | Energy: {total_kwh} kWh | Cost: {total_amount}€")
        print(f"{'='*70}\n")
        
        self._schedule_next_request()

    def _handle_supply_update(self, fields):
        """Real-time update during charging"""
        if len(fields) < 4:
            return
        
        cp_id = fields[1]
        consumption_kw = fields[2]
        amount_euro = fields[3]
        
        print(f"⚡ Charging: {consumption_kw} kW - {amount_euro}€")

    def _schedule_next_request(self):
        """Wait, then process next request"""
        def wait_and_process():
            print(f"⏳ Waiting {WAIT_BETWEEN_REQUESTS} seconds before next request...")
            time.sleep(WAIT_BETWEEN_REQUESTS)
            self.process_next_request()
        
        thread = threading.Thread(target=wait_and_process, daemon=True)
        thread.start()

    def send_charge_request(self, cp_id, kwh_needed):
        """Send charging request to CENTRAL"""
        with self.lock:
            if self.status != "IDLE":
                print(f"⚠️  Cannot send request (status: {self.status})")
                return False
            
            self.status = "REQUESTING"
        
        request_msg = Protocol.encode(
            Protocol.build_message(
                MessageTypes.REQUEST_CHARGE,
                self.driver_id,
                cp_id,
                kwh_needed
            )
        )
        
        try:
            self.central_socket.send(request_msg)
            print(f"📤 Sent request: {cp_id}, {kwh_needed} kWh")
            return True
        
        except Exception as e:
            print(f"❌ Failed to send request: {e}")
            with self.lock:
                self.status = "IDLE"
            return False

    def process_next_request(self):
        """Process next request in queue"""
        with self.lock:
            if self.current_request_number >= len(self.charging_requests):
                print(f"\n{'='*70}")
                print(f"✅ ALL REQUESTS COMPLETED!")
                print(f"   Processed {self.current_request_number} charging requests")
                print(f"{'='*70}\n")
                self.running = False
                return False
            
            request = self.charging_requests[self.current_request_number]
            self.current_request_number += 1
            request_num = self.current_request_number
            total = len(self.charging_requests)
        
        print(f"\n{'='*70}")
        print(f"📋 Processing Request {request_num}/{total}")
        print(f"{'='*70}")
        
        self.send_charge_request(request["cp_id"], request["kwh_needed"])
        
        return True

    def run(self):
        """Main execution"""
        print(f"\n🚀 Starting automated driver...\n")
        
        if not self.load_requests_from_file():
            print("❌ Cannot load requests. Exiting.")
            return
        
        if not self.connect_to_central():
            print("❌ Cannot connect to CENTRAL. Exiting.")
            return
        
        print("\n⏳ Waiting 2 seconds before starting...\n")
        time.sleep(2)
        
        self.process_next_request()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n⚠️  Interrupted by user")
        finally:
            if self.central_socket:
                self.central_socket.close()
            self.kafka.close()
        
        print(f"\n👋 {self.driver_id} shutdown complete\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ev_driver_auto.py <DRIVER_ID> [central_host] [central_port] [requests_file]")
        print("\nExample:")
        print("  python ev_driver_auto.py DRIVER-AUTO localhost 5000 data/charging_requests.txt")
        sys.exit(1)
    
    driver_id = sys.argv[1]
    central_host = sys.argv[2] if len(sys.argv) > 2 else "localhost"
    central_port = int(sys.argv[3]) if len(sys.argv) > 3 else 5000
    requests_file = sys.argv[4] if len(sys.argv) > 4 else "data/charging_requests.txt"
    
    driver = EVDriverAuto(driver_id, central_host, central_port, requests_file)
    driver.run()