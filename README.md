# 📡 QPSK Transmitter & Receiver with Real-time Constellation Streaming (USRP B210, UHD, ZeroMQ, Flask)

This repository implements a full QPSK communication chain using **USRP B210**.  
It includes:

- A **transmitter** that converts WAV audio to binary, performs QPSK modulation with RRC shaping, and continuously transmits via UHD.
- A **receiver** that streams IQ samples from B210, performs multi-stage synchronization, and outputs clean QPSK symbols.
- A **real-time constellation visualization server** that receives symbols via ZeroMQ and displays them on a Flask dashboard.

---

## ✨ Features

### **Transmitter**
- WAV → binary conversion  
- Optional **Barker preamble** insertion  
- QPSK modulation  
- RRC pulse shaping  
- Oversampling (sps = 4)  
- Continuous transmission via **USRP B210 (UHD)**  
- Built-in constellation & PSD plotting utilities  

---

### **Receiver**
- Continuous IQ capture via UHD (start_cont mode)
- Block-based processing pipeline
- **Coarse frequency synchronization** (4th-power FFT method)
- **Mueller & Müller (M&M)** timing recovery
- **4th-order Costas loop** fine carrier sync
- Normalization + symbol-rate sampling
- Constellation snapshots at:
  - Before Sync  
  - After Coarse Sync  
  - After Timing Sync  
  - After Fine Carrier Sync  
- Optional saving of constellation figures
- Sends processed symbols to visualization server via **ZeroMQ PUB**

---

### **Visualization Server**
- ZeroMQ **SUB** listening on `tcp://*:50001`
- Downsampling + point limiting (performance-safe)
- Creates constellation PNG using Matplotlib (`Agg` backend)
- Flask web app with auto-refreshing display (300 ms interval)

Access in browser:
`http://<server-ip>:8000`
