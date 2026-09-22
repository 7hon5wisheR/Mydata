FROM python:3.11-slim

# ===================== SYSTEM PREP =====================
RUN apt-get update && apt-get install -y supervisor \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ===================== PYTHON DEPENDENCIES =====================
RUN pip install --no-cache-dir flask paho-mqtt requests waitress

# ===================== WORKDIR =====================
WORKDIR /app
COPY sensor.py   /app/
COPY module.py   /app/
COPY templates/  /app/templates/

# ===================== SUPERVISORD CONFIG =====================
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ===================== DEFAULT COMMAND =====================
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
