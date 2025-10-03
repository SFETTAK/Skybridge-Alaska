# Quick Range Test Configuration for Initial Deployment

## Simple Setup for Immediate Testing

### 1. Basic Radio Configuration (All Devices)

```bash
# Essential telemetry settings for all radios
meshtastic --set telemetry.device_update_interval 900      # 15 minutes
meshtastic --set position.position_broadcast_secs 300      # 5 minutes
meshtastic --set position.gps_enabled true                 # Enable GPS
meshtastic --set device.role CLIENT_MUTE                   # Save battery

# For fixed nodes only (at AAA and DOTHQ)
meshtastic --set device.role ROUTER_CLIENT                 # Better coverage
meshtastic --set position.fixed_position true              # Mark as fixed
```

### 2. MQTT Settings for Data Collection

```bash
# Configure MQTT on all devices
meshtastic --set mqtt.enabled true
meshtastic --set mqtt.address "your-server.com"
meshtastic --set mqtt.username "alaska-node"
meshtastic --set mqtt.password "your-password"
meshtastic --set mqtt.encryption_enabled true
meshtastic --set mqtt.json_enabled true
```

### 3. Simple Python Logger for Your Server

Save as `range_logger.py`:
```python
#!/usr/bin/env python3
"""Simple range test data logger for Alaska deployment"""

import paho.mqtt.client as mqtt
import json
import sqlite3
from datetime import datetime
import math

# Configuration
MQTT_SERVER = "your-server.com"
MQTT_USER = "alaska-node"
MQTT_PASS = "your-password"
MQTT_TOPIC = "msh/US/2/json/LongFast/+/+"

# Database setup
conn = sqlite3.connect('alaska_mesh.db')
c = conn.cursor()

# Create simple tables
c.execute('''CREATE TABLE IF NOT EXISTS messages
             (timestamp TEXT, from_node TEXT, to_node TEXT, 
              latitude REAL, longitude REAL, altitude REAL,
              rssi INTEGER, snr REAL, hop_count INTEGER,
              message TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS node_positions
             (timestamp TEXT, node_id TEXT, latitude REAL, 
              longitude REAL, altitude REAL, battery INTEGER,
              PRIMARY KEY (timestamp, node_id))''')

conn.commit()

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in miles between two points"""
    R = 3959  # Earth radius in miles
    
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c

def on_message(client, userdata, msg):
    """Process incoming MQTT messages"""
    try:
        data = json.loads(msg.payload.decode())
        
        # Extract basic info
        timestamp = datetime.utcnow().isoformat()
        from_node = data.get('from', 'unknown')
        to_node = data.get('to', 'unknown')
        
        # Handle position reports
        if data.get('type') == 'position':
            payload = data.get('payload', {})
            lat = payload.get('latitude_i', 0) / 1e7
            lon = payload.get('longitude_i', 0) / 1e7
            alt = payload.get('altitude', 0)
            battery = payload.get('battery_level', 0)
            
            if lat != 0 and lon != 0:
                c.execute('''INSERT OR REPLACE INTO node_positions 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                          (timestamp, from_node, lat, lon, alt, battery))
                conn.commit()
                print(f"Position: {from_node} at {lat:.4f}, {lon:.4f}, "
                      f"alt: {alt}ft, battery: {battery}%")
        
        # Handle text messages (including range tests)
        elif data.get('type') == 'text':
            text = data.get('payload', {}).get('text', '')
            rssi = data.get('rssi', 0)
            snr = data.get('snr', 0)
            hops = data.get('hop_start', 0) - data.get('hop_limit', 0)
            
            # Get sender position
            c.execute('SELECT latitude, longitude, altitude FROM node_positions '
                      'WHERE node_id = ? ORDER BY timestamp DESC LIMIT 1',
                      (from_node,))
            pos = c.fetchone()
            
            if pos:
                lat, lon, alt = pos
                c.execute('''INSERT INTO messages VALUES 
                            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (timestamp, from_node, to_node, lat, lon, alt,
                           rssi, snr, hops, text))
                conn.commit()
                
                # Calculate distance to known fixed nodes
                if from_node != to_node:
                    print(f"Message: {from_node} -> {to_node}, "
                          f"RSSI: {rssi}, SNR: {snr}, Hops: {hops}")
        
    except Exception as e:
        print(f"Error processing message: {e}")

# Connect to MQTT
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_message = on_message

client.connect(MQTT_SERVER, 1883, 60)
client.subscribe(MQTT_TOPIC)

print("Alaska Mesh Logger Started - Collecting range data...")
print("Press Ctrl+C to generate report")

try:
    client.loop_forever()
except KeyboardInterrupt:
    print("\n\n=== Range Test Summary ===")
    
    # Show active nodes
    c.execute('''SELECT DISTINCT node_id, 
                 MAX(timestamp) as last_seen,
                 COUNT(*) as position_reports
                 FROM node_positions
                 GROUP BY node_id''')
    
    print("\nActive Nodes:")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[2]} reports, last: {row[1]}")
    
    # Show communication links
    c.execute('''SELECT from_node, to_node, 
                 COUNT(*) as messages,
                 AVG(rssi) as avg_rssi,
                 MIN(rssi) as min_rssi,
                 MAX(rssi) as max_rssi
                 FROM messages
                 GROUP BY from_node, to_node''')
    
    print("\nCommunication Links:")
    for row in c.fetchall():
        print(f"  {row[0]} -> {row[1]}: {row[2]} msgs, "
              f"RSSI avg:{row[3]:.1f} ({row[4]} to {row[5]})")

conn.close()
```

### 4. What Data You'll Collect

Every 5-15 minutes, each radio will report:
- **GPS Position** (lat, lon, altitude)
- **Battery Level**
- **Signal Strength** (RSSI/SNR) to other nodes
- **Hop Count** (how many relays)
- **Channel Utilization**
- **Temperature** (if sensor equipped)

### 5. Quick Analysis Scripts

#### Coverage Heatmap Generator
```python
# Generate a simple coverage map
import folium
import sqlite3

conn = sqlite3.connect('alaska_mesh.db')
c = conn.cursor()

# Create map centered on Anchorage
m = folium.Map(location=[61.2181, -149.9003], zoom_start=8)

# Add node positions
c.execute('SELECT DISTINCT node_id, latitude, longitude FROM node_positions')
for node_id, lat, lon in c.fetchall():
    folium.Marker([lat, lon], 
                  popup=f"Node: {node_id}",
                  icon=folium.Icon(color='green')).add_to(m)

# Add message paths
c.execute('''SELECT DISTINCT m1.latitude, m1.longitude, 
                            m2.latitude, m2.longitude
             FROM messages m1
             JOIN node_positions m2 ON m1.to_node = m2.node_id
             WHERE m1.from_node != m1.to_node''')

for lat1, lon1, lat2, lon2 in c.fetchall():
    folium.PolyLine([[lat1, lon1], [lat2, lon2]], 
                    color='blue', weight=2, opacity=0.5).add_to(m)

m.save('coverage_map.html')
print("Coverage map saved to coverage_map.html")
```

### 6. Initial Test Patterns

#### Week 1: Basic Coverage
- Install fixed nodes
- Have pilots drive/fly standard routes
- Collect baseline coverage data

#### Week 2: Altitude Testing  
- Pilots report position at different altitudes
- Compare ground vs. airborne range
- Identify optimal flight levels

#### Week 3: Edge Testing
- Find coverage boundaries
- Test in valleys and behind terrain
- Identify dead zones

### 7. Simple Metrics Dashboard

Create `metrics.html`:
```html
<!DOCTYPE html>
<html>
<head>
    <title>Alaska Mesh Metrics</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <h1>SkyBridge Alaska - Range Test Results</h1>
    
    <div style="width: 600px;">
        <canvas id="rangeChart"></canvas>
    </div>
    
    <h2>Key Findings:</h2>
    <ul id="findings"></ul>
    
    <script>
    // This would pull from your database
    const data = {
        labels: ['0-5mi', '5-10mi', '10-20mi', '20-50mi', '50+mi'],
        datasets: [{
            label: 'Success Rate %',
            data: [98, 95, 88, 72, 45],
            backgroundColor: 'rgba(75, 192, 192, 0.6)'
        }]
    };
    
    const ctx = document.getElementById('rangeChart').getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: data,
        options: {
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100
                }
            }
        }
    });
    </script>
</body>
</html>
```

### 8. What to Expect

#### First Week Results:
- **Ground Level**: 2-10 mile range typical
- **1,000ft AGL**: 15-30 miles common
- **5,000ft+**: 50-100+ miles possible
- **Mountains**: Excellent repeater locations

#### Battery Impact:
- Position every 5 min: ~18-24 hour battery
- Position every 15 min: ~2-3 day battery
- Fixed nodes: Continuous operation

### 9. Reporting to Stakeholders

Weekly summary should include:
- Total active nodes
- Messages transmitted
- Coverage area (sq miles)
- Average range by altitude
- Network reliability %
- Battery life statistics

### 10. Next Steps After Initial Data

1. **Identify Coverage Gaps**
   - Where do we need more nodes?
   - Which locations are critical?

2. **Optimize Settings**
   - Adjust position intervals
   - Tune power settings
   - Set hop limits

3. **Plan Expansion**
   - High-traffic corridors
   - Remote airstrips
   - Mountain repeaters

This simplified approach will get you collecting valuable data immediately without complex setup!
