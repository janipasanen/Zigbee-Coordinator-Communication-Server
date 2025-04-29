import asyncio
import argparse
import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta
import aiohttp
from bellows.zigbee.application import ControllerApplication as BellowsApplication

# Configuration variables
DEVICE_PATH = '/dev/ttyUSB0'
DATABASE_FILE = 'sensor_data.db'
API_ENDPOINT = 'http://172.16.222.43/readings/batch'

# Zigbee Cluster IDs
TEMPERATURE_CLUSTER_ID = 0x0402
HUMIDITY_CLUSTER_ID = 0x0405

WAIT_MINUTES = 15

# ---- Helper Functions ----

# Set CET timezone manually (UTC+1 or +2 with daylight saving)
CET = timezone(timedelta(hours=2))  # Change to +1 if winter time (no DST)

def log(msg):
    print(f"[{datetime.now(CET).isoformat()}] {msg}")

def initialize_database():
    if not os.path.exists(DATABASE_FILE):
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_ieee TEXT,
                device_name TEXT,
                temperature REAL,
                humidity REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        log("✅ Database initialized.")

# ---- Pairing Command ----

async def pair_device():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    log("🔌 Starting Zigbee coordinator...")

    app = BellowsApplication(config)
    await app.connect()

    try:
        log("🌐 Trying to form a new Zigbee network...")
        await app.form_network()
        log("✅ Network formed successfully!")
    except Exception as e:
        log(f"⚠️ Could not form network (maybe already exists?): {e}")

    await app.initialize(auto_form=False)

    try:
        log("🟢 Permitting joins for 60 seconds...")
        await app.permit(time_s=60)
    except Exception:
        log("⚪ Could not explicitly permit joins (probably already open).")

    log("🛎️ Pair your SNZB-02D sensors now!")
    await asyncio.sleep(60)

    log("⏹️ Pairing mode ended.")
    await app.shutdown()

# ---- Listening Command ----
def resolve_device_location(ieee: str) -> str:
    if ieee.lower() == "0c:ef:f6:ff:fe:49:a4:1d":
        return "Sovrum"
    elif ieee.lower() == "0c:ef:f6:ff:fe:49:a5:82":
        return "Kontor"
    else:
        return "Okänd"

async def listen_for_data(send_to_api=False):
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    log(f"🔌 Connecting to ZBT-1 on {DEVICE_PATH}...")
    app = BellowsApplication(config)

    await app.connect()
    await app.initialize(auto_form=False)

    log("🛰️ Listening for device data...")

    class MainListener:
        def __init__(self):
            self.readings = []

        def device_initialized(self, device):
            ieee_str = str(device.ieee)
            model = getattr(device, "model", "<unknown>")
            log(f"✅ Device initialized: {ieee_str} ({model})")

            async def delayed_read():
                await asyncio.sleep(2.0)  # ← Changed to 2 seconds here!
                await self.read_device_data(device)

            asyncio.create_task(delayed_read())

        async def read_device_data(self, device):
            ieee_str = str(device.ieee)
            temp_cluster = hum_cluster = None
            for ep_id, ep in device.endpoints.items():
                if ep_id == 0:
                    continue
                if TEMPERATURE_CLUSTER_ID in ep.in_clusters:
                    temp_cluster = ep.in_clusters[TEMPERATURE_CLUSTER_ID]
                if HUMIDITY_CLUSTER_ID in ep.in_clusters:
                    hum_cluster = ep.in_clusters[HUMIDITY_CLUSTER_ID]
                if temp_cluster and hum_cluster:
                    break

            if not temp_cluster and not hum_cluster:
                log(f"⚠️ No temp/humidity cluster found on {ieee_str}")
                return

            async def try_read():
                temperature = humidity = None
                try:
                    if temp_cluster:
                        res = await temp_cluster.read_attributes(["measured_value"])
                        if isinstance(res, dict) and "measured_value" in res:
                            temperature = res["measured_value"] / 100
                    if hum_cluster:
                        res = await hum_cluster.read_attributes(["measured_value"])
                        if isinstance(res, dict) and "measured_value" in res:
                            humidity = res["measured_value"] / 100
                except Exception as e:
                    log(f"❌ Error reading attributes from {ieee_str}: {e}")
                return temperature, humidity

            # First try
            temperature, humidity = await try_read()

            if temperature is None and humidity is None:
                log(f"⚠️ First read failed from {ieee_str}, retrying after 2 seconds...")
                await asyncio.sleep(2)
                temperature, humidity = await try_read()

            if temperature is not None or humidity is not None:
                device_name = getattr(device, "model", "SNZB-02D")
                timestamp = datetime.now(CET).strftime("%Y-%m-%d %H:%M:%S")  # Local time
                try:
                    conn = sqlite3.connect(DATABASE_FILE)
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO sensor_readings (device_ieee, device_name, temperature, humidity, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (ieee_str, device_name, temperature, humidity, timestamp))
                    conn.commit()
                    conn.close()
                    log(f"📥 Stored reading: {device_name} ({ieee_str}) Temp={temperature}°C Hum={humidity}% at {timestamp}")
                    self.readings.append({
                        "deviceIEEE": ieee_str,
                        "deviceName": device_name,
                        "deviceLocation": resolve_device_location(ieee_str),
                        "temperature": temperature,
                        "humidity": humidity,
                        "timestamp": datetime.now(CET).isoformat()
                    })
                except Exception as e:
                    log(f"❌ Failed to store in database for {ieee_str}: {e}")
            else:
                log(f"⚠️ No sensor data read from {ieee_str} after retry.")

    listener = MainListener()
    app.add_listener(listener)

    try:
        try:
            await app.permit(time_s=60)
            log("🟢 Permitting joins for 60 seconds...")
        except Exception:
            log("⚪ Could not explicitly permit joins (already open?).")

        total_wait = WAIT_MINUTES * 60
        log(f"⏳ Waiting for {WAIT_MINUTES} minutes...")
        await asyncio.sleep(total_wait)

        if listener.readings:
            if send_to_api:
                await send_to_api_function(listener.readings)
            log(f"✅ {len(listener.readings)} device readings handled.")
        else:
            log("⚪ No device readings collected in this cycle.")

    except KeyboardInterrupt:
        log("🛑 KeyboardInterrupt received, exiting...")

    finally:
        try:
            await app.shutdown()
        except Exception as e:
            log(f"❌ Error shutting down Zigbee application: {e}")

        log("🔁 Restarting program...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

async def send_to_api_function(readings):
    payload = {
        'readings': readings,
        'batch_sent_at': datetime.now(CET).isoformat()  # Local time here too
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_ENDPOINT, json=payload) as response:
                if response.status == 200:
                    log(f"📤 Sent {len(readings)} readings to API successfully.")
                else:
                    log(f"❌ API POST failed: HTTP {response.status}")
    except Exception as e:
        log(f"❌ Exception during API POST: {e}")

# ---- Main Entry ----

def main():
    parser = argparse.ArgumentParser(description="ZBT-1 CLI tool for pairing and data collection.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listen_parser = subparsers.add_parser("listen", help="Listen for devices and collect data.")
    listen_parser.add_argument("--sendToApi", action="store_true", help="Send collected data to external API.")

    subparsers.add_parser("pair", help="Put ZBT-1 into pairing mode (60 seconds).")

    args = parser.parse_args()

    initialize_database()

    if args.command == "pair":
        asyncio.run(pair_device())
    elif args.command == "listen":
        asyncio.run(listen_for_data(send_to_api=args.sendToApi))

if __name__ == "__main__":
    main()
