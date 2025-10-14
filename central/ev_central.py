# ============================================================================
# EVCharging System - EV_Central (Control Center) - COMPLETE FINAL VERSION
# ============================================================================

import socket
import threading
import time
import sys
import json
from datetime import datetime
from config import (
    CENTRAL_HOST, CENTRAL_PORT, CP_STATES, COLORS, KAFKA_TOPICS
)
from shared.protocol import Protocol, MessageTypes
from shared.kafka_client import KafkaClient


class EVCentral:
    def __init__(self, host=CENTRAL_HOST, port=CENTRAL_PORT):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = True

        # Data storage (in-memory)
        self.charging_points = {}     # {cp_id: {state, location, price, etc}}
        self.drivers = {}             # {driver_id: {status, current_cp, etc}}
        self.active_connections = {}  # {connection_id: socket}

        # ✅ FIX #1: Map entity IDs to their socket connections
        self.entity_to_socket = {}    # {entity_id: socket}

        # Kafka client
        self.kafka = KafkaClient("EV_Central")

        # Lock for thread safety
        self.lock = threading.Lock()

        print("[EV_Central] Initializing...")

    def start(self):
        """Start the central system"""
        print(f"[EV_Central] Starting on {self.host}:{self.port}")

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)

        print(f"[EV_Central] Listening on port {self.port}")

        try:
            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    client_id = f"{client_address[0]}:{client_address[1]}"
                    self.active_connections[client_id] = client_socket

                    thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, client_id),
                        daemon=True
                    )
                    thread.start()

                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        print(f"[EV_Central] Accept error: {e}")

        except KeyboardInterrupt:
            print("[EV_Central] Shutting down...")
        finally:
            self.shutdown()

    def _handle_client(self, client_socket, client_id):
        """Handle individual client connection"""
        print(f"[EV_Central] Client connected: {client_id}")

        try:
            buffer = b''
            while self.running:
                data = client_socket.recv(4096)
                if not data:
                    break

                buffer += data

                # Try to decode messages (may have multiple messages)
                while len(buffer) > 0:
                    message, is_valid = Protocol.decode(buffer)

                    if is_valid:
                        # Remove processed message from buffer
                        etx_pos = buffer.find(b'\x03')
                        buffer = buffer[etx_pos + 2:]  # ETX + LRC

                        # Handle message
                        self._process_message(message, client_socket, client_id)
                    else:
                        break

        except Exception as e:
            print(f"[EV_Central] Error handling {client_id}: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
            if client_id in self.active_connections:
                del self.active_connections[client_id]
            print(f"[EV_Central] Client disconnected: {client_id}")

    def _process_message(self, message, client_socket, client_id):
        """Process incoming message"""
        fields = Protocol.parse_message(message)
        msg_type = fields[0]

        if msg_type == MessageTypes.REGISTER:
            self._handle_register(fields, client_socket, client_id)

        elif msg_type == MessageTypes.HEARTBEAT:
            self._handle_heartbeat(fields, client_socket, client_id)

        elif msg_type == MessageTypes.REQUEST_CHARGE:
            self._handle_charge_request(fields, client_socket, client_id)

        elif msg_type == MessageTypes.SUPPLY_UPDATE:
            self._handle_supply_update(fields, client_socket)

        elif msg_type == MessageTypes.SUPPLY_END:
            self._handle_supply_end(fields, client_socket)

        elif msg_type == MessageTypes.FAULT:
            self._handle_fault(fields, client_socket)

        elif msg_type == MessageTypes.RECOVERY:
            self._handle_recovery(fields, client_socket)

        else:
            print(f"[EV_Central] Unknown message type: {msg_type}")

    def _handle_register(self, fields, client_socket, client_id):
        """Handle CP or Driver registration"""
        # REGISTER#entity_type#entity_id#extra_data...
        if len(fields) < 3:
            return

        entity_type = fields[1]
        entity_id = fields[2]

        if entity_type == "CP":
            # CP registration: REGISTER#CP#cp_id#latitude#longitude#price_per_kwh
            lat = fields[3] if len(fields) > 3 else "0"
            lon = fields[4] if len(fields) > 4 else "0"
            price = fields[5] if len(fields) > 5 else "0.30"

            with self.lock:
                self.charging_points[entity_id] = {
                    "state": CP_STATES["ACTIVATED"],
                    "location": (lat, lon),
                    "price_per_kwh": price,
                    "connected_at": datetime.now().isoformat(),
                    "current_driver": None,
                    "consumption_kw": 0,
                    "amount_euro": 0
                }
                
                # ✅ FIX #1: Map CP ID to socket for direct communication
                self.entity_to_socket[entity_id] = client_socket

            print(f"[EV_Central] CP Registered: {entity_id} at ({lat}, {lon})")
            self.kafka.publish_event("system_events", "CP_REGISTERED", {
                "cp_id": entity_id,
                "location": (lat, lon),
                "price": price
            })

            # Send acknowledgement
            response = Protocol.encode(
                Protocol.build_message(MessageTypes.ACKNOWLEDGE, entity_id, "OK")
            )
            client_socket.send(response)

        elif entity_type == "DRIVER":
            # Driver registration: REGISTER#DRIVER#driver_id
            with self.lock:
                self.drivers[entity_id] = {
                    "status": "IDLE",
                    "current_cp": None,
                    "charge_amount": 0
                }
                
                # ✅ FIX #1: Map driver ID to socket for direct communication
                self.entity_to_socket[entity_id] = client_socket

            print(f"[EV_Central] Driver Registered: {entity_id}")
            response = Protocol.encode(
                Protocol.build_message(MessageTypes.ACKNOWLEDGE, entity_id, "OK")
            )
            client_socket.send(response)

    def _handle_charge_request(self, fields, client_socket, client_id):
        """Handle driver charging request"""
        # REQUEST_CHARGE#driver_id#cp_id#kwh_needed
        if len(fields) < 4:
            return

        driver_id = fields[1]
        cp_id = fields[2]
        kwh_needed = fields[3]

        with self.lock:
            # Validate CP exists and is available
            if cp_id not in self.charging_points:
                response = Protocol.encode(
                    Protocol.build_message(MessageTypes.DENY, driver_id, cp_id,
                                         "CP_NOT_FOUND")
                )
                client_socket.send(response)
                return

            cp = self.charging_points[cp_id]

            if cp["state"] != CP_STATES["ACTIVATED"]:
                response = Protocol.encode(
                    Protocol.build_message(MessageTypes.DENY, driver_id, cp_id,
                                         f"CP_STATE_{cp['state']}")
                )
                client_socket.send(response)
                return

            # Authorization granted
            cp["state"] = CP_STATES["SUPPLYING"]
            cp["current_driver"] = driver_id
            self.drivers[driver_id]["status"] = "CHARGING"
            self.drivers[driver_id]["current_cp"] = cp_id

        print(f"[EV_Central] Charge authorized: Driver {driver_id} -> CP {cp_id}")

        # Send authorization to driver
        response = Protocol.encode(
            Protocol.build_message(MessageTypes.AUTHORIZE, driver_id, cp_id,
                                 kwh_needed, cp["price_per_kwh"])
        )
        client_socket.send(response)

        self.kafka.publish_event("charging_logs", "CHARGE_AUTHORIZED", {
            "driver_id": driver_id,
            "cp_id": cp_id,
            "kwh_needed": kwh_needed
        })

    def _handle_supply_update(self, fields, client_socket):
        """Handle real-time supply updates from CP"""
        # SUPPLY_UPDATE#cp_id#consumption_kw#amount_euro
        if len(fields) < 4:
            return

        cp_id = fields[1]
        consumption = float(fields[2])
        amount = float(fields[3])

        driver_id = None
        with self.lock:
            if cp_id in self.charging_points:
                self.charging_points[cp_id]["consumption_kw"] = consumption
                self.charging_points[cp_id]["amount_euro"] = amount
                
                # ✅ FIX #2: Get driver being served
                driver_id = self.charging_points[cp_id]["current_driver"]

        # ✅ FIX #2: Forward update to driver's connection so they see real-time updates
        if driver_id and driver_id in self.entity_to_socket:
            try:
                update_msg = Protocol.encode(
                    Protocol.build_message(
                        MessageTypes.SUPPLY_UPDATE, cp_id, consumption, amount
                    )
                )
                self.entity_to_socket[driver_id].send(update_msg)
            except Exception as e:
                print(f"[EV_Central] Failed to forward update to {driver_id}: {e}")

    def _handle_supply_end(self, fields, client_socket):
        """Handle supply completion"""
        # SUPPLY_END#cp_id#driver_id#total_kwh#total_amount
        if len(fields) < 5:
            return

        cp_id = fields[1]
        driver_id = fields[2]
        total_kwh = float(fields[3])
        total_amount = float(fields[4])

        with self.lock:
            if cp_id in self.charging_points:
                cp = self.charging_points[cp_id]
                cp["state"] = CP_STATES["ACTIVATED"]
                cp["current_driver"] = None
                cp["consumption_kw"] = 0
                cp["amount_euro"] = 0

            if driver_id in self.drivers:
                self.drivers[driver_id]["status"] = "IDLE"
                self.drivers[driver_id]["current_cp"] = None

        ticket = f"=== TICKET ===\nCP: {cp_id}\nTotal: {total_amount}€\nkWh: {total_kwh}"
        print(f"[EV_Central] Supply ended: {driver_id} at {cp_id}\n{ticket}")

        # ✅ FIX #3: Send ticket to driver using tracked socket
        if driver_id in self.entity_to_socket:
            try:
                ticket_msg = Protocol.encode(
                    Protocol.build_message(
                        MessageTypes.TICKET, cp_id, total_kwh, total_amount
                    )
                )
                self.entity_to_socket[driver_id].send(ticket_msg)
                print(f"[EV_Central] Ticket sent to {driver_id}")
            except Exception as e:
                print(f"[EV_Central] Failed to send ticket to {driver_id}: {e}")

        self.kafka.publish_event("charging_logs", "CHARGE_COMPLETED", {
            "cp_id": cp_id,
            "driver_id": driver_id,
            "total_kwh": total_kwh,
            "total_amount": total_amount
        })

    def _handle_heartbeat(self, fields, client_socket, client_id):
        """Handle heartbeat from CP"""
        # HEARTBEAT#cp_id#state
        if len(fields) < 3:
            return

        cp_id = fields[1]
        state = fields[2]

        with self.lock:
            if cp_id in self.charging_points:
                self.charging_points[cp_id]["state"] = state

    def _handle_fault(self, fields, client_socket):
        """Handle fault notification from CP monitor"""
        if len(fields) < 2:
            return

        cp_id = fields[1]

        with self.lock:
            if cp_id in self.charging_points:
                self.charging_points[cp_id]["state"] = CP_STATES["OUT_OF_ORDER"]

        print(f"[EV_Central] FAULT reported for CP {cp_id}")
        self.kafka.publish_event("system_events", "CP_FAULT", {"cp_id": cp_id})

    def _handle_recovery(self, fields, client_socket):
        """Handle recovery notification from CP monitor"""
        if len(fields) < 2:
            return

        cp_id = fields[1]

        with self.lock:
            if cp_id in self.charging_points:
                self.charging_points[cp_id]["state"] = CP_STATES["ACTIVATED"]

        print(f"[EV_Central] CP {cp_id} recovered")
        self.kafka.publish_event("system_events", "CP_RECOVERED", {"cp_id": cp_id})

    def display_dashboard(self):
        """Display monitoring dashboard periodically"""
        while self.running:
            time.sleep(2)

            with self.lock:
                print("\n" + "="*80)
                print("EV_CENTRAL MONITORING DASHBOARD")
                print("="*80)

                print("\n[CHARGING POINTS]")
                if not self.charging_points:
                    print("  No charging points registered")
                else:
                    for cp_id, cp_data in self.charging_points.items():
                        color = COLORS.get(cp_data["state"], "?")
                        print(f"  [{color}] {cp_id}: {cp_data['state']}")
                        if cp_data["state"] == CP_STATES["SUPPLYING"]:
                            print(f"      Driver: {cp_data['current_driver']}")
                            print(f"      Power: {cp_data['consumption_kw']} kW")
                            print(f"      Amount: {cp_data['amount_euro']}€")

                print("\n[DRIVERS]")
                if not self.drivers:
                    print("  No drivers registered")
                else:
                    for driver_id, driver_data in self.drivers.items():
                        print(f"  {driver_id}: {driver_data['status']}")
                        if driver_data['status'] == "CHARGING":
                            print(f"      At: {driver_data['current_cp']}")

                print("="*80 + "\n")

    def handle_admin_commands(self):
        """Handle admin commands for stop/resume CPs"""
        while self.running:
            try:
                cmd = input("\n[ADMIN] Enter command (stop <CP_ID>, resume <CP_ID>, or 'help'): ").strip()

                if cmd == "help":
                    print("Commands: stop <CP_ID>, resume <CP_ID>, list, quit")
                    continue

                if cmd == "list":
                    with self.lock:
                        for cp_id in self.charging_points:
                            print(f"  - {cp_id}")
                    continue

                if cmd == "quit":
                    self.running = False
                    break

                if cmd.startswith("stop"):
                    cp_id = cmd.split()[1] if len(cmd.split()) > 1 else None
                    if cp_id and cp_id in self.entity_to_socket:
                        with self.lock:
                            if cp_id in self.charging_points:
                                self.charging_points[cp_id]["state"] = CP_STATES["STOPPED"]
                        
                        # ✅ FIX #4: Send STOP command to CP Engine via socket
                        try:
                            stop_msg = Protocol.encode(
                                Protocol.build_message(MessageTypes.STOP_COMMAND, cp_id)
                            )
                            self.entity_to_socket[cp_id].send(stop_msg)
                            print(f"CP {cp_id} stopped")
                        except Exception as e:
                            print(f"Failed to stop CP: {e}")
                    else:
                        print(f"CP {cp_id} not found or not connected")

                if cmd.startswith("resume"):
                    cp_id = cmd.split()[1] if len(cmd.split()) > 1 else None
                    if cp_id and cp_id in self.entity_to_socket:
                        with self.lock:
                            if cp_id in self.charging_points:
                                self.charging_points[cp_id]["state"] = CP_STATES["ACTIVATED"]
                        
                        # ✅ FIX #4: Send RESUME command to CP Engine via socket
                        try:
                            resume_msg = Protocol.encode(
                                Protocol.build_message(MessageTypes.RESUME_COMMAND, cp_id)
                            )
                            self.entity_to_socket[cp_id].send(resume_msg)
                            print(f"CP {cp_id} resumed")
                        except Exception as e:
                            print(f"Failed to resume CP: {e}")
                    else:
                        print(f"CP {cp_id} not found or not connected")

            except Exception as e:
                print(f"Command error: {e}")

    def shutdown(self):
        """Shutdown the central system"""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        self.kafka.close()
        print("[EV_Central] Shutdown complete")


if __name__ == "__main__":
    central = EVCentral()

    # Start server in separate thread
    server_thread = threading.Thread(target=central.start, daemon=True)
    server_thread.start()

    # Start dashboard in separate thread
    dashboard_thread = threading.Thread(target=central.display_dashboard, daemon=True)
    dashboard_thread.start()

    # Start admin console
    try:
        central.handle_admin_commands()
    except KeyboardInterrupt:
        print("\n[EV_Central] Shutting down...")
        central.shutdown()