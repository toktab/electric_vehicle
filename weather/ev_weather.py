#!/usr/bin/env python3
"""
EV_W (Weather Control Office)
Monitors weather conditions and disables CPs when temperature <= 0°C
"""

import requests
import time
import json
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

OPENWEATHER_API_KEY = "0dd2fb3fbe091298b89e507c2a7acae4"
CENTRAL_API_URL = "http://central:8080/api/weather"
CHECK_INTERVAL = 4  # seconds

# CP locations mapping
CP_LOCATIONS = {
    "CP-001": "Madrid,ES",
    "CP-002": "Barcelona,ES",
    # Add more CPs as needed
}

# ============================================================================
# WEATHER SERVICE
# ============================================================================

class WeatherService:
    def __init__(self, api_key, central_url, locations):
        self.api_key = api_key
        self.central_url = central_url
        self.locations = locations
        self.current_alerts = {}  # Track active alerts
        
        print("=" * 70)
        print("  EV_W (Weather Control Office) - STARTED")
        print("=" * 70)
        print(f"\nMonitoring {len(locations)} charging points:")
        for cp_id, city in locations.items():
            print(f"   • {cp_id} → {city}")
        print()
    
    def get_temperature(self, city):
        """
        Query OpenWeather API for current temperature
        Returns: temperature in Celsius, or None if error
        """
        try:
            url = f"http://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": city,
                "appid": self.api_key,
                "units": "metric"  # Celsius
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                temp = data['main']['temp']
                return temp
            else:
                print(f"⚠️  Weather API error for {city}: {response.status_code}")
                return None
        
        except Exception as e:
            print(f"❌ Error fetching weather for {city}: {e}")
            return None
    
    def send_alert(self, cp_id, city, temperature):
        """Send cold weather alert to Central"""
        try:
            url = f"{self.central_url}/alert"
            payload = {
                "cp_id": cp_id,
                "location": city,
                "temperature": temperature,
                "alert_type": "COLD_WEATHER",
                "timestamp": datetime.now().isoformat()
            }
            
            response = requests.post(url, json=payload, timeout=5)
            
            if response.status_code == 200:
                print(f"🚨 ALERT SENT: {cp_id} at {city} - {temperature}°C → CP DISABLED")
                self.current_alerts[cp_id] = True
                return True
            else:
                print(f"⚠️  Failed to send alert: {response.status_code}")
                return False
        
        except Exception as e:
            print(f"❌ Error sending alert: {e}")
            return False
    
    def send_clear(self, cp_id, city, temperature):
        """Send weather clear signal to Central"""
        try:
            url = f"{self.central_url}/clear"
            payload = {
                "cp_id": cp_id,
                "location": city,
                "temperature": temperature,
                "timestamp": datetime.now().isoformat()
            }
            
            response = requests.post(url, json=payload, timeout=5)
            
            if response.status_code == 200:
                print(f"✅ CLEAR SENT: {cp_id} at {city} - {temperature}°C → CP ENABLED")
                self.current_alerts[cp_id] = False
                return True
            else:
                print(f"⚠️  Failed to send clear: {response.status_code}")
                return False
        
        except Exception as e:
            print(f"❌ Error sending clear: {e}")
            return False
    
    def check_weather_loop(self):
        """Main loop - check weather every 4 seconds"""
        print("\n🌡️  Starting weather monitoring loop (checking every 4 seconds)...\n")
        
        while True:
            try:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking weather...")
                
                for cp_id, city in self.locations.items():
                    temp = self.get_temperature(city)
                    
                    if temp is None:
                        continue
                    
                    # Round to 1 decimal
                    temp = round(temp, 1)
                    
                    # Check if alert is currently active
                    is_alerted = self.current_alerts.get(cp_id, False)
                    
                    if temp <= 0:
                        # Cold weather - send alert if not already alerted
                        if not is_alerted:
                            self.send_alert(cp_id, city, temp)
                        else:
                            print(f"   ❄️  {cp_id} at {city}: {temp}°C (already disabled)")
                    
                    else:
                        # Normal weather - send clear if currently alerted
                        if is_alerted:
                            self.send_clear(cp_id, city, temp)
                        else:
                            print(f"   ☀️  {cp_id} at {city}: {temp}°C (operational)")
                
                print()  # Blank line for readability
                time.sleep(CHECK_INTERVAL)
            
            except KeyboardInterrupt:
                print("\n\n👋 Weather monitoring stopped by user")
                break
            except Exception as e:
                print(f"❌ Error in monitoring loop: {e}")
                time.sleep(CHECK_INTERVAL)

# ============================================================================
# MAIN
# ============================================================================

def main():
    # Create service
    service = WeatherService(
        api_key=OPENWEATHER_API_KEY,
        central_url=CENTRAL_API_URL,
        locations=CP_LOCATIONS
    )
    
    # Start monitoring
    service.check_weather_loop()

if __name__ == "__main__":
    main()