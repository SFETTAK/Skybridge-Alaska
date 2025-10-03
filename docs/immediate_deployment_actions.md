# Immediate Deployment Actions - Post-NASAO

## Current Status
✅ Collins Aerospace/Raytheon interested - engineer reviewing
✅ Alaska Airmen's Association president on board
✅ Two node locations secured (AAA & DOTHQ)
✅ Rokland contacted for radio recommendations

## Week 1 Priority Actions

### 1. Hardware Procurement (Upon Rokland Response)
**Fixed Nodes (2 units)**:
- Raspberry Pi 4B (4GB) or Pi 5
- High-gain antennas (6-8 dBi)
- Weatherproof enclosures
- PoE adapters or 12V power supplies
- Industrial SD cards (32GB+)

**Pilot Radios (10-20 units for Nenana)**:
- Based on Rokland recommendations
- Include variety for testing (T-Echo, T-Beam, Heltec)
- Extra batteries/cases
- USB cables for each

**Budget Estimate**: $2,000-3,000 total

### 2. Infrastructure Setup

#### MQTT Server (This Week)
```bash
# Quick cloud setup on DigitalOcean/AWS
# $10-20/month for small instance
# Can migrate to DOT server later
```

Key features:
- SSL/TLS enabled
- Authentication configured  
- Grafana dashboard
- Automated backups

#### Remote Access
- Set up Tailscale for easy management
- Configure SSH keys
- Document access procedures

### 3. Pre-Configuration Tasks

#### Radio Firmware
- Flash latest Meshtastic stable
- Test each device
- Document device IDs

#### Standard Configuration
```yaml
# Alaska Aviation Channel
channel_name: "AK-Aviation"
region: US
modem_preset: LONG_FAST
hop_limit: 7

# Node-specific
mqtt_server: "your-server.com"
mqtt_enabled: true
telemetry_interval: 900
```

### 4. Deployment Schedule

**Week 1**:
- Order hardware
- Set up MQTT server
- Create monitoring dashboard

**Week 2**:
- Receive and configure radios
- Install node at Alaska Airmen's
- Test connectivity

**Week 3**:
- Install node at DOTHQ
- Distribute radios to Nenana pilots
- Begin telemetry collection

**Week 4**:
- Analyze initial data
- Gather pilot feedback
- Plan expansion

### 5. Documentation Package

For Alaska Airmen's Association:
- [ ] Pilot Quick Start Guide (✅ Created)
- [ ] Node host responsibilities
- [ ] Troubleshooting guide
- [ ] Contact information

For Technical Review (Collins/Raytheon):
- [ ] Technical architecture diagram
- [ ] Integration possibilities
- [ ] Scalability analysis
- [ ] Standards compliance

### 6. Testing Protocol

Initial metrics to track:
- Message delivery rate
- Coverage maps
- Node uptime
- Battery life
- User engagement

Feedback collection:
- Simple Google Form
- Weekly check-ins
- Issue tracker

### 7. Quick Wins for Demonstration

1. **Basic Messaging**: Working immediately
2. **Position Sharing**: Visible on map
3. **Range Testing**: Document achievements
4. **Reliability**: Track uptime

### 8. Risk Mitigation

**Technical**:
- Backup radios for key users
- Redundant node at DOTHQ
- Remote reset capability

**User Adoption**:
- Personal handoff to pilots
- Follow-up support
- Success stories

**Regulatory**:
- Part 15 compliance docs ready
- No aviation data initially
- Supplemental use only emphasis

## Key Contacts Management

Create tracking sheet:
- JJ at Collins Aerospace
- Engineer reviewer at Raytheon  
- Alaska Airmen's president
- Adam (node host at AAA)
- Rokland contact
- Nenana pilot coordinator

## Success Metrics (30 days)

- [ ] 2 nodes operational 24/7
- [ ] 20+ active pilots
- [ ] 1000+ messages exchanged
- [ ] Coverage map documented
- [ ] Zero major outages
- [ ] Positive pilot feedback

## Collins/Raytheon Integration Ideas

Prepare discussion points:
1. **ADS-B Integration**: Mesh + existing traffic systems
2. **Weather Station Network**: Automated reporting
3. **Emergency Locator**: Backup to ELT
4. **Fleet Management**: Commercial operator benefits
5. **Government Contracts**: FAA/DOD applications

## Communication Plan

**Internal**:
- Daily MQTT monitoring
- Weekly team sync
- Issue escalation path

**External**:
- Bi-weekly pilot newsletter
- Monthly AAA presentation
- Quarterly stakeholder update

---

**Remember**: This is a pilot program. Focus on:
1. Reliability over features
2. User experience over complexity  
3. Documentation over assumptions
4. Safety always

Ready to execute! 🚀
