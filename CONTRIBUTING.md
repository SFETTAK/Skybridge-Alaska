# Contributing to SkyBridge Alaska

We welcome contributions. By contributing, you're helping improve aviation safety in Alaska and beyond.

## Contributor License Agreement (CLA)

All contributors must sign our CLA before pull requests can be merged. This enables dual licensing (AGPL-3.0 and commercial).

**Why:** Allows commercial licenses to fund development while keeping the core system free for state agencies, SAR organizations, and small operators.

**How:** CLA will be presented automatically on your first pull request via GitHub.

## Getting Started

```bash
git clone https://github.com/SFETTAK/Skybridge-Alaska.git
cd Skybridge-Alaska
```

### Repository Structure

| Directory | What's There | Language |
|-----------|-------------|----------|
| `ground-station/scripts/` | VHF pipeline, dashboard, backup | Python |
| `ground-station/systemd/` | Service and timer unit files | systemd INI |
| `ground-station/config/` | OpenWebRX, readsb, SSH, fail2ban | JSON/conf |
| `app/` | Mobile app (early scaffold) | React Native / JS |
| `protocol/` | TAIGA ASN.1 protocol docs | Markdown |
| `hardware/` | Hardware specifications | Markdown |
| `docs/` | Project documentation | Markdown |

### Running the Test Suite

The VHF pipeline has a 17-test validation suite that runs without SDR hardware:

```bash
# On a Pi or any Linux box with Python 3 + dependencies
python3 -m venv venv
source venv/bin/activate
pip install numpy faster-whisper
python ground-station/scripts/test-pipeline.py
```

## Code Standards

### Python (Ground Station)
- PEP 8 style
- Type hints where it helps readability
- numpy for DSP, faster-whisper for STT
- Test with `test-pipeline.py` before submitting

### JavaScript/TypeScript (Mobile App)
- ESLint + Prettier (config in `app/`)
- React Native best practices

### Configuration Files
- Comment any non-obvious settings
- Include the default value in comments when overriding

## Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Test on hardware if possible, or run the test suite
4. Commit with a clear message
5. Open a Pull Request describing what and why

## Reporting Issues

- Check existing issues first
- Include: steps to reproduce, hardware/software versions, relevant logs
- For SDR issues: include `lsusb` output and `rtl_test` results

## Questions

- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: Technical questions and design conversations

## Recognition

Contributors are recognized in project announcements and the annual safety impact report.
