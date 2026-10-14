---
id: MAN-SPINDLE-200
title: CNC Spindle 200 Maintenance Manual
model: CNC Spindle 200
asset_type: spindle
manufacturer: Kessler Spindeltechnik
---

<!-- page 3 -->
## 1 Safety
Before any work on the spindle, stop the line at the HMI, lock out the main isolator and the spindle drive (LOTO tag
on both), and wait until the spindle has stopped completely. The spindle housing can stay above 60 °C for 30 minutes
after a run; wear heat-resistant gloves. Never run the spindle with the guard open.

<!-- page 9 -->
## 2 Description
The CNC Spindle 200 is a belt-driven motor spindle for drilling and boring housings. The shaft runs on a front
angular contact bearing pair (7008) and a rear deep groove ball bearing (6205-2RS). A 24 V coolant pump circulates
coolant through the spindle jacket. Nominal speed 12,000 rpm, nominal power 11 kW, nominal vibration 2.1 mm/s RMS,
bearing temperature 55–60 °C, motor temperature 60–65 °C.

<!-- page 12 -->
## 3 Sensors and normal values
| Signal | Tag | Normal | Warning | Trip |
|---|---|---|---|---|
| Vibration RMS | SPDL.VIB_RMS | 2.0–2.3 mm/s | 4.0 mm/s | 4.5 mm/s sustained 30 min |
| Kurtosis | SPDL.VIB_KURT | 2.8–3.3 | 4.0 | — |
| Bearing temperature | SPDL.BRG_T | 55–60 °C | 72 °C | 80 °C |
| Motor temperature | SPDL.MTR_T | 60–65 °C | 76 °C | 84 °C |
Vibration and power fall with spindle speed; compare readings at the same speed.

<!-- page 31 -->
## 5 Preventive maintenance
Every 500 h: grease the rear bearing (12 g Kluber Isoflex NBU 15), check belt tension. Every 2,000 h: check coupling
alignment with the dial gauge (max 0.02 mm), replace coolant filter. Every 4,000 h or when vibration trends upward:
replace the rear bearing 6205-2RS (SKF-6205).

<!-- page 54 -->
## 7.1 Reading the vibration trend
A healthy spindle stays within ±10% of its baseline vibration. A steady rise of RMS together with kurtosis above 4
points to a rolling-element (bearing) defect: the impacts make the signal impulsive. A rise of RMS with kurtosis that
stays near 3 points to misalignment or unbalance. A rise of temperature with little vibration change points to a
cooling or lubrication problem (overheating).

<!-- page 58 -->
## 7.4 Rear bearing wear
If vibration RMS rises more than 30% within 24 h together with kurtosis above 4, inspect the rear bearing (6205-2RS). Continued operation above 4.5 mm/s may cause seizure within 48–96 h. Reducing spindle speed to 70% lowers bearing load and extends safe run time.
Replacement part: SKF-6205 (deep groove ball bearing 6205-2RS). Required skill: bearing_replacement. Typical repair
time 2.5 h. See procedure 8.2.

<!-- page 61 -->
## 7.6 Misalignment
Misalignment shows as high vibration at 2× running speed with normal kurtosis.
Check the spindle-to-motor coupling with the dial gauge; more than 0.05 mm offset needs realignment with the shim kit
ALN-KIT-200. A misaligned coupling also loads the rear bearing, so a misalignment left too long can look like bearing
wear; if the bearing looks good when removed, check the alignment before restarting. Required skill: alignment.
Typical repair time 1.5 h. See procedure 8.4.

<!-- page 63 -->
## 7.8 Overheating
If the motor temperature rises above 76 °C while vibration stays normal, the coolant pump (CLT-PUMP-24) is the most
likely cause: check coolant flow at the return line (normal 6 l/min). Low flow with a clean filter means the pump is
worn. Do not run above 84 °C: the spindle drive trips and the bearing grease degrades, which leads to bearing wear.
Required skill: electrical. Typical repair time 2 h. See procedure 8.5.

<!-- page 70 -->
## 8.2 Replacing the rear bearing (SKF-6205)
1. Lock out the line (section 1). 2. Remove the belt guard and the drive belt (BLT-HTD-8M). 3. Pull the rear bearing
cap (4× M6). 4. Extract the bearing with puller SP-200-3; never hammer the shaft. 5. Clean the seat, check the shaft
journal for scoring (max 0.01 mm). 6. Heat the new SKF-6205 to 80 °C and fit it. 7. Grease 12 g, refit the cap
(torque 10 Nm), refit the belt (tension 140 Hz). 8. Run-in: 10 min at 30%, 10 min at 70%, then full speed. Vibration
must be below 2.5 mm/s and kurtosis below 3.5 after run-in.

<!-- page 74 -->
## 8.4 Coupling realignment (ALN-KIT-200)
1. Lock out the line. 2. Mount the dial gauges on the coupling. 3. Measure offset and angularity at 0°, 90°, 180°,
270°. 4. Shim the motor feet with ALN-KIT-200 shims until the offset is below 0.02 mm. 5. Torque the motor bolts to
45 Nm in a cross pattern and re-measure. 6. Run-in as in 8.2; vibration must fall back to the baseline.

<!-- page 77 -->
## 8.5 Replacing the coolant pump (CLT-PUMP-24)
1. Lock out the line and the 24 V supply. 2. Close both coolant valves, drain the jacket. 3. Disconnect the pump
(2 hoses, 1 connector). 4. Fit CLT-PUMP-24, refill coolant, bleed the jacket. 5. Check flow (6 l/min) and run 20 min:
motor temperature must settle below 66 °C.

<!-- page 82 -->
## 9 Spare parts
SKF-6205 rear bearing · SKF-7008 front bearing (pair) · BLT-HTD-8M drive belt · ALN-KIT-200 alignment shim kit ·
CLT-PUMP-24 coolant pump.
