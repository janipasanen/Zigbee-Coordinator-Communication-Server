import sqlite3
from tabulate import tabulate  # for prettier output, optional

DATABASE_FILE = 'sensor_data.db'

def print_all_readings():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()

    c.execute('SELECT id, device_ieee, device_name, temperature, humidity, timestamp FROM sensor_readings')
    rows = c.fetchall()

    if rows:
        headers = ['ID', 'Device IEEE', 'Device Name', 'Temperature (C)', 'Humidity (%)', 'Timestamp']
        print(tabulate(rows, headers=headers, tablefmt='grid'))
    else:
        print("No data found in the database.")

    conn.close()

if __name__ == "__main__":
    print_all_readings()
