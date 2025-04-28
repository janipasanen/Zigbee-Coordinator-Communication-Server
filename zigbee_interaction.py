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

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}")

async def pair_device():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    log("Starting ZBT-1 and initializing...")

    app = BellowsApplication(config)
    await app.connect()

    try:
        log("Trying to form a new Zigbee network...")
        await app.form_network()
        log("Network formed successfully!")
    except Exception as e:
        log(f"Could not form network (maybe already exists?): {e}")

    log("Starting the application...")
    await app.initialize(auto_form=False)

    log("Permitting joins for 60 seconds...")
    await app.permit(time_s=60)

    log("ZBT-1 is now in pairing mode. Activate pairing on your SNZB-02D sensor.")
    await asyncio.sleep(60)

    log("Pairing mode has ended.")
    await app.shutdown()

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
    log("📤 Preparing to send last 4 hours of data to API...")

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
                        log("✅ Successfully sent data to API.")
                    else:
                        log(f"❌ Failed to send data. HTTP {response.status}")
            except Exception as e:
                log(f"❌ Exception during API POST: {e}")
    else:
        log("ℹ️ No data to send.")


async def configure_reporting(device):
    try:
        endpoint = device.endpoints.get(1)
        if endpoint is None:
            log(f"⚠️ No endpoint 1 on device {device.ieee}")
            return

        if TEMPERATURE_CLUSTER_ID in endpoint.in_clusters:
            temp_cluster = endpoint.in_clusters[TEMPERATURE_CLUSTER_ID]
            await temp_cluster.bind()
            await temp_cluster.configure_reporting(0x0000, 10, 300, 5)
            log(f"✅ Configured temperature reporting for {device.ieee}")
        else:
            log(f"⚠️ Temperature cluster not found on {device.ieee}")

        if HUMIDITY_CLUSTER_ID in endpoint.in_clusters:
            hum_cluster = endpoint.in_clusters[HUMIDITY_CLUSTER_ID]
            await hum_cluster.bind()
            await hum_cluster.configure_reporting(0x0000, 10, 300, 100)
            log(f"✅ Configured humidity reporting for {device.ieee}")
        else:
            log(f"⚠️ Humidity cluster not found on {device.ieee}")

    except Exception as e:
        log(f"❌ Failed configure_reporting for {device.ieee}: {e}")

async def discover_sleepy_devices(app):
    network = app.devices

    #log("Current devices known by coordinator:")
    for nwk, device in network.items():
        if isinstance(nwk, int):
            nwk_str = f"0x{nwk:04X}"
        else:
            nwk_str = str(nwk)

        ieee_str = str(device.ieee) if device.ieee else "<unknown>"
        device_name = device.model if hasattr(device, 'model') else "Unknown"
        #log(f"NWK: {nwk_str}, IEEE: {ieee_str}, Model: {device_name}")

    for nwk, device in network.items():
        if device.ieee is None:
            try:
                log(f"Sending Simple Descriptor Request to 0x{nwk:04X}...")
                await app.zdo.request_simple_desc(nwk, 1)
            except Exception as e:
                log(f"Failed to request descriptor from 0x{nwk:04X}: {e}")

async def periodic_discover(app):
    while True:
        await discover_sleepy_devices(app)
        await asyncio.sleep(60)

async def periodic_poll_devices(app):
    while True:
        network = app.devices
        for nwk, device in network.items():
            if device.ieee is None:
                continue

            device_name = device.model if hasattr(device, 'model') else "Unknown"

            try:
                endpoint = device.endpoints.get(1)
                if endpoint is None:
                    log(f"⚠️ No endpoint 1 on device {device.ieee}")
                    continue

                if TEMPERATURE_CLUSTER_ID in endpoint.in_clusters:
                    temp_cluster = endpoint.in_clusters[TEMPERATURE_CLUSTER_ID]
                    res = await temp_cluster.read_attributes(['measured_value'])
                    if 'measured_value' in res:
                        temperature = res['measured_value'] / 100
                        log(f"🌡️ Polled temperature: {temperature:.1f} °C (from {device.ieee})")
                        store_reading(str(device.ieee), device_name, temperature=temperature)

                if HUMIDITY_CLUSTER_ID in endpoint.in_clusters:
                    hum_cluster = endpoint.in_clusters[HUMIDITY_CLUSTER_ID]
                    res = await hum_cluster.read_attributes(['measured_value'])
                    if 'measured_value' in res:
                        humidity = res['measured_value'] / 100
                        log(f"💧 Polled humidity: {humidity:.1f}% (from {device.ieee})")
                        store_reading(str(device.ieee), device_name, humidity=humidity)

            except Exception as e:
                log(f"❌ Exception while polling {device.ieee}: {e}")

        await asyncio.sleep(300)

async def listen_for_data(send_to_api=False):
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    log("Connecting to ZBT-1 to listen for data...")
    app = BellowsApplication(config)

    await app.connect()
    await app.initialize(auto_form=False)

    log("Listening for incoming Zigbee events...")

    # Track devices we've successfully configured
    successfully_configured = set()
    last_catchall_poll = {}
    MIN_CATCHALL_POLL_INTERVAL = 300  # 5 minutes in seconds

    def make_listener():
        class Listener:
            def attribute_updated(self, device, cluster, attribute, value):
                device_name = device.model if hasattr(device, 'model') else "Unknown"
                if cluster.cluster_id == TEMPERATURE_CLUSTER_ID:
                    temperature = value / 100
                    log(f"🌡️ Temperature: {temperature:.1f} °C (from {device.ieee})")
                    store_reading(str(device.ieee), device_name, temperature=temperature)
                elif cluster.cluster_id == HUMIDITY_CLUSTER_ID:
                    humidity = value / 100
                    log(f"💧 Humidity: {humidity:.1f}% (from {device.ieee})")
                    store_reading(str(device.ieee), device_name, humidity=humidity)

                if device.ieee not in successfully_configured:
                    log(f"🔄 Trying to configure reporting for {device.ieee} after receiving attribute update...")
                    asyncio.create_task(configure_and_mark(device))

            def device_initialized(self, device):
                log(f"✅ Device initialized: {device.ieee}")
                asyncio.create_task(self._handle_device_initialized(device))

            async def _handle_device_initialized(self, device):
                try:
                    endpoint = device.endpoints.get(1)
                    if endpoint is None:
                        log(f"⚠️ No endpoint 1 on device {device.ieee}")
                        return

                    if TEMPERATURE_CLUSTER_ID in endpoint.in_clusters:
                        temp_cluster = endpoint.in_clusters[TEMPERATURE_CLUSTER_ID]
                        res = await temp_cluster.read_attributes(['measured_value'])
                        log(f"🌡️ Initial temperature read for {device.ieee}: {res}")

                    if HUMIDITY_CLUSTER_ID in endpoint.in_clusters:
                        hum_cluster = endpoint.in_clusters[HUMIDITY_CLUSTER_ID]
                        res = await hum_cluster.read_attributes(['measured_value'])
                        log(f"💧 Initial humidity read for {device.ieee}: {res}")

                    await configure_reporting(device)
                except Exception as e:
                    log(f"❌ Exception in handling device {device.ieee}: {e}")

            def device_joined(self, device):
                log(f"🎉 Device joined: {device.ieee}")

            def __getattr__(self, name):
                def catch_all(*args, **kwargs):
                    device = args[0] if args else None
                    if device and hasattr(device, 'ieee'):
                        now = datetime.utcnow().timestamp()
                        log(f"[Catch-All] Event: {name} Args: {args} Kwargs: {kwargs}")

                        # If first time or last request was longer ago than MIN_CATCHALL_POLL_INTERVAL
                        if (device.ieee not in last_catchall_poll) or (
                                now - last_catchall_poll[device.ieee] > MIN_CATCHALL_POLL_INTERVAL):
                            log(f"🔵 Catch-all event received from {device.ieee} (event: {name}), triggering value read.")
                            last_catchall_poll[device.ieee] = now
                            asyncio.create_task(read_and_store_device_values(device))
                        else:
                            log(f"🔵 Catch-all event from {device.ieee} ignored (rate limited).")

                        # Still configure if not already done
                        if device.ieee not in successfully_configured:
                            log(f"🔄 Trying to configure reporting for {device.ieee} after receiving catch-all event...")
                            asyncio.create_task(configure_and_mark(device))

                return catch_all

        return Listener()

    async def read_and_store_device_values(device):
        try:
            device_name = device.model if hasattr(device, 'model') else "Unknown"
            endpoint = device.endpoints.get(1)
            if endpoint is None:
                log(f"⚠️ No endpoint 1 on device {device.ieee}")
                return

            if TEMPERATURE_CLUSTER_ID in endpoint.in_clusters:
                temp_cluster = endpoint.in_clusters[TEMPERATURE_CLUSTER_ID]
                res = await temp_cluster.read_attributes(['measured_value'])
                if 'measured_value' in res:
                    temperature = res['measured_value'] / 100
                    log(f"🌡️ Polled (catch-all) temperature: {temperature:.1f} °C (from {device.ieee})")
                    store_reading(str(device.ieee), device_name, temperature=temperature)

            if HUMIDITY_CLUSTER_ID in endpoint.in_clusters:
                hum_cluster = endpoint.in_clusters[HUMIDITY_CLUSTER_ID]
                res = await hum_cluster.read_attributes(['measured_value'])
                if 'measured_value' in res:
                    humidity = res['measured_value'] / 100
                    log(f"💧 Polled (catch-all) humidity: {humidity:.1f}% (from {device.ieee})")
                    store_reading(str(device.ieee), device_name, humidity=humidity)

        except Exception as e:
            log(f"❌ Exception while reading device {device.ieee}: {e}")

    async def configure_and_mark(device):
        try:
            await configure_reporting(device)
            successfully_configured.add(device.ieee)
        except Exception as e:
            log(f"❌ Exception during configure_reporting for {device.ieee}: {e}")

    app.add_listener(make_listener())

    try:
        asyncio.create_task(periodic_discover(app))
        asyncio.create_task(periodic_poll_devices(app))
        if send_to_api:
            asyncio.create_task(send_recent_data_to_api())
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        log("Exiting...")
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