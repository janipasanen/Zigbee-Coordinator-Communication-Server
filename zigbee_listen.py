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

CET = timezone(timedelta(hours=2))  # Adjust manually for daylight saving

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

# ---- Location Mapping ----

def resolve_device_location(ieee: str) -> str:
    if ieee.lower() == "0c:ef:f6:ff:fe:49:a4:1d":
        return "Sovrum"
    elif ieee.lower() == "0c:ef:f6:ff:fe:49:a5:82":
        return "Kontor"
    else:
        return "Okänd"

# ---- Main Listener Logic ----

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
            self.tasks = []

        def device_initialized(self, device):
            ieee_str = str(device.ieee)
            model = getattr(device, "model", "<unknown>")
            log(f"✅ Device initialized: {ieee_str} ({model})")

            async def delayed_read():
                await asyncio.sleep(2.0)
                await self.read_device_data(device)

            task = asyncio.create_task(delayed_read())
            self.tasks.append(task)

        async def read_device_data(self, device):
            ieee_str = str(device.ieee)
            device_name = getattr(device, "model", "SNZB-02D")
            found_cluster = False
            temperature = humidity = None

            for attempt in range(5):
                await asyncio.sleep(5)
                for ep_id, ep in device.endpoints.items():
                    if ep_id == 0:
                        continue

                    log(f"🔎 Checking endpoint {ep_id} on {ieee_str} with clusters: {list(ep.in_clusters.keys())}")
                    temp_cluster = ep.in_clusters.get(TEMPERATURE_CLUSTER_ID)
                    hum_cluster = ep.in_clusters.get(HUMIDITY_CLUSTER_ID)

                    if temp_cluster:
                        try:
                            res = await temp_cluster.read_attributes(["measured_value"])
                            log(f"🌡️ Temp raw read: {res}")
                            if isinstance(res, dict) and "measured_value" in res:
                                temperature = res["measured_value"] / 100
                                found_cluster = True
                        except Exception as e:
                            log(f"❌ Temp read error from {ieee_str}: {e}")

                    if hum_cluster:
                        try:
                            res = await hum_cluster.read_attributes(["measured_value"])
                            log(f"💧 Humidity raw read: {res}")
                            if isinstance(res, dict) and "measured_value" in res:
                                humidity = res["measured_value"] / 100
                                found_cluster = True
                        except Exception as e:
                            log(f"❌ Humidity read error from {ieee_str}: {e}")

                if temperature is not None or humidity is not None:
                    break

            if temperature is not None or humidity is not None:
                log(f"📊 Final read for DB insert - Temp: {temperature}, Humidity: {humidity}")
                timestamp = datetime.now(CET).strftime("%Y-%m-%d %H:%M:%S")
                try:
                    db_path = os.path.abspath(DATABASE_FILE)
                    log(f"📄 Attempting to open SQLite DB at: {db_path}")
                    conn = sqlite3.connect(db_path)
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
                except sqlite3.Error as e:
                    log(f"❌ SQLite error while storing data: {e.args[0]}")
            elif found_cluster:
                log(f"⚠️ Clusters found but no values received from {ieee_str}")
            else:
                log(f"⚠️ No readable clusters found for {ieee_str}")

    listener = MainListener()
    app.add_listener(listener)

    try:
        try:
            await app.permit(time_s=60)
            log("🟢 Permitting joins for 60 seconds...")
        except Exception:
            log("⚪ Could not explicitly permit joins (already open?).")

        log(f"⏳ Waiting for {WAIT_MINUTES} minutes...")
        await asyncio.sleep(WAIT_MINUTES * 60)

        log("🕒 Waiting for sensor tasks to finish...")
        await asyncio.gather(*listener.tasks)

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

# ---- API Sender ----

async def send_to_api_function(readings):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_ENDPOINT, json=readings) as response:
                if response.status in (200, 201):
                    log(f"📤 Sent {len(readings)} readings to API successfully.")
                else:
                    log(f"❌ API POST failed: HTTP {response.status}")
    except Exception as e:
        log(f"❌ Exception during API POST: {e}")

# ---- Main Entrypoint ----

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
