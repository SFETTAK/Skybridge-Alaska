# MQTT Telemetry Configuration Guide

## Overview
This guide covers setting up Meshtastic radios to report telemetry to your own MQTT server for monitoring and data collection.

## MQTT Server Setup

### Option 1: Cloud-Based (Recommended for Testing)
```bash
# Using DigitalOcean/AWS/Azure Ubuntu VM
sudo apt update
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

# Configure authentication
sudo mosquitto_passwd -c /etc/mosquitto/passwd skybridge
# Enter password when prompted

# Edit mosquitto config
sudo nano /etc/mosquitto/conf.d/default.conf
```

Add to config:
```
listener 1883
listener 8883
certfile /etc/mosquitto/certs/server.crt
keyfile /etc/mosquitto/certs/server.key
allow_anonymous false
password_file /etc/mosquitto/passwd

# ACL for topic access
acl_file /etc/mosquitto/acl
```

### Option 2: Local Server at DOT
Same setup but ensure:
- Static IP address
- Firewall rules allow 1883/8883
- DNS entry for easy access
- Backup strategy in place

## Radio Configuration

### Using Meshtastic Python CLI

1. **Install CLI**:
```bash
pip install meshtastic
```

2. **Connect Radio via USB**:
```bash
# List connected devices
meshtastic --info

# Set MQTT parameters
meshtastic --set mqtt.enabled true
meshtastic --set mqtt.address "your-server.com"
meshtastic --set mqtt.username "skybridge"
meshtastic --set mqtt.password "your-password"
meshtastic --set mqtt.encryption_enabled true
meshtastic --set mqtt.tls_enabled true
meshtastic --set mqtt.root_topic "alaska/aviation"
```

3. **Configure Telemetry Intervals**:
```bash
# Device metrics every 15 minutes
meshtastic --set telemetry.device_update_interval 900

# Environmental sensors (if equipped)
meshtastic --set telemetry.environment_update_interval 900

# Position updates every 5 minutes
meshtastic --set position.position_broadcast_secs 300
```

### Using Web/App Interface

1. Navigate to Radio Configuration
2. Enable MQTT Module
3. Enter server details:
   - Server: `your-mqtt-server.com`
   - Port: `1883` (or 8883 for TLS)
   - Username: `skybridge`
   - Password: `[your-password]`
   - Root Topic: `alaska/aviation`

## MQTT Topics Structure

```
alaska/aviation/
├── 2/stat/!nodeID/           # Node statistics
├── 2/e/LongFast/!nodeID/     # Messages/data
├── 2/map/                    # Position reports
│   └── {"lat":61.123,"lon":-149.456,"id":"!nodeID"}
├── 2/json/LongFast/!nodeID/  # JSON formatted data
└── msh/2/c/LongFast/!nodeID/  # Channel messages
```

## Data Collection Script

Create `collect_telemetry.py`:
```python
import paho.mqtt.client as mqtt
import json
from datetime import datetime
import sqlite3

# MQTT Settings
MQTT_SERVER = "your-server.com"
MQTT_PORT = 1883
MQTT_USER = "skybridge"
MQTT_PASS = "your-password"
MQTT_TOPIC = "alaska/aviation/#"

# Database
conn = sqlite3.connect('telemetry.db')
c = conn.cursor()

# Create tables
c.execute('''CREATE TABLE IF NOT EXISTS telemetry
             (timestamp TEXT, node_id TEXT, topic TEXT, 
              payload TEXT, data_type TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS positions
             (timestamp TEXT, node_id TEXT, latitude REAL, 
              longitude REAL, altitude REAL, battery_level INTEGER)''')

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    timestamp = datetime.now().isoformat()
    topic = msg.topic
    
    try:
        # Try to parse as JSON
        payload = json.loads(msg.payload.decode())
        
        # Extract node ID from topic
        parts = topic.split('/')
        node_id = next((p for p in parts if p.startswith('!')), 'unknown')
        
        # Store raw telemetry
        c.execute("INSERT INTO telemetry VALUES (?, ?, ?, ?, ?)",
                  (timestamp, node_id, topic, json.dumps(payload), 'json'))
        
        # Extract position data if available
        if 'latitude' in payload and 'longitude' in payload:
            lat = payload.get('latitude')
            lon = payload.get('longitude')
            alt = payload.get('altitude', 0)
            battery = payload.get('batteryLevel', -1)
            
            c.execute("INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?)",
                      (timestamp, node_id, lat, lon, alt, battery))
        
        conn.commit()
        print(f"Stored: {node_id} - {topic}")
        
    except json.JSONDecodeError:
        # Store as text if not JSON
        c.execute("INSERT INTO telemetry VALUES (?, ?, ?, ?, ?)",
                  (timestamp, 'unknown', topic, msg.payload.decode(), 'text'))
        conn.commit()
    except Exception as e:
        print(f"Error processing message: {e}")

# Setup MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message

# Connect and loop
client.connect(MQTT_SERVER, MQTT_PORT, 60)
client.loop_forever()
```

## Monitoring Dashboard

### Using Grafana (Recommended)

1. **Install Grafana**:
```bash
sudo apt install grafana
sudo systemctl enable grafana-server
sudo systemctl start grafana-server
```

2. **Install Telegraf for MQTT**:
```bash
# Install Telegraf
wget -qO- https://repos.influxdata.com/influxdb.key | sudo apt-key add -
echo "deb https://repos.influxdata.com/debian stable main" | sudo tee /etc/apt/sources.list.d/influxdb.list
sudo apt update
sudo apt install telegraf influxdb

# Configure Telegraf MQTT input
sudo nano /etc/telegraf/telegraf.conf
```

Add MQTT input:
```toml
[[inputs.mqtt_consumer]]
  servers = ["tcp://localhost:1883"]
  topics = ["alaska/aviation/#"]
  username = "skybridge"
  password = "your-password"
  data_format = "json"
```

3. **Create Dashboard**:
   - Node status (online/offline)
   - Battery levels
   - Message count per hour
   - Geographic distribution map
   - Signal strength heatmap

### Simple Web Dashboard

Create `dashboard.html`:
```html
<!DOCTYPE html>
<html>
<head>
    <title>SkyBridge Telemetry Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/mqtt/dist/mqtt.min.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
    <style>
        #map { height: 400px; }
        .node-status { 
            padding: 10px; 
            margin: 5px; 
            border-radius: 5px; 
            background: #f0f0f0; 
        }
        .online { background: #90EE90; }
        .offline { background: #FFB6C1; }
    </style>
</head>
<body>
    <h1>SkyBridge Alaska Telemetry</h1>
    
    <div id="stats">
        <h2>Network Stats</h2>
        <div id="node-count">Nodes: 0</div>
        <div id="message-count">Messages: 0</div>
    </div>
    
    <div id="nodes">
        <h2>Active Nodes</h2>
        <div id="node-list"></div>
    </div>
    
    <div id="map"></div>
    
    <script>
        // Initialize map
        var map = L.map('map').setView([61.2181, -149.9003], 7);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
        
        var markers = {};
        var nodes = {};
        var messageCount = 0;
        
        // MQTT connection
        const client = mqtt.connect('wss://your-server.com:8884', {
            username: 'skybridge',
            password: 'your-password'
        });
        
        client.on('connect', function () {
            console.log('Connected to MQTT');
            client.subscribe('alaska/aviation/#');
        });
        
        client.on('message', function (topic, message) {
            messageCount++;
            document.getElementById('message-count').textContent = `Messages: ${messageCount}`;
            
            try {
                const data = JSON.parse(message.toString());
                
                // Extract node ID
                const nodeId = topic.match(/![\da-f]+/i)?.[0] || 'unknown';
                
                // Update node status
                if (!nodes[nodeId]) {
                    nodes[nodeId] = { 
                        lastSeen: Date.now(), 
                        battery: data.batteryLevel || 0 
                    };
                }
                nodes[nodeId].lastSeen = Date.now();
                
                // Update position on map
                if (data.latitude && data.longitude) {
                    if (markers[nodeId]) {
                        markers[nodeId].setLatLng([data.latitude, data.longitude]);
                    } else {
                        markers[nodeId] = L.marker([data.latitude, data.longitude])
                            .addTo(map)
                            .bindPopup(`Node: ${nodeId}<br>Battery: ${data.batteryLevel}%`);
                    }
                }
                
                updateNodeList();
                
            } catch (e) {
                console.error('Error parsing message:', e);
            }
        });
        
        function updateNodeList() {
            const nodeList = document.getElementById('node-list');
            nodeList.innerHTML = '';
            
            Object.entries(nodes).forEach(([id, info]) => {
                const isOnline = (Date.now() - info.lastSeen) < 300000; // 5 minutes
                const div = document.createElement('div');
                div.className = `node-status ${isOnline ? 'online' : 'offline'}`;
                div.textContent = `${id} - Battery: ${info.battery}% - ${isOnline ? 'Online' : 'Offline'}`;
                nodeList.appendChild(div);
            });
            
            document.getElementById('node-count').textContent = `Nodes: ${Object.keys(nodes).length}`;
        }
        
        // Check node status every minute
        setInterval(updateNodeList, 60000);
    </script>
</body>
</html>
```

## Security Best Practices

1. **Use TLS/SSL**:
   ```bash
   # Generate certificates
   openssl req -new -x509 -days 365 -nodes \
     -out /etc/mosquitto/certs/server.crt \
     -keyout /etc/mosquitto/certs/server.key
   ```

2. **Implement ACLs**:
   ```
   # /etc/mosquitto/acl
   user skybridge
   topic readwrite alaska/aviation/#
   
   user readonly
   topic read alaska/aviation/#
   ```

3. **Rate Limiting**:
   - Configure max message rate per client
   - Set connection limits
   - Monitor for anomalies

## Troubleshooting

### Common Issues

1. **No Data Received**:
   - Check firewall rules
   - Verify MQTT credentials
   - Ensure radio has internet access
   - Check topic subscriptions

2. **Intermittent Connection**:
   - Increase keepalive interval
   - Check for network issues
   - Monitor server resources

3. **Data Not Parsing**:
   - Enable debug logging
   - Check message format
   - Verify topic structure

### Debug Commands

```bash
# Subscribe to all topics
mosquitto_sub -h your-server.com -u skybridge -P password -t "alaska/aviation/#" -v

# Monitor specific node
mosquitto_sub -h your-server.com -u skybridge -P password -t "alaska/aviation/+/+/!nodeID/#" -v

# Test publish
mosquitto_pub -h your-server.com -u skybridge -P password -t "alaska/aviation/test" -m "Hello"
```

## Next Steps

1. Set up automated backups of telemetry data
2. Create alerts for node offline events
3. Implement data retention policies
4. Build analytics for network optimization
5. Integrate weather data injection system

Ready to proceed with setting up your MQTT infrastructure?
