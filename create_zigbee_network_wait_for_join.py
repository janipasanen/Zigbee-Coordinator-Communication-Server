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

def main():
    parser = argparse.ArgumentParser(description="ZBT-1 CLI tool for pairing.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    #listen_parser = subparsers.add_parser("listen", help="Listen for incoming Zigbee messages.")
    #listen_parser.add_argument("--sendToApi", action="store_true", help="Send collected data to API every 4 hours.")

    subparsers.add_parser("pair", help="Put ZBT-1 into pairing mode.")

    args = parser.parse_args()


    if args.command == "pair":
        asyncio.run(pair_device())

if __name__ == "__main__":
    main()