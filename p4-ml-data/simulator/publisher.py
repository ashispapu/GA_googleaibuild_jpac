"""OT simulator (ML-5). Publishes TelemetryMessage JSON on MQTT at 1 Hz per asset.
Topics + tags: ../../config/tag_map.yaml. Listens to scene changes (calm/warning/crisis/resolved)."""
TOPIC = "plant/{plant_id}/{line_id}/{asset_id}/telemetry"

# TODO: paho-mqtt client -> mosquitto:1883
# TODO: per asset, baseline signals + noise; in "warning" ramp EQ-3012 vibration 2.1 -> 4.3 mm/s over 60 s
# TODO: when line speed is 70%, reduce vibration ramp and power
