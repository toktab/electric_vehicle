# ============================================================================
# EVCharging System - EV_Driver (Driver Application)
# ============================================================================

import socket
import threading
import time
import sys
import os
from datetime import datetime
from config import WAIT_BETWEEN_REQUESTS
from shared.protocol import Protocol, MessageTypes
from shared.kafka_client import KafkaClient


class EVDriver:
    def __init__(self, driver_id, central_host="localhost", central_port=5000,
                 requests_file=None):
        self.driver_id = driver_id
        self.central_host = central_host
        self.central_port = central_port
        self.requests_file = requests_file

        self.central_socket = None
        self.running = True
        self.lock = threading.Lock()

        # Driver state
        self.status = "IDLE"
        self.current_cp = None
        self.charge_amount = 0
        self.requests_queue = []

        # Kafka
        self.kafka = KafkaClient(f"EV_Driver_{driver_id}")

        print(f"[{self.driver_id}] Driver initializing...")

    def connect_to_central(self):
        """Connect to CENTRAL"""
        try:
            self.central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.central_socket.connect((self.central_host, self.central_port))

            # Register with CENTRAL
            register_msg = Protocol.encode(
                Protocol.build_message(MessageTypes.REGISTER, "DRIVER", self.driver_id)
            )
            self.central_socket.send(register_msg)
            print(f"[{self.driver_id}] Registered with CENTRAL")

            # Start listening for messages from CENTRAL
            thread = threading.Thread(
                target=self._listen_central,
                daemon=True
            )
            thread.start()

            return True

        except Exception as e:
            print(f"[{self.driver_id}] Failed to connect to CENTRAL: {e}")
            return False

    def _listen_central(self):
        """Listen for messages from CENTRAL"""
        buffer = b''
        try:
            while self.running:
                try:
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

                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[{self.driver_id}] Listener error: {e}")
                    break

        except Exception as e:
            print(f"[{self.driver_id}] CENTRAL connection lost: {e}")

    def _handle_authorization(self, fields):
        """Handle charging authorization from CENTRAL"""
        # AUTHORIZE#driver_id#cp_id#kwh_needed#price
        cp_id = fields[2]
        kwh_needed = fields[3]
        price = fields[4]

        with self.lock:
            self.status = "AUTHORIZED"
            self.current_cp = cp_id

        print(f"[{self.driver_id}] ✓ AUTHORIZED to charge at {cp_id}")
        print(f"    kWh needed: {kwh_needed}, Price: {price}€/kWh")

        self.kafka.publish_event("charging_logs", "DRIVER_AUTHORIZED", {
            "driver_id": self.driver_id,
            "cp_id": cp_id,
            "kwh_needed": kwh_needed
        })
        print(f"[{self.driver_id}] Vehicle plugged in automatically, starting supply...")

    def _handle_denial(self, fields):
        """Handle charging denial from CENTRAL"""
        # DENY#driver_id#cp_id#reason
        cp_id = fields[2] if len(fields) > 2 else "?"
        reason = fields[3] if len(fields) > 3 else "UNKNOWN"

        with self.lock:
            self.status = "DENIED"

        print(f"[{self.driver_id}] ✗ DENIED charging at {cp_id}")
        print(f"    Reason: {reason}")

        self.kafka.publish_event("charging_logs", "DRIVER_DENIED", {
            "driver_id": self.driver_id,
            "cp_id": cp_id,
            "reason": reason
        })

    def _handle_ticket(self, fields):
        """Handle charging ticket from CENTRAL"""
        # TICKET#cp_id#total_kwh#total_amount
        cp_id = fields[1] if len(fields) > 1 else "?"
        total_kwh = fields[2] if len(fields) > 2 else "?"
        total_amount = fields[3] if len(fields) > 3 else "?"

        with self.lock:
            self.status = "COMPLETED"
            self.current_cp = None

        print(f"\n{'='*50}")
        print(f"CHARGING TICKET - {self.driver_id}")
        print(f"{'='*50}")
        print(f"Charging Point: {cp_id}")
        print(f"Energy: {total_kwh} kWh")
        print(f"Amount: {total_amount}€")
        print(f"{'='*50}\n")

        self.kafka.publish_event("charging_logs", "DRIVER_TICKET", {
            "driver_id": self.driver_id,
            "cp_id": cp_id,
            "total_kwh": total_kwh,
            "total_amount": total_amount
        })

    def _handle_supply_update(self, fields):
        """Handle real-time supply updates during charging"""
        # SUPPLY_UPDATE#cp_id#consumption_kw#amount_euro
        if len(fields) < 4:
            return
        
        cp_id = fields[1]
        consumption_kw = fields[2]
        amount_euro = fields[3]
        
        print(f"[{self.driver_id}] ⚡ Charging at {cp_id}: "
            f"{consumption_kw} kW - {amount_euro}€")

    def load_requests_from_file(self):
        """Load charging requests from file"""
        if not self.requests_file or not os.path.exists(self.requests_file):
            print(f"[{self.driver_id}] No requests file found")
            return

        try:
            with open(self.requests_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        self.requests_queue.append(line)

            print(f"[{self.driver_id}] Loaded {len(self.requests_queue)} requests from file")

        except Exception as e:
            print(f"[{self.driver_id}] Error loading requests: {e}")

    def request_charge(self, cp_id, kwh_needed=10):
        """Request charging from a specific CP"""
        if self.status != "IDLE":
            print(f"[{self.driver_id}] Cannot request (status: {self.status})")
            return False

        with self.lock:
            self.status = "REQUESTING"

        request_msg = Protocol.encode(
            Protocol.build_message(
                MessageTypes.REQUEST_CHARGE, self.driver_id, cp_id, kwh_needed
            )
        )

        try:
            self.central_socket.send(request_msg)
            print(f"[{self.driver_id}] Requesting charge: CP {cp_id}, {kwh_needed} kWh")

            self.kafka.publish_event("charging_logs", "CHARGE_REQUESTED", {
                "driver_id": self.driver_id,
                "cp_id": cp_id,
                "kwh_needed": kwh_needed
            })

            return True

        except Exception as e:
            print(f"[{self.driver_id}] Error sending request: {e}")
            with self.lock:
                self.status = "IDLE"
            return False

    def process_requests_from_file(self):
        """Process requests from file sequentially"""
        if not self.requests_queue:
            return

        for request_line in self.requests_queue:
            # Parse line format: CP_ID,KWH_NEEDED
            parts = request_line.split(',')
            if len(parts) >= 2:
                cp_id = parts[0].strip()
                kwh_needed = float(parts[1].strip())

                print(f"\n[{self.driver_id}] Processing request: {cp_id}, {kwh_needed} kWh")

                self.request_charge(cp_id, kwh_needed)

                # Wait for response (authorization or denial)
                time.sleep(2)

                # If charging completed, wait before next request
                if self.status in ["COMPLETED", "DENIED"]:
                    print(f"[{self.driver_id}] Waiting {WAIT_BETWEEN_REQUESTS}s before next request...")
                    time.sleep(WAIT_BETWEEN_REQUESTS)

                    with self.lock:
                        self.status = "IDLE"

    def display_menu(self):
        """Display interactive menu for driver"""
        while self.running:
            try:
                print(f"\n[{self.driver_id} MENU]")
                print(f"Status: {self.status}")
                if self.current_cp:
                    print(f"At: {self.current_cp}")

                print("\nOptions:")
                print("1. Request charge (manual)")
                print("2. View available CPs (would list from CENTRAL)")
                print("3. Process file requests")
                print("4. View status")

                choice = input("Choice (or press Enter to continue): ").strip()

                if choice == "1":
                    # Reset status if previous charge completed
                    if self.status in ["COMPLETED", "DENIED"]:
                        with self.lock:
                            self.status = "IDLE"
                    
                    cp_id = input("Enter CP ID: ").strip()
                    kwh_str = input("Enter kWh needed (default 10): ").strip()
                    kwh = float(kwh_str) if kwh_str else 10
                    self.request_charge(cp_id, kwh)

                elif choice == "2":
                    print("\n(Available CPs would be displayed here)")
                    print("(This is handled by CENTRAL monitoring)")

                elif choice == "3":
                    if self.requests_queue:
                        self.process_requests_from_file()
                    else:
                        print("No requests loaded")

                elif choice == "4":
                    print(f"Status: {self.status}")
                    print(f"Current CP: {self.current_cp}")

                time.sleep(1)

            except Exception as e:
                print(f"Menu error: {e}")

    def run(self):
        """Run the driver"""
        if not self.connect_to_central():
            print(f"[{self.driver_id}] Cannot connect to CENTRAL")
            return

        # Load requests from file if provided
        if self.requests_file:
            self.load_requests_from_file()
            # Start processing requests automatically
            requests_thread = threading.Thread(
                target=self.process_requests_from_file,
                daemon=True
            )
            requests_thread.start()

        # Display menu
        try:
            self.display_menu()
        except KeyboardInterrupt:
            print(f"\n[{self.driver_id}] Shutting down...")
        finally:
            self.running = False
            if self.central_socket:
                self.central_socket.close()
            self.kafka.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ev_driver.py <DRIVER_ID> [central_host] [central_port] [requests_file]")
        sys.exit(1)

    driver_id = sys.argv[1]
    central_host = sys.argv[2] if len(sys.argv) > 2 else "localhost"
    central_port = int(sys.argv[3]) if len(sys.argv) > 3 else 5000
    requests_file = sys.argv[4] if len(sys.argv) > 4 else None

    driver = EVDriver(driver_id, central_host, central_port, requests_file)
    driver.run()