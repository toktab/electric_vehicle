# ============================================================================
# EVCharging System - EV_CP_M (Charging Point Monitor) - FIXED FOR DISTRIBUTED
# ============================================================================

import socket
import threading
import time
import sys
import os
from config import CP_BASE_PORT, HEALTH_CHECK_INTERVAL
from shared.protocol import Protocol, MessageTypes


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

        print(f"[{self.cp_id} Monitor] Initializing...")
        print(f"[{self.cp_id} Monitor] Central: {self.central_host}:{self.central_port}")
        print(f"[{self.cp_id} Monitor] Engine: {self.engine_host}:{self.engine_port}")

    def connect_to_engine(self):
        """Connect to CP Engine"""
        print(f"[{self.cp_id} Monitor] Connecting to Engine at {self.engine_host}:{self.engine_port}...")
        
        max_retries = 10
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                self.engine_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.engine_socket.connect((self.engine_host, self.engine_port))
                print(f"[{self.cp_id} Monitor] ✅ Connected to Engine")
                return True
            except Exception as e:
                print(f"[{self.cp_id} Monitor] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    print(f"[{self.cp_id} Monitor] Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    print(f"[{self.cp_id} Monitor] ❌ Failed to connect to Engine after {max_retries} attempts")
                    return False

    def connect_to_central(self):
        """Connect to CENTRAL"""
        print(f"[{self.cp_id} Monitor] Connecting to CENTRAL at {self.central_host}:{self.central_port}...")
        
        max_retries = 10
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                self.central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.central_socket.connect((self.central_host, self.central_port))
                print(f"[{self.cp_id} Monitor] ✅ Connected to CENTRAL")
                return True
            except Exception as e:
                print(f"[{self.cp_id} Monitor] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    print(f"[{self.cp_id} Monitor] Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    print(f"[{self.cp_id} Monitor] ❌ Failed to connect to CENTRAL after {max_retries} attempts")
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
                                        print(f"\n[{self.cp_id} Monitor] ✅ Engine recovered!")
                                        # Send recovery to CENTRAL
                                        recovery_msg = Protocol.encode(
                                            Protocol.build_message(
                                                MessageTypes.RECOVERY, self.cp_id
                                            )
                                        )
                                        self.central_socket.send(recovery_msg)

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
                    print(f"\n[{self.cp_id} Monitor] ⚠️  ENGINE FAULT DETECTED!")
                    print(f"[{self.cp_id} Monitor] Notifying CENTRAL...")

                    # Send fault to CENTRAL
                    try:
                        fault_msg = Protocol.encode(
                            Protocol.build_message(MessageTypes.FAULT, self.cp_id)
                        )
                        self.central_socket.send(fault_msg)
                        print(f"[{self.cp_id} Monitor] ✅ FAULT message sent to CENTRAL\n")
                    except Exception as e:
                        print(f"[{self.cp_id} Monitor] Failed to send fault: {e}")

    def display_menu(self):
        """Display interactive menu - NO auto status printing"""
        print(f"\n[{self.cp_id} Monitor] Ready. Type 'help' for commands.\n")
        
        while self.running:
            try:
                cmd = input(f"[{self.cp_id} Monitor]> ").strip().lower()

                if cmd == "help":
                    print(f"\n{'='*60}")
                    print(f"[{self.cp_id} Monitor] Available Commands:")
                    print(f"{'='*60}")
                    print("  status  - View current engine status")
                    print("  fault   - Simulate engine fault")
                    print("  reset   - Reset failure counter")
                    print("  help    - Show this help")
                    print("  quit    - Exit monitor")
                    print(f"{'='*60}\n")

                elif cmd == "status":
                    with self.lock:
                        status_str = "HEALTHY ✅" if self.engine_healthy else "FAULTY ❌"
                        print(f"\n{'='*60}")
                        print(f"Engine Status: {status_str}")
                        print(f"Consecutive Failures: {self.consecutive_failures}/{self.failure_threshold}")
                        print(f"{'='*60}\n")

                elif cmd == "fault":
                    with self.lock:
                        print(f"\n⚠️  Simulating fault...")
                        self.consecutive_failures = self.failure_threshold
                        self._handle_engine_fault()
                    print(f"✅ Fault simulation complete\n")

                elif cmd == "reset":
                    with self.lock:
                        self.consecutive_failures = 0
                        print(f"\n✅ Failure counter reset\n")

                elif cmd == "quit":
                    print(f"\n[{self.cp_id} Monitor] Shutting down...\n")
                    self.running = False
                    break

                elif cmd == "":
                    continue

                else:
                    print(f"\n❌ Unknown command: '{cmd}'. Type 'help' for commands.\n")

            except EOFError:
                break
            except KeyboardInterrupt:
                print(f"\n[{self.cp_id} Monitor] Interrupted\n")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}\n")

    def run(self):
        """Run the monitor"""
        print(f"[{self.cp_id} Monitor] Starting...")

        if not self.connect_to_engine():
            print(f"[{self.cp_id} Monitor] Cannot connect to Engine")
            return

        if not self.connect_to_central():
            print(f"[{self.cp_id} Monitor] Cannot connect to CENTRAL")
            return

        # Start health check loop in background
        health_thread = threading.Thread(
            target=self.health_check_loop,
            daemon=True
        )
        health_thread.start()

        # Display menu (no auto status thread)
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