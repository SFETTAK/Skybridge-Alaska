# Automated Range Testing & Data Collection

## Overview
Configure Meshtastic radios to automatically perform range tests and collect comprehensive network performance data. This will help map actual coverage and identify optimal node locations.

## Range Testing Strategy

### 1. Built-in Meshtastic Features

#### Position Reporting
```bash
# Configure automatic position broadcasts
meshtastic --set position.position_broadcast_secs 300  # Every 5 minutes
meshtastic --set position.gps_enabled true
meshtastic --set position.fixed_position false  # For mobile nodes
```

#### Telemetry Collection
```bash
# Device telemetry (battery, temperature, etc.)
meshtastic --set telemetry.device_update_interval 900  # Every 15 minutes

# Environmental telemetry (if sensors attached)
meshtastic --set telemetry.environment_update_interval 900

# Air quality metrics
meshtastic --set telemetry.air_quality_enabled true
meshtastic --set telemetry.air_quality_interval 900
```

### 2. Range Test Messages

#### Automated Range Test Script
```python
#!/usr/bin/env python3
"""
Automated range testing for Meshtastic network
Sends test messages and logs delivery statistics
"""

import meshtastic
import meshtastic.serial_interface
import time
import json
import sqlite3
from datetime import datetime
import hashlib

class RangeTestBot:
    def __init__(self, db_path="range_tests.db"):
        self.interface = meshtastic.serial_interface.SerialInterface()
        self.db_path = db_path
        self.init_database()
        self.test_interval = 3600  # 1 hour
        
    def init_database(self):
        """Initialize SQLite database for storing test results"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Range test results
        c.execute('''CREATE TABLE IF NOT EXISTS range_tests
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      timestamp TEXT,
                      test_id TEXT,
                      from_node TEXT,
                      to_node TEXT,
                      distance_km REAL,
                      rssi INTEGER,
                      snr REAL,
                      hop_count INTEGER,
                      delivered INTEGER,
                      round_trip_ms INTEGER,
                      gps_from_lat REAL,
                      gps_from_lon REAL,
                      gps_from_alt REAL,
                      gps_to_lat REAL,
                      gps_to_lon REAL,
                      gps_to_alt REAL)''')
        
        # Node telemetry
        c.execute('''CREATE TABLE IF NOT EXISTS node_telemetry
                     (timestamp TEXT,
                      node_id TEXT,
                      battery_level INTEGER,
                      voltage REAL,
                      channel_utilization REAL,
                      air_util_tx REAL,
                      temperature REAL,
                      humidity REAL,
                      pressure REAL)''')
        
        # Network topology
        c.execute('''CREATE TABLE IF NOT EXISTS network_topology
                     (timestamp TEXT,
                      from_node TEXT,
                      to_node TEXT,
                      rssi INTEGER,
                      snr REAL,
                      distance_km REAL)''')
        
        conn.commit()
        conn.close()
    
    def generate_test_id(self):
        """Generate unique test ID"""
        timestamp = str(time.time())
        return hashlib.md5(timestamp.encode()).hexdigest()[:8]
    
    def send_range_test(self, destination="^all"):
        """Send range test message to network"""
        test_id = self.generate_test_id()
        test_message = {
            "type": "range_test",
            "id": test_id,
            "timestamp": datetime.utcnow().isoformat(),
            "from": self.interface.getMyNodeInfo()['user']['id'],
            "position": self.get_current_position()
        }
        
        # Send test message
        message_str = f"RT:{test_id}:{int(time.time())}"
        self.interface.sendText(
            message_str,
            destinationId=destination,
            wantAck=True,
            wantResponse=True
        )
        
        return test_id
    
    def get_current_position(self):
        """Get current GPS position"""
        my_node = self.interface.getMyNodeInfo()
        if 'position' in my_node:
            return {
                "lat": my_node['position'].get('latitude', 0),
                "lon": my_node['position'].get('longitude', 0),
                "alt": my_node['position'].get('altitude', 0)
            }
        return None
    
    def calculate_distance(self, pos1, pos2):
        """Calculate distance between two positions in km"""
        from math import radians, sin, cos, sqrt, atan2
        
        if not pos1 or not pos2:
            return None
            
        R = 6371  # Earth radius in km
        
        lat1, lon1 = radians(pos1['lat']), radians(pos1['lon'])
        lat2, lon2 = radians(pos2['lat']), radians(pos2['lon'])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return R * c
    
    def on_receive(self, packet, interface):
        """Handle received packets"""
        try:
            # Check if it's a range test response
            if packet.get('decoded', {}).get('text', '').startswith('RT:'):
                self.process_range_test_response(packet)
            
            # Log all telemetry
            if 'decoded' in packet and 'telemetry' in packet['decoded']:
                self.log_telemetry(packet)
                
        except Exception as e:
            print(f"Error processing packet: {e}")
    
    def process_range_test_response(self, packet):
        """Process range test response and log results"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Extract test data
        text = packet['decoded']['text']
        parts = text.split(':')
        if len(parts) >= 3:
            test_id = parts[1]
            
            # Get packet metadata
            from_node = packet.get('fromId', 'unknown')
            to_node = packet.get('toId', 'unknown')
            rssi = packet.get('rxRssi', 0)
            snr = packet.get('rxSnr', 0)
            hop_count = packet.get('hopLimit', 0) - packet.get('hopCount', 0)
            
            # Get positions
            my_pos = self.get_current_position()
            their_pos = None
            
            # Calculate distance if positions available
            distance = None
            if my_pos and their_pos:
                distance = self.calculate_distance(my_pos, their_pos)
            
            # Log to database
            c.execute('''INSERT INTO range_tests 
                        (timestamp, test_id, from_node, to_node, distance_km,
                         rssi, snr, hop_count, delivered, gps_from_lat, 
                         gps_from_lon, gps_from_alt)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (datetime.utcnow().isoformat(), test_id, from_node, to_node,
                      distance, rssi, snr, hop_count, 1,
                      my_pos['lat'] if my_pos else None,
                      my_pos['lon'] if my_pos else None,
                      my_pos['alt'] if my_pos else None))
            
            conn.commit()
        conn.close()
    
    def log_telemetry(self, packet):
        """Log telemetry data from nodes"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        telemetry = packet['decoded']['telemetry']
        node_id = packet.get('fromId', 'unknown')
        
        # Device metrics
        if 'deviceMetrics' in telemetry:
            metrics = telemetry['deviceMetrics']
            c.execute('''INSERT INTO node_telemetry
                        (timestamp, node_id, battery_level, voltage,
                         channel_utilization, air_util_tx)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (datetime.utcnow().isoformat(), node_id,
                      metrics.get('batteryLevel', 0),
                      metrics.get('voltage', 0),
                      metrics.get('channelUtilization', 0),
                      metrics.get('airUtilTx', 0)))
        
        # Environmental metrics
        if 'environmentMetrics' in telemetry:
            env = telemetry['environmentMetrics']
            c.execute('''UPDATE node_telemetry 
                        SET temperature = ?, humidity = ?, pressure = ?
                        WHERE node_id = ? AND timestamp = ?''',
                     (env.get('temperature', 0),
                      env.get('relativeHumidity', 0),
                      env.get('barometricPressure', 0),
                      node_id, datetime.utcnow().isoformat()))
        
        conn.commit()
        conn.close()
    
    def run_scheduled_test(self):
        """Run a scheduled range test"""
        print(f"[{datetime.utcnow()}] Running scheduled range test...")
        test_id = self.send_range_test()
        print(f"Range test {test_id} sent")
        
        # Also request node info updates
        self.interface.sendNodeInfo()
    
    def generate_coverage_report(self):
        """Generate coverage analysis from collected data"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Get success rate by distance
        c.execute('''SELECT 
                        ROUND(distance_km/10)*10 as distance_bucket,
                        COUNT(*) as total_tests,
                        SUM(delivered) as successful,
                        AVG(rssi) as avg_rssi,
                        AVG(snr) as avg_snr
                     FROM range_tests
                     WHERE distance_km IS NOT NULL
                     GROUP BY distance_bucket
                     ORDER BY distance_bucket''')
        
        results = c.fetchall()
        
        print("\n=== Coverage Report ===")
        print("Distance | Tests | Success % | Avg RSSI | Avg SNR")
        print("-" * 50)
        for row in results:
            distance, total, success, rssi, snr = row
            success_rate = (success/total) * 100 if total > 0 else 0
            print(f"{distance:>6.0f}km | {total:>5} | {success_rate:>7.1f}% | "
                  f"{rssi:>8.1f} | {snr:>7.1f}")
        
        conn.close()
    
    def start(self):
        """Start automated range testing"""
        # Subscribe to receive messages
        pub.subscribe(self.on_receive, "meshtastic.receive")
        
        print("Starting automated range testing...")
        print(f"Tests will run every {self.test_interval/3600} hours")
        
        while True:
            try:
                self.run_scheduled_test()
                time.sleep(self.test_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error in test cycle: {e}")
                time.sleep(60)  # Wait a minute before retry

# Run the range test bot
if __name__ == "__main__":
    bot = RangeTestBot()
    bot.start()
```

### 3. MQTT Data Collection Configuration

#### Enhanced MQTT Topics for Range Testing
```yaml
# MQTT topic structure for range testing
alaska/aviation/
├── range_test/
│   ├── request/{node_id}      # Range test requests
│   ├── response/{node_id}     # Range test responses  
│   └── results/{test_id}      # Aggregated results
├── telemetry/
│   ├── device/{node_id}       # Device metrics
│   ├── environment/{node_id}  # Environmental data
│   └── position/{node_id}     # GPS positions
└── topology/
    └── links/{from_node}/{to_node}  # Network links
```

### 4. Data Analysis Dashboard

#### Grafana Dashboard Queries
```sql
-- Coverage heatmap query
SELECT 
  latitude, 
  longitude, 
  AVG(rssi) as signal_strength,
  COUNT(*) as measurement_count
FROM positions
WHERE timestamp > NOW() - INTERVAL '24 hours'
GROUP BY ROUND(latitude::numeric, 3), ROUND(longitude::numeric, 3)

-- Link quality over time
SELECT 
  timestamp,
  from_node,
  to_node,
  rssi,
  snr,
  delivered
FROM range_tests
WHERE timestamp > NOW() - INTERVAL '7 days'
ORDER BY timestamp

-- Node reliability
SELECT 
  node_id,
  AVG(battery_level) as avg_battery,
  MIN(battery_level) as min_battery,
  AVG(channel_utilization) as avg_channel_util,
  COUNT(*) as reports
FROM node_telemetry
WHERE timestamp > NOW() - INTERVAL '24 hours'
GROUP BY node_id
```

### 5. Advanced Testing Scenarios

#### Altitude-Based Testing
```python
def altitude_range_test(self):
    """Test range at different altitudes"""
    my_altitude = self.get_current_position()['alt']
    
    # Tag test with altitude bracket
    if my_altitude < 500:
        test_type = "ground"
    elif my_altitude < 3000:
        test_type = "low_altitude"  
    elif my_altitude < 10000:
        test_type = "medium_altitude"
    else:
        test_type = "high_altitude"
    
    test_id = f"ALT_{test_type}_{self.generate_test_id()}"
    # Continue with test...
```

#### Mobile vs Fixed Node Testing
```python
# Configure mobile nodes (aircraft)
meshtastic --set position.position_broadcast_secs 60  # More frequent
meshtastic --set position.gps_enabled true
meshtastic --set position.fixed_position false

# Configure fixed nodes (ground stations)  
meshtastic --set position.position_broadcast_secs 900  # Less frequent
meshtastic --set position.gps_enabled true
meshtastic --set position.fixed_position true
```

### 6. Power-Efficient Testing

#### Adaptive Test Intervals
```python
def get_adaptive_interval(self):
    """Adjust test interval based on battery and activity"""
    battery = self.get_battery_level()
    
    if battery > 80:
        return 1800  # 30 minutes
    elif battery > 50:
        return 3600  # 1 hour
    elif battery > 20:
        return 7200  # 2 hours
    else:
        return 14400  # 4 hours
```

### 7. Real-World Test Scenarios

#### Pattern-Based Testing
1. **Circle Pattern**: Test from multiple directions around a node
2. **Radial Pattern**: Fly away from node until signal lost
3. **Grid Pattern**: Systematic area coverage
4. **Altitude Ladder**: Test at increasing altitudes

#### Weather Correlation
```python
# Log weather conditions with each test
def add_weather_context(self, test_id):
    """Add weather data to range test"""
    weather = self.fetch_current_weather()
    
    # Store conditions that might affect range
    conditions = {
        'temperature': weather['temp'],
        'humidity': weather['humidity'],
        'pressure': weather['pressure'],
        'precipitation': weather.get('precip', 0),
        'visibility': weather.get('visibility', 10)
    }
    
    # Link to test results
    self.store_test_conditions(test_id, conditions)
```

### 8. Deployment Configuration

#### For Fixed Nodes
```bash
# High-frequency testing for infrastructure nodes
meshtastic --set telemetry.device_update_interval 300  # 5 min
meshtastic --set range_test.enabled true
meshtastic --set range_test.interval 1800  # 30 min
meshtastic --set range_test.save_csv true
```

#### For Pilot Radios
```bash
# Battery-conscious settings for handhelds
meshtastic --set telemetry.device_update_interval 900  # 15 min
meshtastic --set range_test.enabled true  
meshtastic --set range_test.interval 3600  # 1 hour
meshtastic --set range_test.sender true  # Participate in tests
```

### 9. Data Export and Analysis

#### Export Script
```python
def export_coverage_data():
    """Export range test data for analysis"""
    conn = sqlite3.connect('range_tests.db')
    
    # Export to CSV for GIS analysis
    query = '''SELECT timestamp, from_node, to_node, 
                     gps_from_lat, gps_from_lon, gps_from_alt,
                     gps_to_lat, gps_to_lon, gps_to_alt,
                     distance_km, rssi, snr, delivered
              FROM range_tests
              WHERE gps_from_lat IS NOT NULL'''
    
    df = pd.read_sql_query(query, conn)
    df.to_csv('coverage_data.csv', index=False)
    
    # Generate KML for Google Earth
    generate_kml(df)
```

### 10. Success Metrics

Track these KPIs:
- **Coverage Area**: Square miles with reliable signal
- **Success Rate**: % of messages delivered by distance
- **Network Reliability**: Uptime percentage  
- **Altitude Bonus**: Range improvement with altitude
- **Weather Impact**: Signal degradation in conditions
- **Battery Life**: Average runtime per charge
- **Node Density**: Optimal spacing for coverage

This comprehensive testing regime will give you real data to:
- Optimize node placement
- Predict coverage for pilots
- Identify dead zones
- Plan network expansion
- Demonstrate reliability to stakeholders

The hourly automated tests will quickly build a coverage map!
