# ============================================================================
# EVCharging System - EV_CP_E (Charging Point Engine) - COMPLETE FINAL VERSION
# ============================================================================

import socket
import threading
import time
import sys
from datetime import datetime
from config import CP_BASE_PORT, CP_STATES, COLORS, SUPPLY_UPDATE_INTERVAL
from shared.protocol import Protocol, MessageTypes
from shared.kafka_client import KafkaClient


class EVCPEngine:
    def __init__(self, cp_id, latitude, longitude, price_per_kwh, 
                 central_host="localhost", central_port=5000,
                 monitor_host="localhost", monitor_port=None):
        self.cp_id = cp_id
        self.latitude = latitude
        self.longitude = longitude
        self.price_per_kwh = float(price_per_kwh)

        self.central_host = central_host
        self.central_port = central_port
        self.monitor_host = monitor_host
        self.monitor_port = monitor_port or (CP_BASE_PORT + int(cp_id.split('-')[1]))

        self.state = CP_STATES["ACTIVATED"]
        self.current_driver = None
        self.current_session = None  # {driver_id, start_time, kwh, amount}

        self.central_socket = None
        self.monitor_socket = None
        self.running = True
        self.lock = threading.Lock()

        # Kafka
        self.kafka = KafkaClient(f"EV_CP_E_{cp_id}")

        # Health status
        self.health_ok = True
        self.simulate_fault = False

        print(f"[{self.cp_id}] Engine initializing...")

    def connect_to_central(self):
        """Connect to central system via socket"""
        try:
            self.central_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.central_socket.connect((self.central_host, self.central_port))

            # Register with CENTRAL
            register_msg = Protocol.encode(
                Protocol.build_message(
                    MessageTypes.REGISTER, "CP", self.cp_id,
                    self.latitude, self.longitude, self.price_per_kwh
                )
            )
            self.central_socket.send(register_msg)
            print(f"[{self.cp_id}] Registered with CENTRAL")

            # Start listening for messages from CENTRAL
            thread = threading.Thread(
                target=self._listen_central,
                daemon=True
            )
            thread.start()

            return True

        except Exception as e:
            print(f"[{self.cp_id}] Failed to connect to CENTRAL: {e}")
            return False

    def listen_for_monitor(self):
        """Listen for Monitor connections via socket"""
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((self.monitor_host, self.monitor_port))
            server_socket.listen(1)

            print(f"[{self.cp_id}] Listening for monitor on port {self.monitor_port}")

            while self.running:
                try:
                    client_socket, addr = server_socket.accept()
                    self.monitor_socket = client_socket
                    print(f"[{self.cp_id}] Monitor connected")

                    # Start health check listener
                    thread = threading.Thread(
                        target=self._listen_monitor,
                        daemon=True
                    )
                    thread.start()

                except Exception as e:
                    if self.running:
                        print(f"[{self.cp_id}] Monitor connection error: {e}")

        except Exception as e:
            print(f"[{self.cp_id}] Monitor listening error: {e}")

    def _listen_central(self):
        """Listen for messages from CENTRAL via socket"""
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
                            
                            if fields[0] == MessageTypes.AUTHORIZE:
                                self._handle_authorization(fields)
                            
                            # ✅ FIX #5: Handle STOP/RESUME commands from CENTRAL
                            elif fields[0] == MessageTypes.STOP_COMMAND:
                                self._handle_stop_command()
                            
                            elif fields[0] == MessageTypes.RESUME_COMMAND:
                                self._handle_resume_command()

                        else:
                            break

                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[{self.cp_id}] CENTRAL listener error: {e}")
                    break

        except Exception as e:
            print(f"[{self.cp_id}] CENTRAL connection lost: {e}")

    def _listen_monitor(self):
        """Listen for health checks from monitor via socket"""
        try:
            while self.running and self.monitor_socket:
                data = self.monitor_socket.recv(4096)
                if not data:
                    break

                message, is_valid = Protocol.decode(data)
                if is_valid:
                    fields = Protocol.parse_message(message)

                    if fields[0] == MessageTypes.HEALTH_CHECK:
                        # Respond to health check
                        if self.simulate_fault:
                            response = Protocol.encode(
                                Protocol.build_message(
                                    MessageTypes.HEALTH_KO, self.cp_id
                                )
                            )
                        else:
                            response = Protocol.encode(
                                Protocol.build_message(
                                    MessageTypes.HEALTH_OK, self.cp_id
                                )
                            )
                        self.monitor_socket.send(response)

        except Exception as e:
            print(f"[{self.cp_id}] Monitor listener error: {e}")

    def _handle_authorization(self, fields):
        """Handle charging authorization from CENTRAL"""
        # AUTHORIZE#driver_id#cp_id#kwh_needed#price
        driver_id = fields[1]
        kwh_needed = float(fields[3]) if len(fields) > 3 else 10

        with self.lock:
            if self.state == CP_STATES["ACTIVATED"]:
                self.current_driver = driver_id
                self.state = CP_STATES["SUPPLYING"]
                self.current_session = {
                    "driver_id": driver_id,
                    "start_time": time.time(),
                    "kwh_needed": kwh_needed,
                    "kwh_delivered": 0,
                    "amount": 0
                }

                print(f"[{self.cp_id}] Charging authorized for {driver_id}")

    # ✅ FIX #5: Handle STOP command from CENTRAL
    def _handle_stop_command(self):
        """Handle STOP command from CENTRAL"""
        with self.lock:
            # If currently charging, end the session
            if self.state == CP_STATES["SUPPLYING"] and self.current_session:
                driver_id = self.current_driver
                session = self.current_session
                kwh_delivered = session["kwh_delivered"]
                total_amount = round(kwh_delivered * self.price_per_kwh, 2)
                
                # Notify CENTRAL of supply end
                end_msg = Protocol.encode(
                    Protocol.build_message(
                        MessageTypes.SUPPLY_END, self.cp_id, driver_id,
                        kwh_delivered, total_amount
                    )
                )
                try:
                    self.central_socket.send(end_msg)
                except Exception as e:
                    print(f"[{self.cp_id}] Error notifying supply end: {e}")
                
                self.current_driver = None
                self.current_session = None
            
            self.state = CP_STATES["STOPPED"]
        
        print(f"[{self.cp_id}] Received STOP command from CENTRAL - now stopped")

    # ✅ FIX #5: Handle RESUME command from CENTRAL
    def _handle_resume_command(self):
        """Handle RESUME command from CENTRAL"""
        with self.lock:
            self.state = CP_STATES["ACTIVATED"]
        print(f"[{self.cp_id}] Received RESUME command from CENTRAL - now activated")

    def start_charging(self, driver_id):
        """
        Simulate driver plugging vehicle into CP
        Called via menu or external trigger
        """
        with self.lock:
            if self.state == CP_STATES["SUPPLYING"] and self.current_driver == driver_id:
                print(f"[{self.cp_id}] Driver {driver_id} plugged in, starting supply")
                return True
        return False

    def stop_charging(self):
        """Simulate driver unplugging vehicle from CP"""
        with self.lock:
            if self.state == CP_STATES["SUPPLYING"]:
                driver_id = self.current_driver
                session = self.current_session

                # Calculate final amount
                kwh_delivered = session["kwh_delivered"]
                total_amount = round(kwh_delivered * self.price_per_kwh, 2)

                print(f"[{self.cp_id}] Supply ended for {driver_id}")

                # Notify CENTRAL
                end_msg = Protocol.encode(
                    Protocol.build_message(
                        MessageTypes.SUPPLY_END, self.cp_id, driver_id,
                        kwh_delivered, total_amount
                    )
                )
                self.central_socket.send(end_msg)

                self.state = CP_STATES["ACTIVATED"]
                self.current_driver = None
                self.current_session = None

                return True

        return False

    def send_status_updates(self):
        """Send status updates to CENTRAL every second"""
        while self.running:
            time.sleep(SUPPLY_UPDATE_INTERVAL)

            try:
                with self.lock:
                    # Always send state
                    heartbeat = Protocol.encode(
                        Protocol.build_message(
                            MessageTypes.HEARTBEAT, self.cp_id, self.state
                        )
                    )
                    self.central_socket.send(heartbeat)

                    # If charging, send detailed update
                    if self.state == CP_STATES["SUPPLYING"] and self.current_session:
                        session = self.current_session
                        elapsed = time.time() - session["start_time"]

                        # Simulate charging (10 kW power)
                        power_kw = 10
                        kwh_this_second = power_kw / 3600  # 10 kW for 1 second
                        session["kwh_delivered"] += kwh_this_second

                        # Stop if reached target
                        if session["kwh_delivered"] >= session["kwh_needed"]:
                            session["kwh_delivered"] = session["kwh_needed"]
                            print(f"[{self.cp_id}] Target reached, auto-stopping...")
                            self.stop_charging()

                        amount = round(session["kwh_delivered"] * self.price_per_kwh, 2)

                        update_msg = Protocol.encode(
                            Protocol.build_message(
                                MessageTypes.SUPPLY_UPDATE, self.cp_id,
                                round(power_kw, 2), amount
                            )
                        )
                        self.central_socket.send(update_msg)

                        print(f"[{self.cp_id}] Charging: {session['kwh_delivered']:.2f} kWh, "
                              f"{amount}€")

            except Exception as e:
                print(f"[{self.cp_id}] Error sending status: {e}")

    def display_menu(self):
        """Display interactive menu for CP operations"""
        while self.running:
            try:
                print(f"\n[{self.cp_id} MENU]")
                print(f"Current State: {self.state}")

                if self.state == CP_STATES["SUPPLYING"]:
                    print(f"Charging: {self.current_driver}")
                    print("1. Stop charging (simulate unplug)")
                    choice = input("Choice: ").strip()
                    if choice == "1":
                        self.stop_charging()

                elif self.state == CP_STATES["ACTIVATED"]:
                    print("CP is ready and waiting")
                    print("(Waiting for driver requests...)")
                    time.sleep(2)

                else:
                    print("CP is in state:", self.state)
                    time.sleep(2)

                if input("Simulate fault? (y/n): ").strip().lower() == 'y':
                    self.simulate_fault = True
                    print(f"[{self.cp_id}] Fault simulated - monitor will detect")
                    time.sleep(3)
                    self.simulate_fault = False
                    print(f"[{self.cp_id}] Fault cleared")

            except Exception as e:
                print(f"Menu error: {e}")

    def run(self):
        """Run the CP engine"""
        if not self.connect_to_central():
            print(f"[{self.cp_id}] Cannot connect to CENTRAL")
            return

        # Start monitor listener
        monitor_thread = threading.Thread(
            target=self.listen_for_monitor,
            daemon=True
        )
        monitor_thread.start()

        # Start status updater
        updater_thread = threading.Thread(
            target=self.send_status_updates,
            daemon=True
        )
        updater_thread.start()

        # Run menu
        try:
            self.display_menu()
        except KeyboardInterrupt:
            print(f"\n[{self.cp_id}] Shutting down...")
        finally:
            self.running = False
            if self.central_socket:
                self.central_socket.close()
            if self.monitor_socket:
                self.monitor_socket.close()
            self.kafka.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ev_cp_engine.py <CP_ID> [latitude] [longitude] [price] [central_host] [central_port]")
        sys.exit(1)

    cp_id = sys.argv[1]
    latitude = sys.argv[2] if len(sys.argv) > 2 else "40.5"
    longitude = sys.argv[3] if len(sys.argv) > 3 else "-3.1"
    price = sys.argv[4] if len(sys.argv) > 4 else "0.30"
    central_host = sys.argv[5] if len(sys.argv) > 5 else "localhost"
    central_port = int(sys.argv[6]) if len(sys.argv) > 6 else 5000

    engine = EVCPEngine(cp_id, latitude, longitude, price, central_host, central_port)
    engine.run()