# temperature.py
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9) - GPIO simulation (no hardware)"

# =====================================================================================================================================
#      TEMPERATURE
#      Name                       : TEMPERATURE
#      Version                    : 1.0.0(9) - GPIO simulation (no hardware)
#      Date Created               : 27-05-2026
#      Author                     : Saifuddin
#      Notes                      : Hardware not yet available - GPIO simulation only
#                                   Random value between 21.3 - 21.5 Celsius
#                                   Ready to replace with real GPIO implementation
#                                   when hardware is available (e.g. DHT22 / DS18B20 / SHT31)
# ======================================================================================================================================

import random
import threading
import time

# =====================================================
# GPIO PIN (reserved - not yet in use)
# Replace with the correct pin when hardware is available
# Example for DHT22: TEMPERATURE_GPIO = 4
# =====================================================
TEMPERATURE_GPIO = 4   # reserved, not yet in use

# =====================================================
# SIMULATION STATE
# =====================================================
_simulated_temperature = round(random.uniform(21.3, 21.5), 1)
_sim_lock              = threading.Lock()


def _update_simulation():
    """
    Background thread: updates simulated value every 5 seconds
    to avoid a flat reading (realistic minor fluctuation).
    """
    global _simulated_temperature
    while True:
        time.sleep(5)
        with _sim_lock:
            _simulated_temperature = round(random.uniform(21.3, 21.5), 1)


_sim_thread = threading.Thread(target=_update_simulation, daemon=True)
_sim_thread.start()


# =====================================================
# PUBLIC API
# =====================================================

def get_temperature() -> float:
    """
    Read the current temperature value (Celsius).

    When hardware is available, replace the body of this function
    with the actual GPIO read. The interface stays the same.

    Returns:
        float: Temperature in Celsius, e.g. 21.3
    """
    with _sim_lock:
        return _simulated_temperature


def get_temperature_status() -> str:
    """
    Return temperature as a formatted string with unit.
    Used by status.py, utilitys.py, rfid2.json.

    Returns:
        str: e.g. "21.3"
    """
    return f"{get_temperature()}"


def get_temperature_float() -> float:
    """
    Return temperature as a float (without unit).
    Useful when the consumer needs a numeric value for calculation.

    Returns:
        float: e.g. 21.3
    """
    return get_temperature()
