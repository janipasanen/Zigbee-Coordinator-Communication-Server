import asyncio
import argparse
from zigpy.application import ControllerApplication
from bellows.zigbee.application import ControllerApplication as BellowsApplication
import os
import random

# Configuration variables
# MacOS device path: /dev/cu.usbserial-1A1230
#DEVICE_PATH = '/dev/cu.usbserial-1A1230'

# Linux devie path /dev/ttyUSB0
DEVICE_PATH = '/dev/ttyUSB0'
CHANNEL = 15
#NETWORK_KEY = [0x01] * 16

async def pair_device():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    print("Starting ZBT-1 and initializing...")

    # Step 1: Create application instance
    app = BellowsApplication(config)

    # Step 2: Connect manually (open serial port)
    await app.connect()

    try:
        print("Trying to form a new Zigbee network...")
        await app.form_network()
        print("Network formed successfully!")
    except Exception as e:
        print(f"Could not form network (maybe already exists?): {e}")

    print("Starting the application...")
    await app.initialize(auto_form=False)  # Initialize AFTER forming

    print("Permitting joins for 60 seconds...")
    await app.permit(time_s=60)

    print("ZBT-1 is now in pairing mode. Activate pairing on your SNZB-02D sensor.")
    await asyncio.sleep(60)

    print("Pairing mode has ended.")
    await app.shutdown()


async def listen_for_data():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
        'network': {
            'channel': CHANNEL,
           # 'network_key': NETWORK_KEY,
        },
    }

    print("Connecting to ZBT-1 to listen for data...")
    app = await BellowsApplication.new(config)
    await app.startup(auto_form=False)

    print("Listening for incoming Zigbee messages...")
    try:
        while True:
            message = await app.raw_receive()
            print(f"Received message: {message}")
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        await app.shutdown()


def main():
    parser = argparse.ArgumentParser(description="ZBT-1 CLI tool for pairing and data listening.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("pair", help="Put ZBT-1 into pairing mode.")
    subparsers.add_parser("listen", help="Listen for incoming temperature and humidity data.")

    args = parser.parse_args()

    if args.command == "pair":
        asyncio.run(pair_device())
    elif args.command == "listen":
        asyncio.run(listen_for_data())

if __name__ == "__main__":
    main()
