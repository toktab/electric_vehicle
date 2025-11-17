# ============================================================================
# EVCharging System - EV_Central (Control Center) - FIXED kWh TRACKING
# ============================================================================

import socket
import threading
import time
import sys
from datetime import datetime
from config import (
    CENTRAL_HOST, CENTRAL_PORT, CP_STATES, COLORS
)
from shared.protocol import Protocol, MessageTypes
from shared.kafka_client import KafkaClient
from shared.file_storage import FileStorage


class EVCentral:
    def __init__(self, host=CENTRAL_HOST, port=CENTRAL_PORT):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = True

        # File storage instead of database
        self.storage = FileStorage("data")

        # Runtime data (in-memory for active sessions)
        self.charging_points = {}     # {cp_id: {state, location, price, current_driver, etc}}
        self.drivers = {}             # {driver_id: {status, current_cp, etc}}
        self.active_connections = {}  # {connection_id: socket}
        self.entity_to_socket = {}    # {entity_id: socket}
        self.monitors = {}            # {cp_id: monitor_socket}

        # Kafka client
        self.kafka = KafkaClient("EV_Central")

        # Lock for thread safety
        self.lock = threading.Lock()

        print("[EV_Central] Initializing with file storage...")
        
        # Load existing CPs from file on startup
        self._load_stored_cps()

    def _load_stored_cps(self):
        """Load charging points from file on startup"""
        stored_cps = self.storage.get_all_cps()
        
        if stored_cps:
            print(f"[EV_Central] Loading {len(stored_cps)} charging points from storage...")
            
            for cp_data in stored_cps:
                cp_id = cp_data['cp_id']
                self.charging_points[cp_id] = {
                    "state": CP_STATES["DISCONNECTED"],
                    "location": (cp_data['latitude'], cp_data['longitude']),
                    "price_per_kwh": cp_data['price_per_kwh'],
                    "current_driver": None,
                    "kwh_delivered": 0,  # ✅ FIXED: Track actual kWh delivered
                    "amount_euro": 0,
                    "session_start": None
                }
                print(f"  - {cp_id} at ({cp_data['latitude']}, {cp_data['longitude']})")
        else:
            print("[EV_Central] No stored charging points found")

    def start(self):
        """Start the central system"""
        print(f"[EV_Central] Starting on {self.host}:{self.port}")

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)

        print(f"[EV_Central] Listening on port {self.port}")
        print(f"[EV_Central] File storage location: data/")

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

                while len(buffer) > 0:
                    message, is_valid = Protocol.decode(buffer)

                    if is_valid:
                        etx_pos = buffer.find(b'\x03')
                        buffer = buffer[etx_pos + 2:]
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
        elif msg_type == MessageTypes.QUERY_AVAILABLE_CPS:
            self._handle_query_available_cps(fields, client_socket, client_id)
        elif msg_type == MessageTypes.SUPPLY_UPDATE:
            self._handle_supply_update(fields, client_socket)
        elif msg_type == MessageTypes.SUPPLY_END:
            self._handle_supply_end(fields, client_socket)
        elif msg_type == MessageTypes.END_CHARGE:
            self._handle_end_charge(fields, client_socket, client_id)
        elif msg_type == MessageTypes.FAULT:
            self._handle_fault(fields, client_socket)
        elif msg_type == MessageTypes.RECOVERY:
            self._handle_recovery(fields, client_socket)

    def _handle_register(self, fields, client_socket, client_id):
        """Handle CP or Driver registration"""
        if len(fields) < 3:
            return

        entity_type = fields[1]
        entity_id = fields[2]

        if entity_type == "CP":
            lat = fields[3] if len(fields) > 3 else "0"
            lon = fields[4] if len(fields) > 4 else "0"
            price = float(fields[5]) if len(fields) > 5 else 0.30

            with self.lock:
                self.charging_points[entity_id] = {
                    "state": CP_STATES["ACTIVATED"],
                    "location": (lat, lon),
                    "price_per_kwh": price,
                    "connected_at": datetime.now().isoformat(),
                    "current_driver": None,
                    "kwh_delivered": 0,  # ✅ FIXED
                    "amount_euro": 0,
                    "session_start": None
                }
                
                self.entity_to_socket[entity_id] = client_socket

            self.storage.save_cp(entity_id, lat, lon, price, CP_STATES["ACTIVATED"])

            print(f"[EV_Central] ✅ CP Registered: {entity_id} at ({lat}, {lon}) - Saved to file")
            
            self.kafka.publish_event("system_events", "CP_REGISTERED", {
                "cp_id": entity_id,
                "location": (lat, lon),
                "price": price
            })

            response = Protocol.encode(
                Protocol.build_message(MessageTypes.ACKNOWLEDGE, entity_id, "OK")
            )
            client_socket.send(response)

        elif entity_type == "DRIVER":
            with self.lock:
                self.drivers[entity_id] = {
                    "status": "IDLE",
                    "current_cp": None,
                    "charge_amount": 0
                }
                
                self.entity_to_socket[entity_id] = client_socket

            self.storage.save_driver(entity_id, "IDLE")

            print(f"[EV_Central] ✅ Driver Registered: {entity_id} - Saved to file")
            
            response = Protocol.encode(
                Protocol.build_message(MessageTypes.ACKNOWLEDGE, entity_id, "OK")
            )
            client_socket.send(response)

        elif entity_type == "MONITOR":
        # ⭐ NEW: Register monitor
            monitor_cp_id = fields[3] if len(fields) > 3 else None
        
            if monitor_cp_id:
                with self.lock:
                    self.monitors[monitor_cp_id] = client_socket
                
                print(f"[EV_Central] ✅ Monitor Registered for {monitor_cp_id}")
                
                response = Protocol.encode(
                    Protocol.build_message(MessageTypes.ACKNOWLEDGE, monitor_cp_id, "MONITOR_OK")
                )
                client_socket.send(response)

    def _handle_charge_request(self, fields, client_socket, client_id):
        """Handle driver charging request"""
        if len(fields) < 4:
            print(f"[EV_Central] ⚠️  Invalid REQUEST_CHARGE: {fields}")
            return

        driver_id = fields[1]
        cp_id = fields[2]
        kwh_needed = float(fields[3])

        print(f"[EV_Central] 🔌 {driver_id} requesting {kwh_needed} kWh at {cp_id}")

        with self.lock:
            if cp_id not in self.charging_points:
                response = Protocol.encode(
                    Protocol.build_message(MessageTypes.DENY, driver_id, cp_id, "CP_NOT_FOUND")
                )
                client_socket.send(response)
                print(f"[EV_Central] ❌ Denied: CP not found")
                return

            cp = self.charging_points[cp_id]

            if cp["state"] != CP_STATES["ACTIVATED"] or cp["current_driver"] is not None:
                reason = f"CP_STATE_{cp['state']}" if cp["state"] != CP_STATES["ACTIVATED"] else "CP_ALREADY_IN_USE"
                response = Protocol.encode(
                    Protocol.build_message(MessageTypes.DENY, driver_id, cp_id, reason)
                )
                client_socket.send(response)
                print(f"[EV_Central] ❌ Denied: {reason}")
                return

            # Authorization granted
            cp["state"] = CP_STATES["SUPPLYING"]
            cp["current_driver"] = driver_id
            cp["session_start"] = time.time()
            cp["kwh_delivered"] = 0
            cp["amount_euro"] = 0
            cp["kwh_needed"] = kwh_needed
            
            self.drivers[driver_id]["status"] = "CHARGING"
            self.drivers[driver_id]["current_cp"] = cp_id

        print(f"[EV_Central] ✅ Charge authorized: Driver {driver_id} → CP {cp_id}")

        # ⭐ FIX: Send AUTHORIZE to BOTH driver AND CP Engine
        
        # 1. Send to driver
        driver_auth_msg = Protocol.encode(
            Protocol.build_message(MessageTypes.AUTHORIZE, driver_id, cp_id, kwh_needed, cp["price_per_kwh"])
        )
        try:
            client_socket.send(driver_auth_msg)
            print(f"[EV_Central] 📤 Sent AUTHORIZE to driver {driver_id}")
        except Exception as e:
            print(f"[EV_Central] ⚠️  Failed to send AUTHORIZE to driver: {e}")

        # 2. ⭐ NEW: Send to CP Engine
        if cp_id in self.entity_to_socket:
            cp_auth_msg = Protocol.encode(
                Protocol.build_message(MessageTypes.AUTHORIZE, driver_id, cp_id, kwh_needed)
            )
            try:
                self.entity_to_socket[cp_id].send(cp_auth_msg)
                print(f"[EV_Central] 📤 Sent AUTHORIZE to CP {cp_id}")
            except Exception as e:
                print(f"[EV_Central] ⚠️  Failed to send AUTHORIZE to CP: {e}")
        else:
            print(f"[EV_Central] ⚠️  CP {cp_id} not found in entity_to_socket mapping")

        # 3. Notify monitor
        if cp_id in self.monitors:
            try:
                monitor_notify = Protocol.encode(
                    Protocol.build_message("DRIVER_START", cp_id, driver_id)
                )
                self.monitors[cp_id].send(monitor_notify)
                print(f"[EV_Central] 📤 Notified monitor: {driver_id} started at {cp_id}")
            except Exception as e:
                print(f"[EV_Central] Failed to notify monitor: {e}")

        self.kafka.publish_event("charging_logs", "CHARGE_AUTHORIZED", {
            "driver_id": driver_id,
            "cp_id": cp_id,
            "kwh_needed": kwh_needed
        })

    def _handle_supply_update(self, fields, client_socket):
        """Handle real-time supply updates from CP"""
        if len(fields) < 4:
            return

        cp_id = fields[1]
        kwh_increment = float(fields[2])  # ✅ FIXED: This is kWh added this second
        amount = float(fields[3])

        driver_id = None
        with self.lock:
            if cp_id in self.charging_points:
                cp = self.charging_points[cp_id]
                cp["kwh_delivered"] += kwh_increment  # ✅ FIXED: Accumulate kWh
                cp["amount_euro"] = cp["kwh_delivered"] * cp["price_per_kwh"]  # ✅ FIXED: Calculate accurately
                driver_id = cp["current_driver"]

        # Forward update to driver
        if driver_id and driver_id in self.entity_to_socket:
            try:
                update_msg = Protocol.encode(
                    Protocol.build_message(MessageTypes.SUPPLY_UPDATE, cp_id, kwh_increment, cp["amount_euro"])
                )
                self.entity_to_socket[driver_id].send(update_msg)
            except Exception as e:
                print(f"[EV_Central] Failed to forward update to {driver_id}: {e}")

    def _handle_supply_end(self, fields, client_socket):
        """Handle supply completion from CP"""
        if len(fields) < 5:
            return

        cp_id = fields[1]
        driver_id = fields[2]
        total_kwh = float(fields[3])
        total_amount = float(fields[4])

        duration_seconds = 0
        
        with self.lock:
            if cp_id in self.charging_points:
                cp = self.charging_points[cp_id]
                
                if cp["session_start"]:
                    duration_seconds = int(time.time() - cp["session_start"])
                
                cp["state"] = CP_STATES["ACTIVATED"]
                cp["current_driver"] = None
                cp["kwh_delivered"] = 0  # ✅ FIXED: Reset
                cp["amount_euro"] = 0
                cp["session_start"] = None

            if driver_id in self.drivers:
                self.drivers[driver_id]["status"] = "IDLE"
                self.drivers[driver_id]["current_cp"] = None

        self.storage.save_charging_session(cp_id, driver_id, total_kwh, total_amount, duration_seconds)
        self.storage.update_driver_stats(driver_id, total_amount)

        ticket = f"=== CHARGING COMPLETED ===\nCP: {cp_id}\nDriver: {driver_id}\nEnergy: {total_kwh} kWh\nCost: {total_amount}€\nDuration: {duration_seconds}s"
        print(f"[EV_Central] {ticket}")
        print(f"[EV_Central] ✅ Session saved to charging_history.txt")

        if driver_id in self.entity_to_socket:
            try:
                ticket_msg = Protocol.encode(
                    Protocol.build_message(MessageTypes.TICKET, cp_id, total_kwh, total_amount)
                )
                self.entity_to_socket[driver_id].send(ticket_msg)
            except Exception as e:
                print(f"[EV_Central] Failed to send ticket to {driver_id}: {e}")

        self.kafka.publish_event("charging_logs", "CHARGE_COMPLETED", {
            "cp_id": cp_id,
            "driver_id": driver_id,
            "total_kwh": total_kwh,
            "total_amount": total_amount
        })
        if cp_id in self.monitors:
            try:
                monitor_notify = Protocol.encode(
                    Protocol.build_message("DRIVER_STOP", cp_id, driver_id)
                )
                self.monitors[cp_id].send(monitor_notify)
                print(f"[EV_Central] 📤 Notified monitor: {driver_id} finished at {cp_id}")
            except Exception as e:
                print(f"[EV_Central] Failed to notify monitor: {e}")

        self.kafka.publish_event("charging_logs", "CHARGE_COMPLETED", {
            "cp_id": cp_id,
            "driver_id": driver_id,
            "total_kwh": total_kwh,
            "total_amount": total_amount
        })

    def _handle_end_charge(self, fields, client_socket, client_id):
        """Handle manual end charge from driver"""
        if len(fields) < 3:
            return

        driver_id = fields[1]
        cp_id = fields[2]

        print(f"[EV_Central] 🔌 Driver {driver_id} manually ending charge at {cp_id}")

        total_kwh = 0
        total_amount = 0
        duration_seconds = 0

        with self.lock:
            if cp_id not in self.charging_points:
                print(f"[EV_Central] ❌ CP {cp_id} not found")
                return
            
            cp = self.charging_points[cp_id]
            
            if cp["current_driver"] != driver_id:
                print(f"[EV_Central] ❌ Driver {driver_id} not charging at {cp_id}")
                return

            # ✅ FIXED: Calculate kWh based on actual time elapsed for accurate billing
            duration_seconds = int(time.time() - cp["session_start"]) if cp["session_start"] else 0
            total_seconds = 14.0  # Demo charging time
            kwh_needed = cp.get("kwh_needed", 10)  # Default to 10 if not set
            total_kwh = min(kwh_needed, (duration_seconds / total_seconds) * kwh_needed)
            total_amount = round(total_kwh * cp["price_per_kwh"], 2)

            # Update states immediately
            cp["state"] = CP_STATES["ACTIVATED"]
            cp["current_driver"] = None
            cp["kwh_delivered"] = 0
            cp["amount_euro"] = 0
            cp["session_start"] = None

            if driver_id in self.drivers:
                self.drivers[driver_id]["status"] = "IDLE"
                self.drivers[driver_id]["current_cp"] = None

        self.storage.save_charging_session(cp_id, driver_id, total_kwh, total_amount, duration_seconds)
        self.storage.update_driver_stats(driver_id, total_amount)

        print(f"[EV_Central] ✅ Manual end charge completed: {driver_id} @ {cp_id}")
        print(f"[EV_Central]    → {total_kwh:.2f} kWh, {total_amount:.2f}€, {duration_seconds}s")

        if cp_id in self.entity_to_socket:
            try:
                end_supply_msg = Protocol.encode(
                    Protocol.build_message(MessageTypes.END_SUPPLY, cp_id)
                )
                self.entity_to_socket[cp_id].send(end_supply_msg)
                print(f"[EV_Central] 📤 Sent END_SUPPLY to CP {cp_id}")
            except Exception as e:
                print(f"[EV_Central] ⚠️  Failed to send END_SUPPLY to {cp_id}: {e}")

        if driver_id in self.entity_to_socket:
            try:
                ticket_msg = Protocol.encode(
                    Protocol.build_message(MessageTypes.TICKET, cp_id, total_kwh, total_amount)
                )
                self.entity_to_socket[driver_id].send(ticket_msg)
                print(f"[EV_Central] 📤 Sent ticket to driver {driver_id}")
            except Exception as e:
                print(f"[EV_Central] ⚠️  Failed to send ticket to {driver_id}: {e}")

        if cp_id in self.monitors:
            try:
                monitor_notify = Protocol.encode(
                    Protocol.build_message("DRIVER_STOP", cp_id, driver_id)
                )
                self.monitors[cp_id].send(monitor_notify)
                print(f"[EV_Central] 📤 Notified monitor: {driver_id} stopped at {cp_id}")
            except Exception as e:
                print(f"[EV_Central] Failed to notify monitor: {e}")

        self.kafka.publish_event("charging_logs", "CHARGE_MANUALLY_ENDED", {
            "cp_id": cp_id,
            "driver_id": driver_id,
            "total_kwh": total_kwh,
            "total_amount": total_amount,
            "duration_seconds": duration_seconds
        })

    def _handle_heartbeat(self, fields, client_socket, client_id):
        """Handle heartbeat from CP"""
        if len(fields) < 3:
            return

        cp_id = fields[1]
        state = fields[2]

        with self.lock:
            if cp_id in self.charging_points:
                if self.charging_points[cp_id]["state"] != CP_STATES["SUPPLYING"]:
                    self.charging_points[cp_id]["state"] = state

    def _handle_fault(self, fields, client_socket):
        """Handle fault notification from CP monitor"""
        if len(fields) < 2:
            return

        cp_id = fields[1]

        driver_id = None
        was_supplying = False

        with self.lock:
            if cp_id in self.charging_points:
                cp = self.charging_points[cp_id]
                was_supplying = (cp["state"] == CP_STATES["SUPPLYING"])
                driver_id = cp["current_driver"]
                
                cp["state"] = CP_STATES["OUT_OF_ORDER"]
                
                if was_supplying and driver_id:
                    # ✅ FIXED: Use accumulated kWh
                    total_kwh = cp["kwh_delivered"]
                    total_amount = cp["amount_euro"]
                    duration_seconds = int(time.time() - cp["session_start"]) if cp["session_start"] else 0
                    
                    self.storage.save_charging_session(cp_id, driver_id, total_kwh, total_amount, duration_seconds)
                    self.storage.update_driver_stats(driver_id, total_amount)
                    
                    cp["current_driver"] = None
                    cp["kwh_delivered"] = 0
                    cp["amount_euro"] = 0
                    cp["session_start"] = None
                    
                    if driver_id in self.drivers:
                        self.drivers[driver_id]["status"] = "IDLE"
                        self.drivers[driver_id]["current_cp"] = None

        print(f"[EV_Central] ⚠️ FAULT reported for CP {cp_id}")
        
        if was_supplying and driver_id:
            print(f"[EV_Central] ⚠️  Charging session interrupted for driver {driver_id}")
            
            if driver_id in self.entity_to_socket:
                try:
                    fault_msg = Protocol.encode(
                        Protocol.build_message(MessageTypes.DENY, driver_id, cp_id, "CP_FAULT_EMERGENCY_STOP")
                    )
                    self.entity_to_socket[driver_id].send(fault_msg)
                except Exception as e:
                    print(f"[EV_Central] Failed to notify driver of fault: {e}")
        
        self.kafka.publish_event("system_events", "CP_FAULT", {"cp_id": cp_id})

    def _handle_recovery(self, fields, client_socket):
        """Handle recovery notification from CP monitor"""
        if len(fields) < 2:
            return

        cp_id = fields[1]

        with self.lock:
            if cp_id in self.charging_points:
                self.charging_points[cp_id]["state"] = CP_STATES["ACTIVATED"]

        print(f"[EV_Central] ✅ CP {cp_id} recovered")
        self.kafka.publish_event("system_events", "CP_RECOVERED", {"cp_id": cp_id})

    def _handle_query_available_cps(self, fields, client_socket, client_id):
        """Handle driver query for available CPs"""
        if len(fields) < 2:
            return

        driver_id = fields[1]

        available_cps = []
        with self.lock:
            for cp_id, cp_data in self.charging_points.items():
                if cp_data["state"] == CP_STATES["ACTIVATED"] and cp_data["current_driver"] is None:
                    available_cps.append({
                        "cp_id": cp_id,
                        "location": cp_data["location"],
                        "price_per_kwh": cp_data["price_per_kwh"]
                    })

        response_fields = [MessageTypes.AVAILABLE_CPS]
        for cp in available_cps:
            response_fields.extend([
                cp["cp_id"],
                cp["location"][0],
                cp["location"][1],
                cp["price_per_kwh"]
            ])

        response = Protocol.encode(Protocol.build_message(*response_fields))
        client_socket.send(response)

        print(f"[EV_Central] Sent {len(available_cps)} available CPs to {driver_id}")

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
                            print(f"      kWh: {cp_data['kwh_delivered']:.2f} kWh")  # ✅ FIXED
                            print(f"      Amount: {cp_data['amount_euro']:.2f}€")

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
                cmd = input("\n[ADMIN] Command (stop/resume <CP_ID>, list, history, quit): ").strip()

                if cmd == "help":
                    print("Commands:")
                    print("  stop <CP_ID>    - Stop a charging point")
                    print("  resume <CP_ID>  - Resume a charging point")
                    print("  list            - List all charging points")
                    print("  history         - Show recent charging history")
                    print("  quit            - Shutdown system")
                    continue

                if cmd == "list":
                    with self.lock:
                        print("\n=== CHARGING POINTS ===")
                        for cp_id, cp_data in self.charging_points.items():
                            print(f"  {cp_id}: {cp_data['state']}")
                            if cp_data["current_driver"]:
                                print(f"    └─ Charging: {cp_data['current_driver']}")
                    continue

                if cmd == "history":
                    history = self.storage.get_recent_history(10)
                    print("\n=== RECENT CHARGING HISTORY ===")
                    if not history:
                        print("  No history yet")
                    else:
                        for session in history:
                            print(f"  {session['timestamp'][:19]}: {session['driver_id']} @ {session['cp_id']}")
                            print(f"     → {session['kwh_delivered']} kWh, {session['total_amount']}€, {session['duration_seconds']}s")
                    continue

                if cmd == "quit":
                    print("Shutting down...")
                    self.running = False
                    break

                if cmd.startswith("stop"):
                    parts = cmd.split()
                    if len(parts) < 2:
                        print("❌ Usage: stop <CP_ID>")
                        continue
                    
                    cp_id = parts[1]
                    
                    driver_id = None
                    was_charging = False
                    
                    with self.lock:
                        if cp_id not in self.charging_points:
                            print(f"❌ CP {cp_id} not found")
                            continue
                        
                        cp = self.charging_points[cp_id]
                        was_charging = (cp["state"] == CP_STATES["SUPPLYING"])
                        driver_id = cp["current_driver"]
                        
                        if was_charging and driver_id:
                            # ✅ FIXED: Use accumulated kWh and calculate amount accurately
                            total_kwh = cp["kwh_delivered"]
                            total_amount = total_kwh * cp["price_per_kwh"]
                            duration_seconds = int(time.time() - cp["session_start"]) if cp["session_start"] else 0
                            
                            self.storage.save_charging_session(cp_id, driver_id, total_kwh, total_amount, duration_seconds)
                            self.storage.update_driver_stats(driver_id, total_amount)
                            
                            print(f"⚠️  Charging session at {cp_id} interrupted ({total_kwh:.2f} kWh, {total_amount:.2f}€)")
                            
                            cp["current_driver"] = None
                            cp["kwh_delivered"] = 0
                            cp["amount_euro"] = 0
                            cp["session_start"] = None
                            
                            if driver_id in self.drivers:
                                self.drivers[driver_id]["status"] = "IDLE"
                                self.drivers[driver_id]["current_cp"] = None
                        
                        cp["state"] = CP_STATES["STOPPED"]
                    
                    if cp_id in self.entity_to_socket:
                        try:
                            stop_msg = Protocol.encode(
                                Protocol.build_message(MessageTypes.STOP_COMMAND, cp_id)
                            )
                            self.entity_to_socket[cp_id].send(stop_msg)
                            print(f"✅ CP {cp_id} stopped")
                            
                            if was_charging and driver_id and driver_id in self.entity_to_socket:
                                ticket_msg = Protocol.encode(
                                    Protocol.build_message(MessageTypes.TICKET, cp_id, 
                                                          total_kwh, total_amount)
                                )
                                self.entity_to_socket[driver_id].send(ticket_msg)
                                print(f"📤 Ticket sent to driver {driver_id}")
                                
                        except Exception as e:
                            print(f"❌ Failed to stop CP: {e}")
                    else:
                        print(f"❌ CP {cp_id} not connected")
                    continue

                if cmd.startswith("resume"):
                    parts = cmd.split()
                    if len(parts) < 2:
                        print("❌ Usage: resume <CP_ID>")
                        continue
                    
                    cp_id = parts[1]
                    if cp_id in self.entity_to_socket:
                        with self.lock:
                            if cp_id in self.charging_points:
                                self.charging_points[cp_id]["state"] = CP_STATES["ACTIVATED"]
                        
                        try:
                            resume_msg = Protocol.encode(
                                Protocol.build_message(MessageTypes.RESUME_COMMAND, cp_id)
                            )
                            self.entity_to_socket[cp_id].send(resume_msg)
                            print(f"✅ CP {cp_id} resumed")
                        except Exception as e:
                            print(f"❌ Failed to resume CP: {e}")
                    else:
                        print(f"❌ CP {cp_id} not found or not connected")
                    continue

                print("❌ Unknown command. Type 'help' for commands.")

            except Exception as e:
                print(f"❌ Command error: {e}")

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