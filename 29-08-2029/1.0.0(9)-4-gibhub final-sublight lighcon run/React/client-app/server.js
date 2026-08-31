// =====================================
// server.js
// =====================================
require("dotenv").config();
const express = require("express");
const cors = require("cors");
const bodyParser = require("body-parser");
const http = require("http");
const { Server } = require("socket.io");
const os = require("os");
const fs = require("fs");
const path = require("path");

const app = express();
app.use(cors());
app.use(bodyParser.json());

const server = http.createServer(app);
const io = new Server(server, { cors: { origin: "*" } });
const PORT = process.env.PORT || 3001;

// ======================================================
// AUTO NETWORK DETECTION - DOCKER AWARE
// ======================================================
const getNetworkPrefix = () => {
  console.log("Starting IP detection...");

  if (process.env.HOST_IP) {
    const ip = process.env.HOST_IP.trim();
    const parts = ip.split(".");
    if (parts.length !== 4) {
      throw new Error(`HOST_IP invalid format: "${ip}" — expected format: x.x.x.x`);
    }
    const prefix = `${parts[0]}.${parts[1]}.${parts[2]}.`;
    console.log(`[MODE: Docker] Using HOST_IP: ${ip}`);
    console.log(`Network Prefix: ${prefix}`);
    return prefix;
  }

  console.log("[MODE: Local] Auto-detecting from network interfaces...");
  console.warn("  If running in Docker, set: -e HOST_IP=<Your_Windows_IP>");

  const interfaces = os.networkInterfaces();
  const skipPrefixes = ["172.", "169.254.", "127."];
  const skipNames    = ["docker", "br-", "veth", "vmware", "vmnet", "virtual", "vbox", "hyper-v", "wsl"];
  const preferredNames = ["ethernet", "eth", "en", "local area"];

  const shouldSkipInterface = (name) => skipNames.some((s) => name.toLowerCase().includes(s));
  const shouldSkipIP        = (address) => skipPrefixes.some((p) => address.startsWith(p));

  for (const name of Object.keys(interfaces)) {
    if (shouldSkipInterface(name)) continue;
    if (!preferredNames.some((p) => name.toLowerCase().includes(p))) continue;
    for (const iface of interfaces[name]) {
      if (iface.family === "IPv4" && !iface.internal && !shouldSkipIP(iface.address)) {
        const parts = iface.address.split(".");
        console.log(`Detected Ethernet IP from [${name}]: ${iface.address}`);
        return `${parts[0]}.${parts[1]}.${parts[2]}.`;
      }
    }
  }

  for (const name of Object.keys(interfaces)) {
    if (shouldSkipInterface(name)) continue;
    for (const iface of interfaces[name]) {
      if (iface.family === "IPv4" && !iface.internal && !shouldSkipIP(iface.address)) {
        const parts = iface.address.split(".");
        console.log(`Detected IP from interface [${name}]: ${iface.address}`);
        return `${parts[0]}.${parts[1]}.${parts[2]}.`;
      }
    }
  }

  throw new Error("Cannot detect network prefix. Please set the HOST_IP environment variable.");
};

const NETWORK_PREFIX = getNetworkPrefix();

// ======================================================
// STATIC CABINET GENERATION (initial seed)
// ======================================================
const NUMBER_OF_CABINETS = parseInt(process.env.NUMBER_OF_CABINETS) || 2;
const IP_START_FROM      = 101;

const INITIAL_IPS = Array.from(
  { length: NUMBER_OF_CABINETS },
  (_, i) => `${NETWORK_PREFIX}${IP_START_FROM + i}`
);

const INITIAL_NAMES = Array.from({ length: NUMBER_OF_CABINETS }, (_, i) => {
  const letter = String.fromCharCode(65 + i);
  return `MASCAB01${letter}`;
});

// ======================================================
// DYNAMIC REGISTRY — live data, can change at runtime
// ======================================================
// knownIPs     : Set of all detected IPs
// ipToName     : Map IP ? hostname  (e.g. "192.168.137.103" ? "MASCAB01C")
// nameToIp     : Map hostname ? IP
// ======================================================
const knownIPs      = new Set(INITIAL_IPS);
const ipToName      = Object.fromEntries(INITIAL_IPS.map((ip, i)   => [ip,   INITIAL_NAMES[i]]));
const nameToIp      = Object.fromEntries(INITIAL_NAMES.map((name, i) => [name, INITIAL_IPS[i]]));
const rabbitStatus  = {};
const isPythonActive = {};

// Heartbeat: track last time each IP sent a status
const lastSeenTime = {};
const TIMEOUT_MS   = 10 * 60 * 1000; // 10 minutes

for (const ip of INITIAL_IPS) {
  rabbitStatus[ip]   = { DoorStatus: "ready", NumberOfRFID: null };
  isPythonActive[ip] = false;
  lastSeenTime[ip]   = Date.now(); // treat as active on startup
}

// ======================================================
// PERSISTENT REGISTRY (disk-backed)
// ------------------------------------------------------
// Problem this solves: previously the registry (knownIPs /
// ipToName / nameToIp / lastSeenTime) only lived in memory.
// Restarting server.js (npm run dev, crash, deploy) wiped it
// back to the INITIAL_IPS/INITIAL_NAMES seed, losing every
// cabinet that had been auto-registered/renamed since boot.
//
// Now we persist the registry to a JSON file on every change
// and restore it on startup (after the initial seed above),
// so a server restart no longer loses cabinet state.
// ======================================================
const REGISTRY_FILE = path.join(__dirname, "registry-data.json");

function saveRegistryToDisk() {
  try {
    const data = {
      knownIPs: [...knownIPs],
      ipToName,
      nameToIp,
      lastSeenTime,
    };
    fs.writeFileSync(REGISTRY_FILE, JSON.stringify(data, null, 2));
  } catch (e) {
    console.error("Failed to save registry:", e.message);
  }
}

function loadRegistryFromDisk() {
  try {
    if (!fs.existsSync(REGISTRY_FILE)) return false;
    const data = JSON.parse(fs.readFileSync(REGISTRY_FILE, "utf-8"));

    knownIPs.clear();
    (data.knownIPs || []).forEach((ip) => knownIPs.add(ip));

    // Clear then repopulate maps (avoid leaving stale seed entries around)
    Object.keys(ipToName).forEach((k) => delete ipToName[k]);
    Object.keys(nameToIp).forEach((k) => delete nameToIp[k]);
    Object.assign(ipToName, data.ipToName || {});
    Object.assign(nameToIp, data.nameToIp || {});
    Object.assign(lastSeenTime, data.lastSeenTime || {});

    // Make sure every restored IP has status/python-active entries
    for (const ip of knownIPs) {
      if (!rabbitStatus[ip]) rabbitStatus[ip] = { DoorStatus: "ready", NumberOfRFID: null };
      if (isPythonActive[ip] === undefined) isPythonActive[ip] = false;
    }

    console.log(`[Registry] Restored ${knownIPs.size} cabinet(s) from disk (${REGISTRY_FILE})`);
    return true;
  } catch (e) {
    console.error("Failed to load registry:", e.message);
    return false;
  }
}

// Restore any previously known cabinets, overriding the fresh seed above.
loadRegistryFromDisk();

// ======================================================
// BROKER CONFIG (runtime-editable â€” no rebuild/restart needed)
// ------------------------------------------------------
// Problem this solves: MQTT_BROKER IP used to be hardcoded inside
// CabinetStatusComponent.js (`ws://20.81.43.213:15675/ws`). Changing
// it meant editing React source and rebuilding (npm run dev restart,
// or docker build + docker rm + docker run for run.bat).
//
// Only the IP is configurable â€” port/user/pass stay as they always
// were (15675 / guest / guest). The IP lives in a tiny JSON file next
// to server.js (broker-config.json), same folder as registry-data.json.
// React fetches this at startup via GET /api/broker-config instead of
// hardcoding it. To change the broker IP:
//   1) POST to /api/broker-config (e.g. via curl/Postman), OR
//   2) edit broker-config.json directly and save â€” fs.watchFile below
//      detects the change automatically.
// Either way the server broadcasts "broker-config-update" over
// Socket.IO so every open React tab reconnects its MQTT client to the
// new IP immediately, with zero restart/rebuild.
//
// DOCKER NOTE: broker-config.json lives INSIDE the container's
// filesystem by default. If you only edit it on the host without
// mounting it as a volume in run.bat, the container never sees your
// change. See the updated run.bat for the required `-v` mount.
// ======================================================
const BROKER_CONFIG_FILE = path.join(__dirname, "broker-config.json");
const BROKER_PORT = "15675";
const BROKER_USER = "guest";
const BROKER_PASS = "guest";

function loadBrokerConfig() {
  try {
    if (fs.existsSync(BROKER_CONFIG_FILE)) {
      const data = JSON.parse(fs.readFileSync(BROKER_CONFIG_FILE, "utf-8"));
      if (data.ip) return { ip: data.ip };
    }
  } catch (e) {
    console.error("Failed to load broker config:", e.message);
  }
  // First run fallback: env var, or the old hardcoded default.
  return { ip: process.env.MQTT_BROKER_IP || "20.81.43.213" };
}

function saveBrokerConfig(cfg) {
  try {
    fs.writeFileSync(BROKER_CONFIG_FILE, JSON.stringify({ ip: cfg.ip }, null, 2));
  } catch (e) {
    console.error("Failed to save broker config:", e.message);
  }
}

let brokerConfig = loadBrokerConfig();
if (!fs.existsSync(BROKER_CONFIG_FILE)) saveBrokerConfig(brokerConfig);

const brokerWsUrl = (cfg) => `ws://${cfg.ip}:${BROKER_PORT}/ws`;
const brokerPublicPayload = (cfg) => ({ ws: brokerWsUrl(cfg), user: BROKER_USER, pass: BROKER_PASS });

// Guard against fs.watchFile firing on our own writes (saveBrokerConfig)
let lastBrokerConfigWriteAt = 0;

// Watch the file so a manual edit + save (no curl needed) also triggers
// a live reload + broadcast to every open React tab.
// NOTE: this only fires for changes made INSIDE the container's own
// filesystem view of broker-config.json. If you edit the file on the
// host without a volume mount, the container's copy never changes and
// this watcher never fires â€” see the Docker note above.
fs.watchFile(BROKER_CONFIG_FILE, { interval: 1000 }, () => {
  // Skip the reload we ourselves just triggered via POST /api/broker-config
  if (Date.now() - lastBrokerConfigWriteAt < 1500) return;
  try {
    const newCfg = JSON.parse(fs.readFileSync(BROKER_CONFIG_FILE, "utf-8"));
    if (!newCfg.ip) return;
    brokerConfig = { ip: newCfg.ip };
    const payload = brokerPublicPayload(brokerConfig);
    io.emit("broker-config-update", payload);
    console.log(`[Broker Config] File changed on disk -> reloaded & broadcast: ${payload.ws}`);
  } catch (e) {
    console.error("Failed to reload broker config after file change:", e.message);
  }
});

// ======================================================
// AUTO-REGISTER cabinet from incoming MQTT payload
//
// Key fix: we start with NO initial seed in registry
// and let MQTT payloads build the registry dynamically.
// This prevents duplicate cabinets when:
//   - IP .101 is seeded as "MASCAB01A" (placeholder)
//   - MQTT arrives with IP .101, hostname "MASCAB01A"
//   ? these must match, not create a duplicate
//
// Scenarios handled:
// 1. Brand new IP + hostname  ? register as new cabinet
// 2. Known IP, hostname changed (RPI renamed) ? rename
// 3. Known IP, same hostname  ? just update lastSeenTime (no event)
// 4. Known hostname, new IP   ? update IP mapping
// ======================================================
function autoRegisterCabinet(ip, hostname) {
  if (!ip || !hostname) return null; // not enough info

  const existingNameForIp       = ipToName[ip];       // what name do we know this IP as?
  const existingIpForHostname   = nameToIp[hostname];  // what IP do we know this hostname as?

  // -- Case 3: exact match — nothing to do ----------------------
  if (existingNameForIp === hostname) {
    return null; // no change
  }

  // -- Case 4: hostname known, but came from a different IP -----
  // e.g. MASCAB01A was at .101, now appears at .103
  if (!existingNameForIp && existingIpForHostname && existingIpForHostname !== ip) {
    console.log(`[IP-change] ${hostname}: ${existingIpForHostname} ? ${ip}`);
    const oldIp = existingIpForHostname;

    // Remove old IP mapping
    knownIPs.delete(oldIp);
    delete ipToName[oldIp];
    delete lastSeenTime[oldIp];

    // Register under new IP
    knownIPs.add(ip);
    ipToName[ip]       = hostname;
    nameToIp[hostname] = ip;

    if (!rabbitStatus[ip]) rabbitStatus[ip] = { DoorStatus: "ready", NumberOfRFID: null };
    if (isPythonActive[ip] === undefined) isPythonActive[ip] = false;

    // Return event with oldName = newName (IP changed, name stayed same)
    // React will update IP mapping but not rename the cabinet card
    return { ip, newName: hostname, oldName: hostname, ipChanged: true, oldIp };
  }

  // -- Case 2: known IP, hostname changed -----------------------
  // e.g. .101 was MASCAB01A, now reports as MASCAB01C
  if (existingNameForIp && existingNameForIp !== hostname) {
    console.log(`[Rename] ${existingNameForIp} ? ${hostname} (IP: ${ip})`);

    // Clean up old hostname mapping
    delete nameToIp[existingNameForIp];

    // If hostname was also pointing to a different IP, remove that too
    if (existingIpForHostname && existingIpForHostname !== ip) {
      knownIPs.delete(existingIpForHostname);
      delete ipToName[existingIpForHostname];
      delete lastSeenTime[existingIpForHostname];
    }

    // Register new mapping
    knownIPs.add(ip);
    ipToName[ip]       = hostname;
    nameToIp[hostname] = ip;

    if (!rabbitStatus[ip]) rabbitStatus[ip] = { DoorStatus: "ready", NumberOfRFID: null };
    if (isPythonActive[ip] === undefined) isPythonActive[ip] = false;

    return { ip, newName: hostname, oldName: existingNameForIp };
  }

  // -- Case 1: completely new IP + hostname ---------------------
  console.log(`[New cabinet] ${hostname} ? ${ip}`);
  knownIPs.add(ip);
  ipToName[ip]       = hostname;
  nameToIp[hostname] = ip;

  if (!rabbitStatus[ip]) rabbitStatus[ip] = { DoorStatus: "ready", NumberOfRFID: null };
  if (isPythonActive[ip] === undefined) isPythonActive[ip] = false;

  return { ip, newName: hostname, oldName: null };
}

// ======================================================
// REMOVE cabinets inactive for more than TIMEOUT_MS
// (kept for manual/future use — no longer called
// automatically by the heartbeat interval below)
// ======================================================
function removeCabinet(ip) {
  const name = ipToName[ip];
  if (!name) return null;

  knownIPs.delete(ip);
  delete ipToName[ip];
  delete nameToIp[name];
  delete rabbitStatus[ip];
  delete isPythonActive[ip];
  delete lastSeenTime[ip];

  console.log(`[Timeout] Cabinet removed: ${name} (${ip}) — inactive for over ${TIMEOUT_MS / 60000} minutes`);
  return { ip, removedName: name };
}

// ------------------------------------------------------
// Heartbeat check every 2 minutes.
//
// IMPORTANT CHANGE: previously this called removeCabinet()
// and permanently deleted cabinets from the registry once
// no browser had forwarded MQTT traffic for TIMEOUT_MS.
// Since MQTT is only relayed to the server via a connected
// browser tab, this meant closing all browser tabs for a
// while would wipe every cabinet from the server's registry
// — and every client that opened the app afterward would see
// an empty list ("Loading cabinet configuration..." stuck).
//
// Cabinets are no longer deleted automatically. They just get
// logged as stale; the frontend's own "No Status" indicator
// (cabinetStatusAge) already handles showing staleness in the UI.
// ------------------------------------------------------
setInterval(() => {
  const now = Date.now();

  for (const ip of [...knownIPs]) {
    const last = lastSeenTime[ip];
    if (last === undefined) continue;
    if (now - last > TIMEOUT_MS) {
      console.log(`[Heartbeat] ${ipToName[ip] || ip} inactive > ${TIMEOUT_MS / 60000} min (kept in registry, not removed)`);
      // removeCabinet(ip) intentionally NOT called anymore.
    }
  }
}, 2 * 60 * 1000); // run every 2 minutes

// ======================================================
// REGISTRY SNAPSHOT
// ======================================================
function getRegistrySnapshot() {
  const allIps = [...knownIPs];
  const n2ip   = {};
  const ip2n   = {};
  allIps.forEach((ip) => {
    const n  = ipToName[ip] || ip;
    ip2n[ip] = n;
    n2ip[n]  = ip;
  });
  return {
    prefix: NETWORK_PREFIX,
    cabinets: allIps.map(ip => ipToName[ip] || ip),
    cabinetsIp: allIps,
    ipToName: ip2n,
    nameToIp: n2ip,
    numberOfCabinets: allIps.length,
    ipStartFrom: IP_START_FROM,
  };
}

console.log("========================================");
console.log("Network Prefix  :", NETWORK_PREFIX);
console.log("Initial IPs     :", INITIAL_IPS);
console.log("Initial Names   :", INITIAL_NAMES);
console.log("Registry file   :", REGISTRY_FILE);
console.log("Cabinets in registry now:", [...knownIPs].map(ip => ipToName[ip] || ip));
console.log("========================================");

// ======================================================
// API ENDPOINTS
// ======================================================

app.get("/api/network-prefix", (req, res) => {
  res.json(getRegistrySnapshot());
});

// React fetches this once on startup instead of hardcoding the broker IP.
app.get("/api/broker-config", (req, res) => {
  res.json(brokerPublicPayload(brokerConfig));
});

// Change the broker IP at any time, while npm run dev / the Docker
// container is still running â€” no restart, no rebuild.
// Example:
//   curl -X POST http://localhost:3001/api/broker-config \
//        -H "Content-Type: application/json" \
//        -d '{"ip":"192.168.137.1"}'
app.post("/api/broker-config", (req, res) => {
  const { ip } = req.body || {};
  if (!ip) return res.status(400).json({ error: "ip wajib diisi" });

  brokerConfig = { ip };
  lastBrokerConfigWriteAt = Date.now();
  saveBrokerConfig(brokerConfig);

  const payload = brokerPublicPayload(brokerConfig);
  io.emit("broker-config-update", payload); // every open React tab reconnects live

  console.log(`[Broker Config] Updated via API -> ${payload.ws}`);
  res.json({ status: "updated", ...payload });
});

app.get("/python/activate/:mac", (req, res) => {
  const { mac }      = req.params;
  const resolvedIp   = nameToIp[mac] || mac;
  if (!knownIPs.has(resolvedIp)) return res.status(400).send("Invalid MAC");
  isPythonActive[resolvedIp] = true;
  console.log(`[Python] ${ipToName[resolvedIp] || resolvedIp} activated.`);
  res.status(200).send(`Python ${resolvedIp} activated`);
});

app.get("/python/deactivate/:mac", (req, res) => {
  const { mac }      = req.params;
  const resolvedIp   = nameToIp[mac] || mac;
  if (!knownIPs.has(resolvedIp)) return res.status(400).send("Invalid MAC");
  isPythonActive[resolvedIp] = false;
  console.log(`[Python] ${ipToName[resolvedIp] || resolvedIp} deactivated.`);
  res.status(200).send(`Python ${resolvedIp} deactivated`);
});

app.post("/api/status/:mac", (req, res) => {
  const { mac }      = req.params;
  const resolvedIp   = nameToIp[mac] || mac;
  if (!knownIPs.has(resolvedIp)) return res.status(400).send("Invalid MAC");
  if (!isPythonActive[resolvedIp]) {
    console.log(`[${ipToName[resolvedIp] || resolvedIp}] Python not active.`);
    return res.status(400).send("Python inactive");
  }

  rabbitStatus[resolvedIp] = req.body;
  const { DoorStatus, NumberOfRFID } = rabbitStatus[resolvedIp];
  const cabName = ipToName[resolvedIp] || resolvedIp;

  if (DoorStatus === "Open")            console.log(`[${cabName}] Door opened.`);
  else if (DoorStatus === "Scanning")   console.log(`[${cabName}] Scanning...`);
  else if (DoorStatus === "Close") {
    rabbitStatus[resolvedIp].DoorStatus   = "Ready";
    rabbitStatus[resolvedIp].NumberOfRFID = null;
    console.log(`[${cabName}] Door ready.`);
  }

  io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: "out", payload: rabbitStatus[resolvedIp].DoorStatus });
  io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: "out", payload: { count: NumberOfRFID || 0, inventory: [] } });
  res.status(200).send("Status updated");
});

app.get("/api/status/:mac", (req, res) => {
  const { mac }    = req.params;
  const resolvedIp = nameToIp[mac] || mac;
  res.json(rabbitStatus[resolvedIp] || {});
});

// ======================================================
// COMMAND HANDLER
// Frontend sends cabinet name (e.g. MASCAB01C), server resolves to IP
// ======================================================
app.post("/api/command", async (req, res) => {
  const { mac, topic, message } = req.body;
  if (!mac) return res.status(400).json({ error: "MAC missing" });

  const resolvedIp    = nameToIp[mac] || mac;
  const cabName       = ipToName[resolvedIp] || mac;
  const resolvedTopic = topic ? topic.replace(mac, resolvedIp) : topic;

  try {
    if (knownIPs.has(resolvedIp)) {
      console.log(`[Command] ${cabName} (${resolvedIp}) ? ${resolvedTopic}`);
      io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: resolvedTopic, payload: message });

      let parsed = message;
      try { if (typeof message === "string") parsed = JSON.parse(message); } catch {}
      const cmd = typeof parsed === "string" ? parsed.toLowerCase() : parsed?.cmd?.toLowerCase();

      if (cmd === "scan") {
        io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: "out", payload: "Open" });
        setTimeout(() => {
          io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: "out", payload: "Close" });
        }, 2000);
      }
      if (cmd === "stop") {
        io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: "out", payload: "Ready" });
        io.to(resolvedIp).emit("status-update", { mac: resolvedIp, topic: "out", payload: { count: 0, inventory: [] } });
      }
      if (cmd === "reboot") {
        io.to(resolvedIp).emit("reboot-broadcast", { mac: resolvedIp });
      }

      return res.json({ status: "Message sent", mac: resolvedIp, cabName, topic: resolvedTopic, message });
    }

    return res.status(400).json({ error: "Unknown MAC/Name" });
  } catch (err) {
    console.error("Error handling command:", err);
    return res.status(500).json({ error: err.message });
  }
});

app.get("/api/sessions", (req, res) => res.json({}));

// ======================================================
// SOCKET.IO — MQTT OUT FORWARDER
// React forwards all "out" MQTT payloads to the server.
// Server auto-registers new/renamed cabinets and broadcasts
// "cabinet-update" to all connected clients.
// ======================================================
io.on("connection", (socket) => {
  console.log("Client connected:", socket.id);

  socket.on("join-mac", (mac) => {
    const resolvedIp = nameToIp[mac] || mac;
    socket.join(resolvedIp);
    console.log(`Joined room: ${ipToName[resolvedIp] || mac} (${resolvedIp})`);
  });

  socket.on("mqtt-out", (payload) => {
    if (!payload || typeof payload !== "object") return;

    const ip       = payload.IP_ETH0 || null;
    const hostname = payload.hostname || payload.mac || null;

    if (!ip || !hostname) return;

    // Always update lastSeenTime
    lastSeenTime[ip] = Date.now();

    const event = autoRegisterCabinet(ip, hostname);

    if (event) {
      saveRegistryToDisk(); // persist every registry change immediately
      socket.join(ip);

      // Only broadcast to React if it's a meaningful UI change:
      // - new cabinet (oldName === null)
      // - rename (oldName !== newName)
      // Skip if only IP changed but name stayed the same (ipChanged flag)
      if (!event.ipChanged) {
        io.emit("cabinet-update", {
          event,
          registry: getRegistrySnapshot(),
        });
        console.log(`[Broadcast] cabinet-update ? ${hostname} (${ip}), oldName: ${event.oldName}`);
      } else {
        console.log(`[IP-change silent] ${hostname}: ${event.oldIp} ? ${ip} — no UI update needed`);
      }
    }
  });

  socket.on("disconnect", () => console.log("Client disconnected:", socket.id));
});

// ======================================================
// START SERVER
// ======================================================
server.listen(PORT, () => {
  console.log("========================================");
  console.log(`Server running on port ${PORT}`);
  console.log(`Mode: ${process.env.HOST_IP ? "Docker (HOST_IP=" + process.env.HOST_IP + ")" : "Local (auto-detect)"}`);
  console.log(`Network Prefix: ${NETWORK_PREFIX}`);
  console.log("Initial Cabinets:");
  INITIAL_NAMES.forEach((name, i) => console.log(`   ${name} ? ${INITIAL_IPS[i]}`));
  console.log(`Broker config file: ${BROKER_CONFIG_FILE}`);
  console.log(`Broker WS URL     : ${brokerWsUrl(brokerConfig)}`);
  console.log("========================================");
});
