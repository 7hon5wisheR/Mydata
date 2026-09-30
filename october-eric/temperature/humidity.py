# humidity.py
# -*- coding: utf-8 -*-
__version__ = "1.1.0(1) - reads from sht20.py shared driver (real hardware)"
# =====================================================================================================================================
#      HUMIDITY
#      Name                       : HUMIDITY
#      Version                    : 1.1.0(1)
#      Date Created               : 20-05-2026
#      Updated                    : 06-09-2026
#      Author                     : Saifuddin
#      Notes                      : No longer a GPIO simulation. Value now comes from
#                                   sht20.py's shared cache (real I2C sensor reading).
#                                   Public API (get_humidity / get_humidity_status /
#                                   get_humidity_float) is UNCHANGED, so status.py and
#                                   utilitys.py do not need any modification.
#                                 : If the sensor is not yet available/connected,
#                                   get_humidity_status() returns "N/A" instead of a
#                                   fabricated number, so it's obvious in status.json /
#                                   MQTT payloads that the reading is missing.
# ======================================================================================================================================
import sht20

# =====================================================
# PUBLIC API
# =====================================================
def get_humidity():
    """
    Read the current humidity value (% RH) from the shared SHT20 driver.
    Returns:
        float: Humidity in percent RH, e.g. 90.0
        None:  if the sensor has never been read successfully
               (not connected yet, or last read failed).
    """
    return sht20.get_humidity()


def get_humidity_status():
    """
    Return humidity as a formatted string.
    Used by status.py, utilitys.py, rfid2.json.
    Returns:
        str: e.g. "90.0", or "N/A" if the sensor is unavailable.
    """
    value = get_humidity()
    if value is None:
        return "N/A"
    return f"{value}"


def get_humidity_float():
    """
    Return humidity as a float (without unit).
    Useful when the consumer needs a numeric value for calculation.
    Returns:
        float: e.g. 90.0
        None:  if the sensor has never been read successfully.
    """
    return get_humidity()
