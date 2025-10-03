# Remote Flashing Station Setup
## Raspberry Pi at Alaska Airmen's Association

### Concept
Set up a Raspberry Pi at Alaska Airmen's that allows you to remotely flash and configure Meshtastic radios. Pilots can simply plug in their radios via USB, and you handle everything remotely.

### Hardware Required

#### At Alaska Airmen's:
- **Raspberry Pi 4B** (4GB minimum)
- **Powered USB Hub** (7+ ports for multiple radios)
- **32GB SD Card** (reliable brand)
- **Ethernet Connection** (more stable than WiFi)
- **HDMI Dummy Plug** (prevents sleep issues)
- **Label Maker** (for device IDs)
- **Simple Instructions Card** for pilots

#### Optional but Helpful:
- USB extension cables (easier access)
- Numbered USB ports (for identification)
- Webcam (to see what's plugged in)
- Small UPS (prevent interruptions)

### Software Setup

#### 1. Base System Install
```bash
# Flash Raspberry Pi OS Lite (64-bit)
# Enable SSH during imaging

# First boot configuration
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip git screen tmux usbutils \
  python3-venv python3-dev build-essential -y

# Install remote access (choose one)
# Option A: Tailscale (easiest)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Option B: WireGuard VPN
sudo apt install wireguard-tools
```

#### 2. Meshtastic Tools Installation
```bash
# Create virtual environment
python3 -m venv /home/pi/meshtastic-env
source /home/pi/meshtastic-env/bin/activate

# Install Meshtastic CLI and firmware tools
pip install --upgrade pip
pip install meshtastic esptool adafruit-nrfutil pyserial

# Create working directories
mkdir -p ~/radios/{firmware,configs,logs}
cd ~/radios
```

#### 3. USB Device Management Script
```python
#!/usr/bin/env python3
# save as: /home/pi/radios/usb_monitor.py

import subprocess
import json
import time
from datetime import datetime

def get_usb_devices():
    """List all USB serial devices"""
    try:
        result = subprocess.run(['ls', '/dev/ttyUSB*', '/dev/ttyACM*'], 
                              capture_output=True, text=True)
        devices = result.stdout.strip().split('\n')
        return [d for d in devices if d]
    except:
        return []

def get_device_info(port):
    """Get device information using meshtastic CLI"""
    try:
        result = subprocess.run(
            ['meshtastic', '--port', port, '--info'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except:
        return None

def monitor_devices():
    """Monitor USB devices and log changes"""
    known_devices = set()
    
    while True:
        current_devices = set(get_usb_devices())
        
        # Check for new devices
        new_devices = current_devices - known_devices
        for device in new_devices:
            print(f"[{datetime.now()}] New device: {device}")
            info = get_device_info(device)
            if info:
                with open(f"logs/device_{device.replace('/', '_')}.log", 'w') as f:
                    f.write(info)
        
        # Check for removed devices
        removed_devices = known_devices - current_devices
        for device in removed_devices:
            print(f"[{datetime.now()}] Removed device: {device}")
        
        known_devices = current_devices
        time.sleep(2)

if __name__ == "__main__":
    monitor_devices()
```

#### 4. Remote Flashing Script
```bash
#!/bin/bash
# save as: /home/pi/radios/flash_radio.sh

PORT=$1
FIRMWARE=$2
CONFIG=$3

if [ -z "$PORT" ] || [ -z "$FIRMWARE" ]; then
    echo "Usage: $0 <port> <firmware> [config]"
    exit 1
fi

echo "=== Flashing device on $PORT with $FIRMWARE ==="

# Flash firmware
esptool.py --port $PORT --baud 921600 --before default_reset \
  --after hard_reset --chip esp32 write_flash -z \
  --flash_mode dio --flash_freq 80m --flash_size detect \
  0x10000 firmware/$FIRMWARE

# Wait for device to reboot
sleep 5

# Apply configuration if provided
if [ ! -z "$CONFIG" ]; then
    echo "=== Applying configuration ==="
    meshtastic --port $PORT --configure configs/$CONFIG
fi

echo "=== Flash complete ==="
```

#### 5. Web Interface (Optional)
```python
# save as: /home/pi/radios/web_interface.py
from flask import Flask, render_template, jsonify, request
import subprocess
import os

app = Flask(__name__)

@app.route('/')
def index():
    return '''
    <html>
    <head><title>Alaska Airmen's Flashing Station</title></head>
    <body>
        <h1>Meshtastic Radio Flashing Station</h1>
        <h2>Connected Devices:</h2>
        <div id="devices"></div>
        <script>
            setInterval(() => {
                fetch('/api/devices')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('devices').innerHTML = 
                            data.map(d => `<div>${d}</div>`).join('');
                    });
            }, 2000);
        </script>
    </body>
    </html>
    '''

@app.route('/api/devices')
def get_devices():
    devices = subprocess.run(['ls', '/dev/ttyUSB*', '/dev/ttyACM*'], 
                           capture_output=True, text=True).stdout.strip().split('\n')
    return jsonify([d for d in devices if d])

@app.route('/api/flash', methods=['POST'])
def flash_device():
    data = request.json
    port = data.get('port')
    firmware = data.get('firmware')
    
    # Security: Validate inputs
    if not port.startswith('/dev/tty'):
        return jsonify({'error': 'Invalid port'}), 400
    
    # Run flash script
    result = subprocess.run(
        ['/home/pi/radios/flash_radio.sh', port, firmware],
        capture_output=True, text=True
    )
    
    return jsonify({
        'success': result.returncode == 0,
        'output': result.stdout,
        'error': result.stderr
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

### Setup at Alaska Airmen's

#### Physical Setup:
1. **Location**: Near a desk where pilots can easily access
2. **Power**: Reliable outlet, surge protector
3. **Network**: Ethernet cable to their network
4. **USB Hub**: Mounted or secured to desk
5. **Instructions**: Laminated card with simple steps

#### Pilot Instructions Card:
```
=== MESHTASTIC RADIO SETUP ===

1. Turn OFF your radio
2. Plug USB cable into any numbered port
3. Text Steve: "Radio plugged into port #X"
4. Wait for "Complete" text (5-10 minutes)
5. Unplug and turn on radio
6. Done! Your radio is configured

Questions? Text: XXX-XXX-XXXX
```

### Remote Management Workflow

#### 1. SSH Connection
```bash
# Connect via Tailscale
ssh pi@alaska-airmen

# Or via direct IP (if VPN setup)
ssh pi@10.x.x.x
```

#### 2. Check Connected Devices
```bash
# List USB devices
ls /dev/ttyUSB* /dev/ttyACM*

# Get device info
meshtastic --port /dev/ttyUSB0 --info
```

#### 3. Flash Single Device
```bash
# Flash with latest firmware
./flash_radio.sh /dev/ttyUSB0 firmware-tbeam-1.3.0.uf2

# Flash and configure
./flash_radio.sh /dev/ttyUSB0 firmware-tbeam-1.3.0.uf2 alaska-aviation.yaml
```

#### 4. Batch Configuration
```python
#!/usr/bin/env python3
# Batch configure all connected radios

import subprocess
import time

# Standard Alaska Aviation config
config = {
    "lora.region": "US",
    "lora.modem_preset": "LONG_FAST", 
    "lora.hop_limit": 7,
    "bluetooth.enabled": True,
    "position.gps_enabled": True,
    "position.position_broadcast_secs": 300,
    "telemetry.device_update_interval": 900,
    "telemetry.environment_update_interval": 900
}

# Get all connected devices
devices = subprocess.run(['ls', '/dev/ttyUSB*'], 
                        capture_output=True, text=True).stdout.strip().split('\n')

for device in devices:
    if device:
        print(f"Configuring {device}...")
        for key, value in config.items():
            cmd = ['meshtastic', '--port', device, '--set', f'{key}', str(value)]
            subprocess.run(cmd)
        time.sleep(2)

print("All devices configured!")
```

### Security Considerations

1. **Network Security**:
   - Use VPN or Tailscale (no port forwarding)
   - Strong passwords
   - Firewall rules

2. **Physical Security**:
   - Trusted location only
   - Consider locking USB devices
   - Log all access

3. **Operational Security**:
   - Only run approved firmware
   - Validate all commands
   - Keep audit logs

### Troubleshooting

**Device Not Detected**:
- Check USB cable
- Try different port
- Reboot device
- Check dmesg logs

**Flash Fails**:
- Device might be locked
- Wrong firmware file
- USB power issues
- Try slower baud rate

**Can't Connect Remotely**:
- Check Tailscale/VPN status
- Verify Pi is online
- Check firewall rules

### Automation Ideas

1. **Auto-Flash on Connect**: Detect new devices and flash automatically
2. **SMS Notifications**: Send pilot confirmation when complete
3. **QR Code Labels**: Scan to see device config
4. **Progress Display**: LCD showing current status

This setup lets you manage radio configuration remotely while pilots simply plug in their devices at Alaska Airmen's. Perfect for scalable deployment!
