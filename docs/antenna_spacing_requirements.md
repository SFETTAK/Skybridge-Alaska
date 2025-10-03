# Antenna Spacing Requirements for Multi-Antenna Arrays

## Quick Answer
**Minimum: 1 wavelength (13 inches at 915 MHz)**
**Recommended: 10-20 feet for basic setup**
**Optimal for triangulation: 30-50 feet**

## The Physics Behind Spacing

### Wavelength at 915 MHz
```
Wavelength (λ) = Speed of Light / Frequency
λ = 300,000,000 m/s / 915,000,000 Hz
λ = 0.328 meters = 13 inches
```

### Minimum Spacing Requirements

#### 1. Avoid Destructive Interference
- **Absolute minimum**: 0.5λ (6.5 inches)
- **Better**: 1λ (13 inches)
- **Recommended minimum**: 2λ (26 inches)

#### 2. Prevent Coupling
- **Physical separation**: 5λ (5.5 feet) reduces coupling to -20dB
- **Practical minimum**: 10λ (11 feet) for -30dB isolation
- **Professional standard**: 20λ (22 feet) for -40dB isolation

## Configuration-Specific Requirements

### Dual Antenna Setup (High/Low Gain)

```
Building Roof View:
┌─────────────────────────────────┐
│                                 │
│  [8 dBi]          [3 dBi]      │
│     ↑                ↑         │
│     └────── 15-20 ft ──┘       │
│                                 │
└─────────────────────────────────┘

Minimum: 10 feet
Recommended: 15-20 feet
Maximum benefit: 25-30 feet
```

**Why this spacing:**
- Prevents pattern distortion
- Allows independent operation
- Reduces near-field effects

### Triangulation Array (3 Antennas)

```
Optimal Triangular Layout:
           
         [Ant 1]
           ○ (3 dBi)
          /|\
         / | \
        /  |  \
    30ft   |   30ft
      /    |    \
     /     |     \
    /      |      \
   ○───────┴───────○
[Ant 2]  30 ft   [Ant 3]
(8 dBi)          (5 dBi)

Equilateral triangle
30-50 feet per side
```

**For triangulation accuracy:**
- **Minimum**: 20 feet (6 meters) - Poor accuracy
- **Good**: 30-40 feet (9-12 meters) - ±15° bearing accuracy
- **Optimal**: 50+ feet (15+ meters) - ±10° bearing accuracy
- **Maximum useful**: 100 feet (30 meters) - Diminishing returns

## Practical Considerations

### 1. Roof Space Constraints

**Limited Space Solution:**
```
Linear Array (if triangular not possible):
[8 dBi]────20ft────[5 dBi]────20ft────[3 dBi]

Total span: 40 feet
Still provides bearing info (less accurate)
```

### 2. Cabling Considerations

**Signal Loss per 100ft:**
- RG-58: 4.5 dB (avoid)
- RG-8/LMR-240: 2.5 dB (acceptable)
- LMR-400: 1.5 dB (recommended)
- LMR-600: 0.9 dB (best)

**Rule of thumb**: Keep cable runs under 50 feet

### 3. Height Differences

```
Side View:
         [High gain]
              |
              |  10ft
    [Medium]  |
         |    |
         |────┴──── Roof level
    5ft  |
         |
    ─────┴───────── 
    
Vertical separation helps too!
Different heights reduce coupling
```

## Installation Scenarios

### Scenario 1: Alaska Airmen's Building (Large Roof)
```
Recommended Layout:
- 3 antennas in triangle
- 40-foot sides
- Center accessible for equipment
- Total area: ~700 sq ft
```

### Scenario 2: DOT Building (Limited Space)
```
Alternative Layout:
- Linear array along roof edge
- 25-foot spacing
- Total span: 50 feet
- Mount on parapet wall
```

### Scenario 3: Mountain Top Site
```
Optimal Layout:
- Maximum spacing (50-100 ft)
- Use natural terrain
- Solar panels between antennas
- Weather-resistant enclosures
```

## Effects of Spacing on Performance

### Too Close (<10 feet):
- ❌ Pattern distortion
- ❌ Reduced gain
- ❌ Phase interference
- ❌ Poor triangulation

### Optimal (30-50 feet):
- ✅ Clean patterns
- ✅ Full gain realized
- ✅ Good triangulation
- ✅ Minimal coupling

### Too Far (>100 feet):
- ⚠️ Long cable runs
- ⚠️ Sync timing issues
- ⚠️ Higher installation cost
- ⚠️ Maintenance challenges

## Quick Decision Guide

### For Coverage Only (2 antennas):
- **Budget install**: 15 feet minimum
- **Good install**: 20-25 feet
- **Best install**: 30+ feet

### For Triangulation (3 antennas):
- **Minimum viable**: 20 feet triangle
- **Recommended**: 30-40 feet triangle
- **Optimal**: 50 feet triangle

### Cable Length Formula:
```
Max cable length = 50ft - (Antenna spacing / 2)

Example: 40ft spacing
Max cable = 50 - (40/2) = 30 feet to radio
```

## Cost Impact of Spacing

### 20-foot spacing:
- Cable: ~$50
- Mounting: Basic
- **Total added cost**: $100

### 40-foot spacing:
- Cable: ~$150 (LMR-400)
- Mounting: Reinforced
- **Total added cost**: $300

### 60-foot spacing:
- Cable: ~$300 (LMR-600)
- Mounting: Professional
- **Total added cost**: $500+

## Recommendations by Use Case

### 1. Basic Communications Only
- 2 antennas
- 15-20 feet apart
- Simple installation

### 2. Enhanced Coverage
- 2-3 antennas
- 25-30 feet apart
- Optimized patterns

### 3. Search and Rescue Capability
- 3 antennas
- 40-50 feet triangle
- GPS time sync
- Professional installation

## Special Considerations for Alaska

### Wind Loading
- Greater spacing = more structure
- Ice buildup on cables
- Use flexible mounts

### Permafrost/Thermal
- Thermal expansion of mounts
- Seasonal ground movement
- Allow for flex in system

### Wildlife
- Birds perching between antennas
- Use deterrents if needed
- Regular inspection

## The Bottom Line

**For your initial deployment:**
1. Start with 20-25 feet (good coverage, manageable)
2. Plan for 30-40 feet if you want triangulation
3. Use LMR-400 cable or better
4. Mount at different heights if possible
5. Document exact positions for algorithm tuning

Remember: You can always start with closer spacing and move them apart later as you refine the system!
