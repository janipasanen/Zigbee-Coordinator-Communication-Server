import serial

def test_serial(port, baudrate):
    try:
        ser = serial.Serial(port, baudrate=baudrate, timeout=1)
        print(f"Opened {port} with baudrate {baudrate}.")
        ser.write(b'AT\r\n')
        response = ser.readline().decode().strip()
        print(f"Response: {response}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    port = '/dev/ttyUSB0'  # Update with your device path
    for baudrate in [9600, 19200, 38400, 57600, 115200]:
        print(f"Testing baudrate {baudrate}...")
        test_serial(port, baudrate)