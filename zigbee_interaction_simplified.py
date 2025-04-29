import asyncio
import logging
import os
import sys
import sqlite3
from datetime import datetime

from bellows.zigbee.application import ControllerApplication
from zigpy.zcl.clusters.measurement import TemperatureMeasurement, RelativeHumidity

# Database file path for storing sensor readings
DB_PATH = "sensor_data.db"
# Zigbee network persistence database (to retain network info between restarts)
ZIGBEE_DB_PATH = "zigbee_network.db"
# Serial port and baud rate for the Zigbee coordinator (ZBT-1 controller)
ZIGBEE_PORT = "/dev/ttyUSB0"
ZIGBEE_BAUD = 115200

# Configure logging to include timestamps
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")

# Initialize SQLite database (create table if it doesn't exist)
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ieee        TEXT,
    device_name TEXT,
    temperature REAL,
    humidity    REAL,
    timestamp   TEXT
)
""")
conn.commit()

class MainListener:
    """Listener for Zigbee device events to trigger data reads."""
    def __init__(self):
        self.stored_count = 0  # count of readings stored this cycle

    def device_initialized(self, device):
        """Called when a Zigbee device is fully initialized (joined and set up)."""
        ieee_str = str(device.ieee)
        model = getattr(device, "model", "<unknown>")
        logging.info(f"Device initialized: IEEE={ieee_str}, Model={model}")
        # Create an asyncio task to read sensor data (temperature and humidity)
        asyncio.create_task(self.read_device_data(device))

    async def read_device_data(self, device):
        """Read temperature and humidity once from the device and store in DB."""
        ieee_str = str(device.ieee)
        # Find temperature and humidity measurement clusters on the device
        temp_cluster = hum_cluster = None
        for ep_id, ep in device.endpoints.items():
            if ep_id == 0:  # skip ZDO endpoint
                continue
            if TemperatureMeasurement.cluster_id in ep.in_clusters:
                temp_cluster = ep.in_clusters[TemperatureMeasurement.cluster_id]
            if RelativeHumidity.cluster_id in ep.in_clusters:
                hum_cluster = ep.in_clusters[RelativeHumidity.cluster_id]
            if temp_cluster and hum_cluster:
                break  # found both clusters

        if not temp_cluster and not hum_cluster:
            logging.warning(f"No temperature/humidity cluster on device {ieee_str}")
            return

        temperature = humidity = None
        # Read temperature (ZCL attribute "measured_value" of TemperatureMeasurement cluster)
        if temp_cluster:
            try:
                result = await temp_cluster.read_attributes(["measured_value"])
                if result is None:
                    logging.warning(f"No temperature data from device {ieee_str}")
                elif isinstance(result, dict):
                    # Zigpy returns a dict of attribute values on success
                    raw_temp = result.get("measured_value")
                    if raw_temp is not None:
                        temperature = raw_temp / 100.0  # value is in centi-degrees
                elif isinstance(result, (list, tuple)):
                    # In some cases, result might be a list/tuple; extract any dict from it
                    for item in result:
                        if isinstance(item, dict) and "measured_value" in item:
                            raw_temp = item["measured_value"]
                            if raw_temp is not None:
                                temperature = raw_temp / 100.0
                            break
            except Exception as e:
                logging.error(f"Error reading temperature from {ieee_str}: {e}")

        # Read humidity (attribute "measured_value" of RelativeHumidity cluster)
        if hum_cluster:
            try:
                result = await hum_cluster.read_attributes(["measured_value"])
                if result is None:
                    logging.warning(f"No humidity data from device {ieee_str}")
                elif isinstance(result, dict):
                    raw_hum = result.get("measured_value")
                    if raw_hum is not None:
                        humidity = raw_hum / 100.0  # value is in hundredths of %
                elif isinstance(result, (list, tuple)):
                    for item in result:
                        if isinstance(item, dict) and "measured_value" in item:
                            raw_hum = item["measured_value"]
                            if raw_hum is not None:
                                humidity = raw_hum / 100.0
                            break
            except Exception as e:
                logging.error(f"Error reading humidity from {ieee_str}: {e}")

        # Store in database if we got any value (temperature or humidity)
        if temperature is not None or humidity is not None:
            device_name = getattr(device, "model", "SNZB-02D")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                cursor.execute(
                    "INSERT INTO readings (ieee, device_name, temperature, humidity, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (ieee_str, device_name, temperature, humidity, timestamp)
                )
                conn.commit()
                self.stored_count += 1
                logging.info(f"Stored reading for {ieee_str}: Temp={temperature}°C, Hum={humidity}% at {timestamp}")
            except Exception as e:
                logging.error(f"Failed to store data for {ieee_str}: {e}")
        else:
            logging.warning(f"No sensor data retrieved from device {ieee_str}")

async def run_cycle():
    """Run one 15-minute cycle of network initialization and data collection."""
    # Start the Zigbee controller application (using Bellows)
    config = {
        "database_path": ZIGBEE_DB_PATH,
        "device": {"path": ZIGBEE_PORT, "baudrate": ZIGBEE_BAUD}
    }
    logging.info(f"Initializing Zigbee network on {ZIGBEE_PORT} ...")
    try:
        app = await ControllerApplication.new(config)
    except Exception as e:
        logging.error(f"Failed to start Zigbee controller: {e}")
        return 0

    # Set up listener for device initialization events
    listener = MainListener()
    app.add_listener(listener)

    # Allow sensors to join or rejoin the network for 15 minutes
    try:
        await app.permit(900)
    except Exception as e:
        logging.error(f"Error enabling join mode: {e}")

    logging.info("Waiting up to 15 minutes for SNZB-02D devices to initialize...")
    await asyncio.sleep(900)  # wait 15 minutes

    # Grace period: ensure any last attribute reads complete
    await asyncio.sleep(2)

    # Shutdown the Zigbee application (close network)
    logging.info("Shutting down Zigbee network...")
    try:
        if hasattr(app, "shutdown"):
            await app.shutdown()
        elif getattr(app, "_api", None):
            app._api.close()  # fallback: close low-level API
    except Exception as e:
        logging.error(f"Error during shutdown: {e}")

    return listener.stored_count

if __name__ == "__main__":
    # Run one cycle of data collection
    stored = asyncio.run(run_cycle())
    if stored > 0:
        logging.info(f"Cycle complete: stored readings for {stored} device(s). Restarting script for next cycle...")
    else:
        logging.info("Cycle complete: no data collected. Restarting script to try again...")
    # Close the database connection before restarting
    conn.close()
    # Restart the script (self-restart after 15-minute cycle)
    os.execv(sys.executable, [sys.executable] + sys.argv)
