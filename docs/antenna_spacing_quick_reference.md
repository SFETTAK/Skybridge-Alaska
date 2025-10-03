# Antenna Spacing Quick Reference

## At 915 MHz (US LoRa Band)

### Wavelength = 13 inches

## Minimum Spacing by Purpose

### 🔧 Basic Dual Coverage (2 antennas)
**Minimum**: 10 feet  
**Recommended**: 15-20 feet  
**Ideal**: 25-30 feet

### 📡 Triangulation Array (3 antennas)
**Minimum**: 20-foot triangle  
**Recommended**: 30-40 foot triangle  
**Optimal**: 50-foot triangle

## Visual Guide

```
POOR (Too Close):        GOOD:                  BEST:
   
[A1]--5ft--[A2]         [A1]----20ft----[A2]    [A1]------40ft------[A2]
                                                          
❌ Interference          ✅ Clean patterns       ✅ Optimal isolation
❌ Pattern distortion    ✅ Good coverage        ✅ Best triangulation
❌ Coupling issues       ✅ Manageable install   ✅ Professional grade
```

## Triangle Layout for SAR

```
         ⬆ North
         
      [3 dBi]
         *
        /|\
       / | \
      /  |  \
  30ft   |   30ft
    /    |    \
   /     |     \
  *------+------*
[8 dBi]  30ft  [5 dBi]

Bearing accuracy: ±10-15°
Coverage: 360° complete
```

## Cable Runs (LMR-400)

| Antenna Spacing | Max Cable Length | Signal Loss |
|----------------|------------------|-------------|
| 20 feet | 30 feet | 0.5 dB |
| 30 feet | 40 feet | 0.6 dB |
| 40 feet | 50 feet | 0.8 dB |
| 50 feet | 60 feet | 0.9 dB |

## Decision Tree

```
Do you need triangulation?
├─ NO → 2 antennas, 20ft apart
└─ YES → 3 antennas
         ├─ Limited space? → 20ft triangle minimum
         └─ Good space? → 40ft triangle optimal
```

## Install Tips

1. **Height variation**: Mount at different heights (reduces coupling)
2. **Cable quality**: Use LMR-400 minimum (1.5dB/100ft loss)
3. **Document positions**: GPS coordinates for each antenna
4. **Test iteratively**: Start closer, expand if needed

## Cost Estimate by Spacing

**20-foot spacing**: +$100-150 (cables & mounts)  
**40-foot spacing**: +$300-400 (better cables & structure)  
**60-foot spacing**: +$500-700 (professional grade)

Remember: **Better spacing = better performance**, but 20-30 feet is perfectly adequate to start!
