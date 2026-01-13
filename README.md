# 📡 Quantize-and-Interferece-aware-experiments

This project provides tools for managing and performing distributed beamforming measurements using USRP B210 devices. It includes tools for synchronization, configuration, and measurement orchestration, using `zmq`, `ansible`, and custom Python scripts.

---

## 🗂️ Directory Structure

```
/Quantize_aware_experiments
├── Ansible/
│   ├── inventory.yaml               # List of target hosts
│   ├── kill.yml                     # Kill running measurement scripts
│   └── pull_code.yml                # Pull the latest code from Git
│   └── comp.yml                     # Start the experiments
│── client/                          
│   │   ├── pilot.py                 # Generate pilot signal at UE client
│   │   ├── usrp-cal-bf.py           # Full uplink and downlink process at APs' client
│   │   ├── cal-settings.yml         # USRPs configuration
│   │   ├── usrp_b210_fpga_loopback_ctrl.bin # Custom FPGA image
│   │   └── tools.py                 # Support tool functions
└── Server/
│   └── helper.py
│   ├── meas-phaes.py
│   ├── scope.py
│   ├── sync-server.py               # Synchronization from server
│── data/                            # Auto-generated measurement result files (YAML)
```

---

## 🚀 Measurement Workflow
---

### On the server:

1. **If do it on the Test Tiles:*
   
```bash
export PYTHONPATH="/usr/local/lib/python3.11/site-packages:$PYTHONPATH"
```

2. **If do it on the ceiling Tiles:*

```bash
export PYTHONPATH="/usr/local/lib/python3/dist-packages:$PYTHONPATH"
```
---

### Reference Signal generator:

```bash
python3 examples/tx_waveforms.py  --args "type=b200" --freq 920e6 --rate 1e6 --duration 1e8 --channels 0 --wave-freq 0e5 --wave-ampl 0.8 --gain 70
```

### On the server:

1. **Kill and pull the latest code:**

   ```ansible
   ansible-playbook -i inventory.yaml pull_code.yml -f40
   ```

2. **Start synchronization server:**

   ```bash
   python3 Server/sync-server.py
   ```

3. **Start beamforming server:**

   ```ansible
   ansible-playbook -i inventory.yaml comp.yml -f40
   ```

## 🧪 TODO

* ✅ Validate the downlink transmission phase stability
* 🔧 Validate the reciprocity-based calibration
