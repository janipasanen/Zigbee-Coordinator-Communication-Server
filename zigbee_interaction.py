import asyncio
import argparse
import sqlite3
import os
from datetime import datetime, timedelta
import aiohttp
from bellows.zigbee.application import ControllerApplication as BellowsApplication


# Configuration variables
# MacOS device path: /dev/cu.usbserial-1A1230
#DEVICE_PATH = '/dev/cu.usbserial-1A1230'

# Linux devie path /dev/ttyUSB0
DEVICE_PATH = '/dev/ttyUSB0'
DATABASE_FILE = 'sensor_data.db'
#TODO: - Update with your actual API endpoint
API_ENDPOINT = 'https://example.com'
CHANNEL = 15

# Zigbee Cluster IDs
TEMPERATURE_CLUSTER_ID = 0x0402
HUMIDITY_CLUSTER_ID = 0x0405

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
        print("✅ Database initialized.")

def store_reading(device_ieee, device_name, temperature=None, humidity=None):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO sensor_readings (device_ieee, device_name, temperature, humidity)
        VALUES (?, ?, ?, ?)
    ''', (device_ieee, device_name, temperature, humidity))
    conn.commit()
    conn.close()

async def send_recent_data_to_api():
    print("📤 Preparing to send last 4 hours of data to API...")

    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()

    four_hours_ago = datetime.utcnow() - timedelta(hours=4)
    c.execute('''
        SELECT device_ieee, device_name, temperature, humidity, timestamp
        FROM sensor_readings
        WHERE timestamp >= ?
    ''', (four_hours_ago,))
    rows = c.fetchall()
    conn.close()

    data = []
    for row in rows:
        record = {
            'device_ieee': row[0],
            'device_name': row[1],
            'temperature': row[2],
            'humidity': row[3],
            'timestamp': row[4]
        }
        data.append(record)

    if data:
        payload = {
            'readings': data,
            'batch_sent_at': datetime.utcnow().isoformat()
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(API_ENDPOINT, json=payload) as response:
                    if response.status == 200:
                        print("✅ Successfully sent data to API.")
                    else:
                        print(f"❌ Failed to send data. HTTP {response.status}")
            except Exception as e:
                print(f"❌ Exception during API POST: {e}")
    else:
        print("ℹ️ No data to send.")

async def configure_reporting(device):
    try:
        temperature_cluster = device.endpoints[1].in_clusters[TEMPERATURE_CLUSTER_ID]
        humidity_cluster = device.endpoints[1].in_clusters[HUMIDITY_CLUSTER_ID]

        await temperature_cluster.bind()
        await humidity_cluster.bind()

        await temperature_cluster.configure_reporting(
            attribute=0x0000,
            minimum_reporting_interval=30,
            maximum_reporting_interval=600,
            reportable_change=50
        )

        await humidity_cluster.configure_reporting(
            attribute=0x0000,
            minimum_reporting_interval=30,
            maximum_reporting_interval=600,
            reportable_change=100
        )

        print(f"✅ Configured reporting for device {device.ieee}")
    except Exception as e:
        print(f"❌ Failed to configure reporting for {device.ieee}: {e}")

async def pair_device():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    print("Starting ZBT-1 and initializing...")

    app = BellowsApplication(config)
    await app.connect()

    try:
        print("Trying to form a new Zigbee network...")
        await app.form_network()
        print("Network formed successfully!")
    except Exception as e:
        print(f"Could not form network (maybe already exists?): {e}")

    print("Starting the application...")
    await app.initialize(auto_form=False)

    print("Permitting joins for 60 seconds...")
    await app.permit(time_s=60)

    print("ZBT-1 is now in pairing mode. Activate pairing on your SNZB-02D sensor.")
    await asyncio.sleep(60)

    print("Pairing mode has ended.")
    await app.shutdown()

async def discover_sleepy_devices(app):
    network = app.devices

    print("Current devices known by coordinator:")
    for nwk, device in network.items():
        if isinstance(nwk, int):
            nwk_str = f"0x{nwk:04X}"
        else:
            nwk_str = str(nwk)

        ieee_str = str(device.ieee) if device.ieee else "<unknown>"
        device_name = device.model if hasattr(device, 'model') else "Unknown"
        print(f"NWK: {nwk_str}, IEEE: {ieee_str}, Model: {device_name}")

    for nwk, device in network.items():
        if device.ieee is None:
            try:
                print(f"Sending Simple Descriptor Request to 0x{nwk:04X}...")
                await app.zdo.request_simple_desc(nwk, 1)
            except Exception as e:
                print(f"Failed to request descriptor from 0x{nwk:04X}: {e}")

async def periodic_discover(app):
    while True:
        await discover_sleepy_devices(app)
        await asyncio.sleep(60)

async def listen_for_data(send_to_api=False):
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    print("Connecting to ZBT-1 to listen for data...")
    app = BellowsApplication(config)

    await app.connect()
    await app.initialize(auto_form=False)

    print("Listening for incoming Zigbee events...")

    def make_listener():
        class Listener:
            def attribute_updated(self, device, cluster, attribute, value):
                device_name = device.model if hasattr(device, 'model') else "Unknown"
                if cluster.cluster_id == TEMPERATURE_CLUSTER_ID:
                    temperature = value / 100
                    print(f"🌡️ Temperature: {temperature:.1f} °C (from {device.ieee})")
                    store_reading(str(device.ieee), device_name, temperature=temperature)
                elif cluster.cluster_id == HUMIDITY_CLUSTER_ID:
                    humidity = value / 100
                    print(f"💧 Humidity: {humidity:.1f}% (from {device.ieee})")
                    store_reading(str(device.ieee), device_name, humidity=humidity)
                else:
                    print(f"Device {device.ieee}: Cluster 0x{cluster.cluster_id:04X} Attribute {attribute} Value {value}")

            def device_initialized(self, device):
                print(f"✅ Device initialized: {device.ieee}")
                asyncio.create_task(configure_reporting(device))

            def device_joined(self, device):
                print(f"🎉 Device joined: {device.ieee}")

            def __getattr__(self, name):
                def catch_all(*args, **kwargs):
                    print(f"[Catch-All] Event: {name} Args: {args} Kwargs: {kwargs}")
                return catch_all

        return Listener()

    app.add_listener(make_listener())

    try:
        asyncio.create_task(periodic_discover(app))
        if send_to_api:
            asyncio.create_task(send_recent_data_to_api())
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        await app.shutdown()

def main():
    parser = argparse.ArgumentParser(description="ZBT-1 CLI tool for pairing and data listening.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listen_parser = subparsers.add_parser("listen", help="Listen for incoming Zigbee messages.")
    listen_parser.add_argument("--sendToApi", action="store_true", help="Send collected data to API every 4 hours.")

    subparsers.add_parser("pair", help="Put ZBT-1 into pairing mode.")

    args = parser.parse_args()

    initialize_database()

    if args.command == "pair":
        asyncio.run(pair_device())
    elif args.command == "listen":
        asyncio.run(listen_for_data(send_to_api=args.sendToApi))

if __name__ == "__main__":
    main()
    main()