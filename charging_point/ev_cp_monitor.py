# ============================================================================
# EVCharging System - EV_CP_M (Charging Point Monitor)
# ============================================================================

import socket
import threading
import time
import sys
from config import CP_BASE_PORT, HEALTH_CHECK_INTERVAL
from shared.protocol import Protocol, MessageTypes
from shared.kafka_client import KafkaClient

central_host = 'central'
central_port = 5000

while True:
    try:
        s = socket.create_connection((central_host, central_port), timeout=5)
        print("Connected to CENTRAL")
        break
    except ConnectionRefusedError:
        print("Cannot connect to CENTRAL yet, retrying in 2s...")
        time.sleep(2)


class EVCPMonitor:
    def __init__(self, cp_id, engine_host="localhost", engine_port=None,
                 central_host="localhost", central_port=5000):
        self.cp_id = cp_id
        self.engine_host = engine_host
        self.engine_port = engine_port or (CP_BASE_PORT + int(cp_id.split('-')[1]))
        self.central_host = central_host
        self.central_port = central_port

        self.engine_socket = None
        self.central_socket = None
        self.running = True
        self.lock = threading.Lock()

        # Status tracking
        self.engine_healthy = True
        self.consecutive_failures = 0
        self.failure_threshold = 3

        # Kafka
        self.kafka = KafkaClient(f"EV_CP_M_{cp_id}")

        print(f"[{self.cp_id} Monitor] Initializing...")

    def connect_to_engine(self):
        """Connect to CP Engine"""
        try:
            self.engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.engine_socket.connect((self.engine_host, self.engine_port))
            print(f"[{self.cp_id} Monitor] Connected to Engine")
            return True
        except Exception as e:
            print(f"[{self.cp_id} Monitor] Failed to connect to Engine: {e}")
            return False

    def connect_to_central(self):
        """Connect to CENTRAL"""
        try:
            self.central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.central_socket.connect((self.central_host, self.central_port))
            print(f"[{self.cp_id} Monitor] Connected to CENTRAL")
            return True
        except Exception as e:
            print(f"[{self.cp_id} Monitor] Failed to connect to CENTRAL: {e}")
            return False

    def health_check_loop(self):
        """Send health checks to engine every second"""
        while self.running:
            time.sleep(HEALTH_CHECK_INTERVAL)

            try:
                if not self.engine_socket:
                    continue

                # Send health check
                health_msg = Protocol.encode(
                    Protocol.build_message(MessageTypes.HEALTH_CHECK, self.cp_id)
                )
                self.engine_socket.send(health_msg)

                # Wait for response with timeout
                self.engine_socket.settimeout(2)
                try:
                    response_data = self.engine_socket.recv(4096)

                    if response_data:
                        response, is_valid = Protocol.decode(response_data)

                        if is_valid:
                            fields = Protocol.parse_message(response)

                            if fields[0] == "HEALTH_OK":
                                with self.lock:
                                    if not self.engine_healthy:
                                        print(f"[{self.cp_id} Monitor] Engine recovered!")
                                        # Send recovery to CENTRAL
                                        recovery_msg = Protocol.encode(
                                            Protocol.build_message(
                                                MessageTypes.RECOVERY, self.cp_id
                                            )
                                        )
                                        self.central_socket.send(recovery_msg)

                                        self.kafka.publish_event(
                                            "system_events", "ENGINE_RECOVERED",
                                            {"cp_id": self.cp_id}
                                        )

                                    self.engine_healthy = True
                                    self.consecutive_failures = 0

                            elif fields[0] == "HEALTH_KO":
                                self._handle_engine_fault()

                        else:
                            self._handle_engine_fault()
                    else:
                        self._handle_engine_fault()

                except socket.timeout:
                    self._handle_engine_fault()

            except Exception as e:
                print(f"[{self.cp_id} Monitor] Health check error: {e}")
                self._handle_engine_fault()

    def _handle_engine_fault(self):
        """Handle engine fault detection"""
        with self.lock:
            self.consecutive_failures += 1

            if self.consecutive_failures >= self.failure_threshold:
                if self.engine_healthy:
                    self.engine_healthy = False
                    print(f"[{self.cp_id} Monitor] ENGINE FAULT DETECTED!")

                    # Send fault to CENTRAL
                    try:
                        fault_msg = Protocol.encode(
                            Protocol.build_message(MessageTypes.FAULT, self.cp_id)
                        )
                        self.central_socket.send(fault_msg)
                    except Exception as e:
                        print(f"[{self.cp_id} Monitor] Failed to send fault: {e}")

                    self.kafka.publish_event(
                        "system_events", "ENGINE_FAULT",
                        {"cp_id": self.cp_id}
                    )

    def display_status(self):
        """Display monitor status periodically"""
        while self.running:
            time.sleep(3)

            with self.lock:
                status = "OK" if self.engine_healthy else "FAULT"
                print(f"[{self.cp_id} Monitor] Engine Status: {status} "
                      f"(Failures: {self.consecutive_failures})")

    def display_menu(self):
        """Display interactive menu"""
        while self.running:
            try:
                print(f"\n[{self.cp_id} Monitor Menu]")
                print(f"Engine Status: {'HEALTHY' if self.engine_healthy else 'FAULTY'}")
                print("Options:")
                print("1. View status")
                print("2. Force fault simulation (optional)")
                print("3. Reset failures counter")

                choice = input("Choice (or press Enter to continue): ").strip()

                if choice == "1":
                    print(f"Engine: {'HEALTHY' if self.engine_healthy else 'FAULTY'}")
                    print(f"Consecutive failures: {self.consecutive_failures}")

                elif choice == "2":
                    print("(Note: Press 'y' in Engine menu to simulate fault)")

                elif choice == "3":
                    with self.lock:
                        self.consecutive_failures = 0
                    print("Counter reset")

                time.sleep(1)

            except Exception as e:
                print(f"Menu error: {e}")

    def run(self):
        """Run the monitor"""
        print(f"[{self.cp_id} Monitor] Starting...")

        if not self.connect_to_engine():
            print(f"[{self.cp_id} Monitor] Cannot connect to Engine")
            return

        if not self.connect_to_central():
            print(f"[{self.cp_id} Monitor] Cannot connect to CENTRAL")
            return

        # Start health check loop
        health_thread = threading.Thread(
            target=self.health_check_loop,
            daemon=True
        )
        health_thread.start()

        # Start status display
        status_thread = threading.Thread(
            target=self.display_status,
            daemon=True
        )
        status_thread.start()

        # Display menu
        try:
            self.display_menu()
        except KeyboardInterrupt:
            print(f"\n[{self.cp_id} Monitor] Shutting down...")
        finally:
            self.running = False
            if self.engine_socket:
                self.engine_socket.close()
            if self.central_socket:
                self.central_socket.close()
            self.kafka.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ev_cp_monitor.py <CP_ID> [engine_host] [engine_port] "
              "[central_host] [central_port]")
        sys.exit(1)

    cp_id = sys.argv[1]
    engine_host = sys.argv[2] if len(sys.argv) > 2 else "localhost"
    engine_port = int(sys.argv[3]) if len(sys.argv) > 3 else None
    central_host = sys.argv[4] if len(sys.argv) > 4 else "localhost"
    central_port = int(sys.argv[5]) if len(sys.argv) > 5 else 5000

    monitor = EVCPMonitor(cp_id, engine_host, engine_port, central_host, central_port)
    monitor.run()