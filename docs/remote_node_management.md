# Remote Node Management Guide

## Raspberry Pi Setup for Remote Management

### Initial Pi Configuration

```bash
# On fresh Raspbian Lite installation
sudo raspi-config
# Enable: SSH, I2C, SPI, Serial
# Set: Hostname, Timezone, WiFi

# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install python3-pip git screen htop \
  wireguard-tools fail2ban ufw python3-venv -y

# Install Meshtastic tools
python3 -m venv /home/pi/meshtastic-env
source /home/pi/meshtastic-env/bin/activate
pip install meshtastic pypubsub
```

### Secure Remote Access

#### Option 1: WireGuard VPN (Recommended)
```bash
# Generate keys
wg genkey | tee privatekey | wg pubkey > publickey

# Configure WireGuard
sudo nano /etc/wireguard/wg0.conf
```

```ini
[Interface]
PrivateKey = [YOUR_PRIVATE_KEY]
Address = 10.0.0.2/24
ListenPort = 51820

[Peer]
PublicKey = [SERVER_PUBLIC_KEY]
Endpoint = your-vpn-server.com:51820
AllowedIPs = 10.0.0.0/24
PersistentKeepalive = 25
```

#### Option 2: Tailscale (Easiest)
```bash
# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Authenticate
sudo tailscale up

# Get IP
ip addr show tailscale0
```

### Management Scripts

#### 1. Node Health Monitor (`/home/pi/scripts/health_check.py`)
```python
#!/usr/bin/env python3
import meshtastic
import meshtastic.serial_interface
import json
import requests
from datetime import datetime
import subprocess
import psutil

def get_system_stats():
    """Get Raspberry Pi system statistics"""
    return {
        "timestamp": datetime.now().isoformat(),
        "cpu_percent": psutil.cpu_percent(interval=1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent,
        "temperature": float(subprocess.check_output(
            ['vcgencmd', 'measure_temp']
        ).decode().split('=')[1].split("'")[0]),
        "uptime_hours": (datetime.now() - datetime.fromtimestamp(
            psutil.boot_time()
        )).total_seconds() / 3600
    }

def get_radio_stats():
    """Get Meshtastic radio statistics"""
    try:
        interface = meshtastic.serial_interface.SerialInterface()
        node = interface.getNode("^local")
        
        stats = {
            "battery_level": node.batteryLevel,
            "channel_utilization": node.deviceMetrics.channelUtilization,
            "air_util_tx": node.deviceMetrics.airUtilTx,
            "num_packets_tx": node.deviceMetrics.numPacketsTx,
            "num_packets_rx": node.deviceMetrics.numPacketsRx,
            "num_packets_bad": node.deviceMetrics.numPacketsBad,
            "node_num": node.num,
            "has_gps": node.position is not None,
            "num_online_nodes": len([n for n in interface.nodes.values() 
                                   if n.lastHeard > 0])
        }
        
        interface.close()
        return stats
    except Exception as e:
        return {"error": str(e)}

def send_telemetry(data):
    """Send telemetry to central server"""
    mqtt_payload = {
        "node_id": subprocess.check_output(['hostname']).decode().strip(),
        "system": get_system_stats(),
        "radio": get_radio_stats(),
        "timestamp": datetime.now().isoformat()
    }
    
    # Send via MQTT (if configured)
    # Or via HTTP POST
    try:
        response = requests.post(
            "https://your-server.com/api/telemetry",
            json=mqtt_payload,
            timeout=10,
            headers={"Authorization": "Bearer YOUR_API_KEY"}
        )
        return response.status_code == 200
    except:
        return False

if __name__ == "__main__":
    data = {
        "system": get_system_stats(),
        "radio": get_radio_stats()
    }
    print(json.dumps(data, indent=2))
    send_telemetry(data)
```

#### 2. Remote Command Executor (`/home/pi/scripts/remote_exec.py`)
```python
#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import subprocess
import json
import hashlib
import hmac

# Security: Only allow specific commands
ALLOWED_COMMANDS = {
    "reboot_radio": "meshtastic --reboot",
    "get_info": "meshtastic --info",
    "get_nodes": "meshtastic --nodes",
    "restart_service": "sudo systemctl restart meshtastic-mqtt",
    "update_firmware": "/home/pi/scripts/update_firmware.sh",
    "get_config": "meshtastic --export-config",
    "system_reboot": "sudo reboot",
    "get_logs": "journalctl -u meshtastic-mqtt -n 100"
}

SECRET_KEY = "YOUR_SECRET_KEY"

def verify_signature(payload, signature):
    """Verify HMAC signature for security"""
    expected = hmac.new(
        SECRET_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        
        # Verify signature
        if not verify_signature(data['command'], data['signature']):
            print("Invalid signature!")
            return
            
        command = data.get('command')
        
        if command in ALLOWED_COMMANDS:
            result = subprocess.run(
                ALLOWED_COMMANDS[command].split(),
                capture_output=True,
                text=True
            )
            
            response = {
                "command": command,
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
                "timestamp": datetime.now().isoformat()
            }
            
            # Publish response
            client.publish(
                f"alaska/aviation/response/{subprocess.check_output(['hostname']).decode().strip()}",
                json.dumps(response)
            )
    except Exception as e:
        print(f"Error: {e}")

# MQTT setup
client = mqtt.Client()
client.on_message = on_message
client.username_pw_set("node-user", "node-password")
client.connect("your-mqtt-server.com", 1883, 60)
client.subscribe(f"alaska/aviation/command/{subprocess.check_output(['hostname']).decode().strip()}")
client.loop_forever()
```

#### 3. Auto-Update Script (`/home/pi/scripts/auto_update.sh`)
```bash
#!/bin/bash

# Auto-update script for Meshtastic nodes
LOG_FILE="/var/log/meshtastic-update.log"
BACKUP_DIR="/home/pi/backups"

# Create backup directory
mkdir -p $BACKUP_DIR

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a $LOG_FILE
}

# Backup current configuration
log "Backing up configuration..."
meshtastic --export-config > "$BACKUP_DIR/config-$(date +%Y%m%d-%H%M%S).yaml"

# Check for firmware updates
log "Checking for firmware updates..."
CURRENT_VERSION=$(meshtastic --info | grep "Firmware version" | awk '{print $3}')
LATEST_VERSION=$(curl -s https://api.github.com/repos/meshtastic/firmware/releases/latest | grep tag_name | cut -d '"' -f 4)

if [ "$CURRENT_VERSION" != "$LATEST_VERSION" ]; then
    log "Update available: $CURRENT_VERSION -> $LATEST_VERSION"
    
    # Download firmware
    wget -O /tmp/firmware.uf2 \
        "https://github.com/meshtastic/firmware/releases/download/$LATEST_VERSION/firmware-tbeam-$LATEST_VERSION.uf2"
    
    # Flash firmware (device-specific)
    # meshtastic --firmware /tmp/firmware.uf2
    
    log "Firmware updated successfully"
else
    log "Firmware is up to date"
fi

# Update Python packages
log "Updating Python packages..."
source /home/pi/meshtastic-env/bin/activate
pip install --upgrade meshtastic

# Restart services
log "Restarting services..."
sudo systemctl restart meshtastic-mqtt
```

### Service Configuration

#### Meshtastic MQTT Bridge (`/etc/systemd/system/meshtastic-mqtt.service`)
```ini
[Unit]
Description=Meshtastic MQTT Bridge
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi
Environment="PATH=/home/pi/meshtastic-env/bin"
ExecStart=/home/pi/meshtastic-env/bin/python /home/pi/scripts/mqtt_bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Health Check Timer (`/etc/systemd/system/health-check.timer`)
```ini
[Unit]
Description=Run health check every 15 minutes
Requires=health-check.service

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
```

### Central Management Dashboard

Create a simple web interface to manage all nodes:

```python
# management_server.py
from flask import Flask, render_template, request, jsonify
import paho.mqtt.client as mqtt
import json
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)

@app.route('/')
def dashboard():
    """Main dashboard showing all nodes"""
    conn = sqlite3.connect('nodes.db')
    c = conn.cursor()
    
    nodes = c.execute('''
        SELECT node_id, last_seen, battery_level, 
               cpu_temp, uptime_hours, status
        FROM nodes
        WHERE last_seen > datetime('now', '-1 hour')
    ''').fetchall()
    
    return render_template('dashboard.html', nodes=nodes)

@app.route('/api/command', methods=['POST'])
def send_command():
    """Send command to specific node"""
    data = request.json
    node_id = data['node_id']
    command = data['command']
    
    # Sign the command
    signature = hmac.new(
        SECRET_KEY.encode(),
        command.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Publish to MQTT
    client.publish(
        f"alaska/aviation/command/{node_id}",
        json.dumps({
            "command": command,
            "signature": signature,
            "timestamp": datetime.now().isoformat()
        })
    )
    
    return jsonify({"status": "sent"})

@app.route('/api/nodes')
def get_nodes():
    """Get all node statuses"""
    conn = sqlite3.connect('nodes.db')
    c = conn.cursor()
    
    nodes = c.execute('''
        SELECT node_id, last_seen, battery_level, 
               latitude, longitude, channel_util,
               num_packets_rx, num_packets_tx
        FROM nodes
        ORDER BY last_seen DESC
    ''').fetchall()
    
    return jsonify([{
        "node_id": n[0],
        "last_seen": n[1],
        "battery_level": n[2],
        "position": {"lat": n[3], "lon": n[4]} if n[3] else None,
        "channel_util": n[5],
        "packets": {"rx": n[6], "tx": n[7]}
    } for n in nodes])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

### Monitoring and Alerts

#### Prometheus Configuration (`prometheus.yml`)
```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'meshtastic-nodes'
    static_configs:
      - targets: ['node1.tailscale:9090', 'node2.tailscale:9090']
```

#### Alert Rules (`alerts.yml`)
```yaml
groups:
  - name: meshtastic_alerts
    rules:
      - alert: NodeOffline
        expr: time() - node_last_seen > 3600
        for: 5m
        annotations:
          summary: "Node {{ $labels.node_id }} is offline"
          
      - alert: LowBattery
        expr: node_battery_level < 20
        for: 5m
        annotations:
          summary: "Node {{ $labels.node_id }} battery low: {{ $value }}%"
          
      - alert: HighTemperature
        expr: node_cpu_temp > 70
        for: 5m
        annotations:
          summary: "Node {{ $labels.node_id }} CPU temp high: {{ $value }}°C"
```

### Deployment Checklist

- [ ] Configure SSH keys for passwordless access
- [ ] Set up VPN or Tailscale
- [ ] Install and test management scripts
- [ ] Configure systemd services
- [ ] Set up monitoring and alerts
- [ ] Test remote command execution
- [ ] Document access credentials
- [ ] Create backup of configuration
- [ ] Test failover procedures

Ready to deploy these management tools on your nodes?
