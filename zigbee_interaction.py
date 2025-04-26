import serial
import time


def initialize_zigbee_coordinator(serial_port):
    """
    Initialize the Zigbee coordinator for communication.
    """
    print("Initializing Zigbee Coordinator...")
    ser = serial.Serial(serial_port, baudrate=115200, timeout=1)
    ser.write(b'AT+START\r\n')  # Initialize Zigbee coordinator
    response = ser.readline().decode().strip()
    print("Coordinator Response:", response)
    return ser


def enable_pairing(serial_connection):
    """
    Put the Zigbee coordinator in pairing mode.
    """
    print("Enabling pairing mode...")
    serial_connection.write(b'AT+PERMITJOIN:60\r\n')  # Allow pairing for 60 seconds
    response = serial_connection.readline().decode().strip()
    print("Pairing Mode Response:", response)


def wait_for_device(serial_connection):
    """
    Wait for a device to pair and return its device ID.
    """
    print("Waiting for a device to pair...")
    while True:
        device_info = serial_connection.readline().decode().strip()
        if device_info:
            print("Paired Device Info:", device_info)
            # Assume the device info contains the Device ID (e.g., 0x1234)
            device_id = parse_device_id(device_info)
            return device_id


def parse_device_id(device_info):
    """
    Extract the Device ID from the pairing message.
    """
    # Assuming the device info contains the device ID in a standard format
    # Modify this function as per the actual response format of your Zigbee coordinator
    return device_info.split(",")[0]  # Example parsing logic


def read_temperature(serial_connection, device_id):
    """
    Read the temperature attribute from the paired sensor.
    """
    print("Reading temperature...")
    command = f'AT+READATTR:{device_id},0x0402,0x0000\r\n'  # Read temperature attribute
    serial_connection.write(command.encode())
    while True:
        response = serial_connection.readline().decode().strip()
        if response:
            print("Temperature Response:", response)
            return parse_attribute_value(response)


def read_humidity(serial_connection, device_id):
    """
    Read the humidity attribute from the paired sensor.
    """
    print("Reading humidity...")
    command = f'AT+READATTR:{device_id},0x0405,0x0000\r\n'  # Read humidity attribute
    serial_connection.write(command.encode())
    while True:
        response = serial_connection.readline().decode().strip()
        if response:
            print("Humidity Response:", response)
            return parse_attribute_value(response)


def parse_attribute_value(response):
    """
    Parse the attribute value from the Zigbee response.
    """
    # Assuming the response contains the attribute value in a standard format
    # Modify this function as per the actual response format of your Zigbee coordinator
    return float(response.split(":")[1]) / 100.0  # Example parsing logic


def main():
    # Replace with your serial port
    serial_port = '/dev/tty.usbserial-1A1230'

    try:
        # Step 1: Initialize Zigbee Coordinator
        ser = initialize_zigbee_coordinator(serial_port)

        # Step 2: Enable Pairing Mode
        enable_pairing(ser)

        # Step 3: Wait for Device to Pair
        device_id = wait_for_device(ser)
        print(f"Paired Device ID: {device_id}")

        # Step 4: Read Sensor Data
        while True:
            temperature = read_temperature(ser, device_id)
            print(f"Temperature: {temperature} °C")

            humidity = read_humidity(ser, device_id)
            print(f"Humidity: {humidity} %")

            time.sleep(5)  # Poll every 5 seconds

    except Exception as e:
        print("Error:", e)
    finally:
        print("Closing serial connection.")
        if 'ser' in locals() and ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()