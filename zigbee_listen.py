import asyncio
import argparse
import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
import aiohttp
from bellows.zigbee.application import ControllerApplication as BellowsApplication

# Configuration
DEVICE_PATH = '/dev/ttyUSB0'
DATABASE_FILE = 'sensor_data.db'
API_READINGS = 'http://172.16.222.43:8199/readings'
API_POST_ONE = 'http://172.16.222.43:8199/readings'
API_POST_BATCH = 'http://172.16.222.43/readings/batch'

TEMPERATURE_CLUSTER_ID = 0x0402
HUMIDITY_CLUSTER_ID = 0x0405
CET = timezone(timedelta(hours=2))  # UTC+2 summer time

def log(msg):
    print(f"[{datetime.now(CET).isoformat()}] {msg}")

def resolve_device_location(ieee: str) -> str:
    if ieee.lower() == "0c:ef:f6:ff:fe:49:a4:1d":
        return "Sovrum"
    elif ieee.lower() == "0c:ef:f6:ff:fe:49:a5:82":
        return "Kontor"
    else:
        return "Okänd"

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
                timestamp TEXT
            )
        ''')
        conn.commit()
        conn.close()
        log("✅ Database initialized.")

async def send_one_reading_to_api(reading):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_POST_ONE, json=reading) as response:
                if response.status in (200, 201):
                    log(f"📤 Sent reading to API: {reading}")
                else:
                    log(f"❌ Failed to send reading: HTTP {response.status}")
    except Exception as e:
        log(f"❌ Exception during API POST: {e}")

async def send_batch_readings_to_api(readings):
    if not readings:
        log("ℹ️ No missing readings to send.")
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_POST_BATCH, json=readings) as response:
                if response.status in (200, 201):
                    log(f"📤 Sent {len(readings)} missing readings to API.")
                else:
                    log(f"❌ Batch POST failed: HTTP {response.status}")
    except Exception as e:
        log(f"❌ Batch POST exception: {e}")

async def fetch_api_readings():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_READINGS) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    log(f"❌ Failed to fetch API readings: HTTP {response.status}")
    except Exception as e:
        log(f"❌ Exception fetching API readings: {e}")
    return []

def load_local_readings():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM sensor_readings")
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows

def find_missing_rows(local_rows, api_rows):
    api_keys = {
        (r["deviceIEEE"], dateparser.parse(r["timestamp"]).isoformat())
        for r in api_rows
    }

    readings_to_send = []
    for row in local_rows:
        ieee = row["device_ieee"]
        ts = dateparser.parse(row["timestamp"]).astimezone(CET).isoformat()
        key = (ieee, ts)
        if key not in api_keys:
            readings_to_send.append({
                "deviceIEEE": ieee,
                "deviceName": row["device_name"],
                "deviceLocation": resolve_device_location(ieee),
                "temperature": row["temperature"],
                "humidity": row["humidity"],
                "timestamp": ts
            })
    return readings_to_send

async def read_and_sync_on_startup():
    log("🔁 Startup: syncing with remote API...")
    api_data = await fetch_api_readings()
    local_data = load_local_readings()
    missing = find_missing_rows(local_data, api_data)
    await send_batch_readings_to_api(missing)

async def pair_device():
    config = {'device': {'path': DEVICE_PATH}}
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
        log("⚪ Could not explicitly permit joins.")

    log("🛎️ Pair your SNZB-02D sensors now!")
    await asyncio.sleep(60)
    log("⏹️ Pairing mode ended.")
    await app.shutdown()

async def listen_for_data():
    config = {'device': {'path': DEVICE_PATH}}
    app = BellowsApplication(config)

    log(f"🔌 Connecting to ZBT-1 on {DEVICE_PATH}...")
    await app.connect()
    await app.initialize(auto_form=False)
    log("🛰️ Listening for device data...")

    class MainListener:
        def device_initialized(self, device):
            ieee_str = str(device.ieee)
            model = getattr(device, "model", "SNZB-02D")
            log(f"✅ Device initialized: {ieee_str} ({model})")

            async def delayed_read():
                await asyncio.sleep(2)
                await self.read_device_data(device)

            asyncio.create_task(delayed_read())

        async def read_device_data(self, device):
            ieee_str = str(device.ieee)
            device_name = getattr(device, "model", "SNZB-02D")
            temperature = humidity = None

            for attempt in range(5):
                await asyncio.sleep(5)
                for ep_id, ep in device.endpoints.items():
                    if ep_id == 0:
                        continue

                    temp_cluster = ep.in_clusters.get(TEMPERATURE_CLUSTER_ID)
                    hum_cluster = ep.in_clusters.get(HUMIDITY_CLUSTER_ID)

                    if temp_cluster:
                        try:
                            res = await temp_cluster.read_attributes(["measured_value"])
                            res_data = res[0] if isinstance(res, tuple) else res
                            if isinstance(res_data, dict) and "measured_value" in res_data:
                                temperature = res_data["measured_value"] / 100
                        except Exception as e:
                            log(f"❌ Temp read error: {e}")

                    if hum_cluster:
                        try:
                            res = await hum_cluster.read_attributes(["measured_value"])
                            res_data = res[0] if isinstance(res, tuple) else res
                            if isinstance(res_data, dict) and "measured_value" in res_data:
                                humidity = res_data["measured_value"] / 100
                        except Exception as e:
                            log(f"❌ Humidity read error: {e}")

                if temperature is not None or humidity is not None:
                    break

            if temperature is not None or humidity is not None:
                timestamp = datetime.now(CET).isoformat()
                conn = sqlite3.connect(DATABASE_FILE)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sensor_readings (device_ieee, device_name, temperature, humidity, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                ''', (ieee_str, device_name, temperature, humidity, timestamp))
                conn.commit()
                conn.close()

                log(f"📥 Stored and sending reading: {device_name} ({ieee_str}) Temp={temperature}°C Hum={humidity}% at {timestamp}")
                await send_one_reading_to_api({
                    "deviceIEEE": ieee_str,
                    "deviceName": device_name,
                    "deviceLocation": resolve_device_location(ieee_str),
                    "temperature": temperature,
                    "humidity": humidity,
                    "timestamp": timestamp
                })
            else:
                log(f"⚠️ No valid readings received from {ieee_str}")

    listener = MainListener()
    app.add_listener(listener)

    try:
        await app.permit(time_s=60)
        log("🟢 Permitting joins for 60 seconds...")
        await asyncio.sleep(60)
    except Exception:
        log("⚪ Could not explicitly permit joins.")

    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        log("🛑 KeyboardInterrupt, exiting...")
    finally:
        await app.shutdown()

def main():
    parser = argparse.ArgumentParser(description="ZBT-1 CLI tool with API sync.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("pair", help="Put ZBT-1 into pairing mode.")
    subparsers.add_parser("listen", help="Listen for sensor readings and sync with API.")
    args = parser.parse_args()

    initialize_database()

    if args.command == "pair":
        asyncio.run(pair_device())
    elif args.command == "listen":
        asyncio.run(read_and_sync_on_startup())
        asyncio.run(listen_for_data())

if __name__ == "__main__":
    main()