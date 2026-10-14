---
id: MAN-ROBOT-6
title: AR-6 Assembly Robot Maintenance Manual
model: AR-6 Assembly Robot
asset_type: robot
manufacturer: Motoko Robotics
---

<!-- page 5 -->
## 1 Safety
Put the controller in T1 (manual reduced speed) or switch it off and lock it out before entering the cell. Brakes hold
the axes; never release a brake without supporting the arm.

<!-- page 11 -->
## 2 Description
Six-axis assembly robot screwing gearbox covers. Each axis has a servo motor with a cooling fan (SRV-FAN-80) and an
RV reducer. Axis 2 carries the highest load. Nominal motor speed 3,000 rpm, power 4 kW.

<!-- page 15 -->
## 3 Sensors and normal values
| Signal | Normal | Warning | Trip |
|---|---|---|---|
| Axis 2 vibration RMS | 1.0–1.4 mm/s | 2.3 mm/s | 2.6 mm/s |
| Axis 2 motor temperature | 52–58 °C | 69 °C | 77 °C |
| Axis 2 motor current | 8–10 A | 11 A | 12 A |

<!-- page 40 -->
## 7.1 Servo overheating
A rising axis 2 motor temperature with higher current and almost unchanged vibration is the classic sign of a failing
servo cooling fan: the motor works the same but cannot shed heat. Check the fan (airflow, noise). Replace with
SRV-FAN-80. Overheating above 77 °C trips the drive and stops the cell. Required skill: electrical. Typical repair
time 1 h. See procedure 8.1.

<!-- page 43 -->
## 7.2 Reducer bearing wear
Rising vibration with kurtosis above 4 on axis 2 means the RV reducer bearing is wearing. Replace with RV-BRG-40 and
refill the reducer grease. Required skill: bearing_replacement. Typical repair time 4 h. See procedure 8.2.

<!-- page 46 -->
## 7.4 Calibration drift (misalignment)
Rising vibration with normal kurtosis and screw torque results drifting out of tolerance (2.9–3.3 Nm) mean the tool
centre point has drifted: the robot is out of calibration and the screwdriver hits the screw off-axis. Recalibrate
with CAL-KIT-AR6. Required skill: alignment. Typical repair time 1.5 h. See procedure 8.3.

<!-- page 52 -->
## 8.1 Replacing a servo fan (SRV-FAN-80)
1. Controller off, LOTO. 2. Remove the motor cover (4 screws). 3. Unplug and replace the fan SRV-FAN-80. 4. Run axis 2
for 20 min in the cycle: motor temperature must stay below 60 °C.

<!-- page 55 -->
## 8.2 Replacing the reducer bearing (RV-BRG-40)
1. Support the arm, LOTO. 2. Remove the axis 2 motor and the reducer. 3. Press out the bearing, press in RV-BRG-40.
4. Refill grease (Molywhite RE No. 00, 220 ml). 5. Reassemble, master the axis, run 30 min test program.

<!-- page 59 -->
## 8.3 Calibration (CAL-KIT-AR6)
1. Fit the calibration pin from CAL-KIT-AR6. 2. Run the 4-point TCP calibration program. 3. Accept when the error is
below 0.05 mm. 4. Run 10 cycles and check screw torque (3.1 ± 0.2 Nm).

<!-- page 64 -->
## 9 Spare parts
SRV-FAN-80 servo fan · RV-BRG-40 reducer bearing · CAL-KIT-AR6 calibration kit.
