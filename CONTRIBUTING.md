# Contributing to SkyBridge Alaska

We welcome contributions to the SkyBridge project! By contributing, you're helping improve aviation safety in Alaska and beyond.

## Contributor License Agreement (CLA)

**Important**: All contributors must sign our CLA before pull requests can be merged. This allows SkyBridge to maintain dual licensing (AGPL-3.0 and commercial).

### Why we need a CLA
- Enables us to offer commercial licenses to fund development
- Allows free licenses for State of Alaska, SAR organizations, and small operators
- Protects the project's ability to evolve its licensing if needed

### How to sign the CLA
1. Read the full CLA at: https://skybridgealaska.net/cla (coming soon)
2. Sign electronically via GitHub (automated on first PR)
3. One-time process per contributor

## Development Process

### Setting up your environment
```bash
git clone https://github.com/SFETTAK/Skybridge-Alaska.git
cd Skybridge-Alaska
# See /docs/development.md for detailed setup
```

### Code contributions
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to your branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Reporting issues
- Check existing issues first
- Include steps to reproduce
- Specify hardware/software versions
- Attach logs if applicable

## Code Standards

### General
- Clear, self-documenting code
- Comments for complex logic
- Follow existing patterns

### C/C++ (Firmware)
- Arduino style for Meshtastic compatibility
- Memory-conscious (embedded systems)

### JavaScript/TypeScript (App)
- ESLint configuration provided
- React Native best practices

### Python (Tools)
- PEP 8 style
- Type hints where applicable

## Testing

- Test on actual hardware when possible
- Include unit tests for new features
- Document test procedures

## Documentation

- Update README.md if needed
- Add inline code documentation
- Create /docs entries for new features

## Questions?

- GitHub Discussions: Technical questions
- Email: dev@skybridgealaska.net (coming soon)

## Recognition

Contributors will be recognized in:
- CONTRIBUTORS.md file
- Project announcements
- Annual safety impact report

Thank you for helping make Alaska's skies safer!
