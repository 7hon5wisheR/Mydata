# temperature.py
# -*- coding: utf-8 -*-
__version__ = "1.0.0(15) - reads from sht20.py shared driver (real hardware)"
# =====================================================================================================================================
#      TEMPERATURE
#      Name                       : TEMPERATURE
#      Version                    : 1.0.0(15)
#      Date Created               : 27-05-2026
#      Updated                    : 09-10-2026
#      Author                     : Saifuddin
#      Notes                      : No longer a GPIO simulation. Value now comes from
#                                   sht20.py's shared cache (real I2C sensor reading).
#                                   Public API (get_temperature / get_temperature_status /
#                                   get_temperature_float) is UNCHANGED, so status.py and
#                                   utilitys.py do not need any modification.
#                                 : If the sensor is not yet available/connected,
#                                   get_temperature_status() returns "-9999.99" instead of a
#                                   fabricated number, so it's obvious in status.json /
#                                   MQTT payloads that the reading is missing.
# ======================================================================================================================================
import sht20

# =====================================================
# PUBLIC API
# =====================================================
def get_temperature():
    """
    Read the current temperature value (Celsius) from the shared SHT20 driver.
    Returns:
        float: Temperature in Celsius, e.g. 21.3
        None:  if the sensor has never been read successfully
               (not connected yet, or last read failed).
    """
    return sht20.get_temperature()


def get_temperature_status():
    """
    Return temperature as a formatted string.
    Used by status.py, utilitys.py, rfid2.json.
    Returns:
        str: e.g. "21.3", or "-9999.99" if the sensor is unavailable.
    """
    value = get_temperature()
    if value is None:
        return "-9999.99"
    return f"{value}"


def get_temperature_float():
    """
    Return temperature as a float (without unit).
    Useful when the consumer needs a numeric value for calculation.
    Returns:
        float: e.g. 21.3
        None:  if the sensor has never been read successfully.
    """
    return get_temperature()
