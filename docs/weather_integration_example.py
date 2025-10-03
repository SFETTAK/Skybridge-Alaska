#!/usr/bin/env python3
"""
Alaska Weather Data Integration Example for SkyBridge
Fetches aviation weather from NOAA and prepares it for mesh distribution
"""

import requests
import json
import time
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')

# Alaska airports to monitor (add more as needed)
ALASKA_AIRPORTS = [
    'PANC',  # Anchorage
    'PAFA',  # Fairbanks
    'PAJN',  # Juneau  
    'PAOM',  # Nome
    'PABE',  # Bethel
    'PADQ',  # Kodiak
    'PACD',  # Cold Bay
    'PAKT',  # Ketchikan
    'PASN',  # St. Paul Island
    'PADU',  # Unalaska
    'PAEN',  # Kenai
    'PALH',  # Lake Hood
    'PAMR',  # Merrill Field
    'PAED',  # Elmendorf
]

class AlaskaWeatherCollector:
    def __init__(self):
        self.base_url = "https://aviationweather.gov/api/data"
        self.weather_cache = {}
        
    def fetch_metars(self):
        """Fetch current METARs for Alaska airports"""
        airports = ','.join(ALASKA_AIRPORTS)
        url = f"{self.base_url}/metar?ids={airports}&format=json"
        
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                logging.info(f"Fetched {len(data)} METARs")
                return data
            else:
                logging.error(f"METAR fetch failed: {response.status_code}")
                return []
        except Exception as e:
            logging.error(f"METAR fetch error: {e}")
            return []
    
    def fetch_tafs(self):
        """Fetch TAFs (forecasts) for Alaska airports"""
        airports = ','.join(ALASKA_AIRPORTS)
        url = f"{self.base_url}/taf?ids={airports}&format=json"
        
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                logging.info(f"Fetched {len(data)} TAFs")
                return data
            else:
                logging.error(f"TAF fetch failed: {response.status_code}")
                return []
        except Exception as e:
            logging.error(f"TAF fetch error: {e}")
            return []
    
    def parse_metar(self, metar_data):
        """Parse METAR into simplified format for mesh transmission"""
        try:
            # Extract key fields
            parsed = {
                'airport': metar_data.get('icaoId', 'UNKN'),
                'time': metar_data.get('reportTime', ''),
                'wind': {
                    'dir': metar_data.get('windDir', 0),
                    'speed': metar_data.get('windSpeed', 0),
                    'gust': metar_data.get('windGust', 0)
                },
                'visibility': metar_data.get('visibility', 10),
                'clouds': self._parse_clouds(metar_data.get('clouds', [])),
                'temp': metar_data.get('temp', 0),
                'dewpoint': metar_data.get('dewpoint', 0),
                'altimeter': metar_data.get('altimeter', 29.92),
                'raw': metar_data.get('rawOb', '')
            }
            
            # Add flight category if available
            if 'flightCategory' in metar_data:
                parsed['category'] = metar_data['flightCategory']
                
            return parsed
        except Exception as e:
            logging.error(f"METAR parse error: {e}")
            return None
    
    def _parse_clouds(self, cloud_layers):
        """Parse cloud layer information"""
        clouds = []
        for layer in cloud_layers:
            if isinstance(layer, dict):
                clouds.append({
                    'cover': layer.get('cover', ''),
                    'base': layer.get('base', 0)
                })
        return clouds
    
    def format_for_mesh(self, weather_data):
        """Format weather data for efficient mesh transmission"""
        # This would eventually use TAIGA encoding
        # For now, create compact JSON
        
        mesh_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'wx': []
        }
        
        for metar in weather_data:
            if metar:
                compact = {
                    'id': metar['airport'],
                    't': metar['time'],
                    'w': f"{metar['wind']['dir']:03d}{metar['wind']['speed']:02d}",
                    'v': metar['visibility'],
                    'c': metar.get('category', 'VFR'),
                    'tmp': metar['temp'],
                    'alt': round(metar['altimeter'], 2)
                }
                
                # Add clouds if present
                if metar['clouds']:
                    compact['cld'] = []
                    for cloud in metar['clouds']:
                        compact['cld'].append(f"{cloud['cover']}{cloud['base']}")
                
                mesh_data['wx'].append(compact)
        
        return mesh_data
    
    def run_collection_cycle(self):
        """Run a complete weather collection cycle"""
        logging.info("Starting weather collection cycle")
        
        # Fetch current conditions
        metars = self.fetch_metars()
        
        # Parse all METARs
        parsed_metars = []
        for metar in metars:
            parsed = self.parse_metar(metar)
            if parsed:
                parsed_metars.append(parsed)
        
        # Format for mesh transmission
        mesh_ready = self.format_for_mesh(parsed_metars)
        
        # Calculate size
        json_size = len(json.dumps(mesh_ready))
        logging.info(f"Mesh payload size: {json_size} bytes")
        
        # Store in cache
        self.weather_cache = mesh_ready
        
        return mesh_ready

def example_mqtt_publish(weather_data):
    """Example of publishing weather to MQTT for mesh distribution"""
    import paho.mqtt.client as mqtt
    
    client = mqtt.Client()
    # Configure your MQTT server here
    # client.username_pw_set("username", "password")
    # client.connect("your-mqtt-server.com", 1883, 60)
    
    # Publish to weather topic
    topic = "alaska/aviation/weather/current"
    payload = json.dumps(weather_data)
    
    # client.publish(topic, payload)
    logging.info(f"Would publish {len(payload)} bytes to {topic}")

def main():
    """Main execution"""
    collector = AlaskaWeatherCollector()
    
    # Run once for testing
    weather_data = collector.run_collection_cycle()
    
    # Print sample output
    print("\n=== Sample Weather Data ===")
    print(json.dumps(weather_data, indent=2))
    
    # Show how it would be published
    example_mqtt_publish(weather_data)
    
    # Production would run this in a loop
    # while True:
    #     weather_data = collector.run_collection_cycle()
    #     example_mqtt_publish(weather_data)
    #     time.sleep(300)  # Update every 5 minutes

if __name__ == "__main__":
    main()
