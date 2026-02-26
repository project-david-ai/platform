# Cisco Modeling Labs (CML) Free Tier — Setup Guide

## Overview

CML Free runs as a VM and provides stable IOS-based router emulation suitable for network automation testing. Unlike GNS3, nodes run as native Linux processes (IOL) rather than emulated hardware, giving much faster boot times and stable telnet port assignments.

---

## Prerequisites

- VMware Workstation, Player, or Fusion installed
- 16GB+ RAM on host recommended (CML takes ~4GB, each IOL node ~512MB)
- Hyper-V **disabled** on Windows (Control Panel → Turn Windows Features On or Off)
- Cisco account (free) at [developer.cisco.com](https://developer.cisco.com)

---

## Step 1 — Download Files

Go to `software.cisco.com/download` and search for **Cisco Modeling Labs Free**.

Download both files:

| File | Description | Size |
|------|-------------|------|
| `cml2_f_2.9.1-7_amd64-7.ova` | CML server VM image for VMware | ~1.3 GB |
| `refplat-20250616-free-iso.zip` | Router/switch images (refplat ISO) | ~2.6 GB |

> The refplat zip **must be extracted** before use — you need the `.iso` file inside it.

---

## Step 2 — Deploy the OVA in VMware

1. Open VMware → **File → Open** → select the `.ova` file
2. Accept the import defaults
3. Before starting the VM, go to **VM Settings → CD/DVD Drive**:
   - Set to **Use ISO image file**
   - Browse to the extracted `refplat-XXXXXX-free.iso`
   - Check **"Connect at power on"**
4. Start the VM

---

## Step 3 — First Boot Setup Wizard

The VM boots into a text-based setup wizard.

### Optional Services Screen
Use arrow keys + **Space** to enable both:
- `[X] OpenSSH` — SSH access to the CML server on port 1122
- `[X] ATty` — Lab node port forwarding (required for telnet to routers)

> If you miss this step, these can be enabled later via Cockpit.

### Confirm Screen
Verify before confirming:

| Field | Expected Value |
|-------|---------------|
| Deployment | Standalone All-in-One |
| Platform ISO/CD-ROM | Should show ISO path (NOT "NOT ATTACHED") |
| Optional Services | OpenSSH, ATty |

> ⚠️ If ISO shows **NOT ATTACHED** — go back, ensure the ISO is mounted in VMware settings first.

### Credentials
The wizard creates two separate sets of credentials:

| Interface | Default Username | Notes |
|-----------|-----------------|-------|
| CML Dashboard (`192.168.x.x`) | `admin` | Lab management UI |
| Cockpit (`192.168.x.x:9090`) | `admin` | System administration UI — **separate password** |

> Note these down separately — they are configured independently during setup.

---

## Step 4 — Copy Refplat ISO to Disk

After the VM finishes booting:

1. Open a browser and go to `https://<CML-IP>:9090`
2. Log in with Cockpit admin credentials
3. Click **CML2** in the left navigation
4. Scroll to the **Maintenance** section
5. Click **Copy Refplat ISO** button
6. Wait for completion — output should show files being restored and end without errors

Successful output looks like:
```
*** Found cdrom device /dev/disk/by-label/REFPLAT
*** Copying content of /dev/sr0 to /var/lib/libvirt/images...
xorriso : UPDATE : 19 files restored...
```

If you see `*** Did not find any cdrom device` — the ISO is not properly mounted in VMware. Power down the VM, re-attach the ISO in VMware settings, and retry.

---

## Step 5 — Verify Node Definitions

1. Open `https://<CML-IP>` in a browser
2. Log in with Dashboard admin credentials
3. Go to **Tools → Node Definitions**
4. You should now see IOL routers, IOL-L2 switches, ASAv, Ubuntu, etc.

If only **External Connector** and **Unmanaged Switch** appear — the refplat copy did not complete successfully. Retry Step 4.

---

## Step 6 — Create Your First Lab

1. From the Dashboard, click **ADD** to create a new lab
2. Drag nodes onto the canvas
3. Click and drag between node interfaces to create links
4. Click the **Start** (▶) button to boot the lab
5. Click any node to open its console in the browser

---

## Step 7 — Connect via Telnet (Automation)

With ATty enabled, each node gets a stable local telnet port.

Find port assignments via the CML REST API:
```
GET https://<CML-IP>/api/v0/labs/<lab-id>/nodes
```

Connect with Netmiko:
```python
from netmiko import ConnectHandler

device = {
    "device_type": "cisco_ios_telnet",
    "host": "192.168.1.9",
    "port": 9000,  # port assigned by ATty for this node
}

with ConnectHandler(**device) as conn:
    output = conn.send_command("show ip interface brief")
    print(output)
```

---

## Free Tier Node Limits

| Node Type | Included |
|-----------|----------|
| IOL (IOS router) | ✅ |
| IOL-L2 (IOS switch) | ✅ |
| ASAv | ✅ |
| Ubuntu / Alpine Linux | ✅ |
| External Connector | ✅ (doesn't count toward limit) |
| Unmanaged Switch | ✅ (doesn't count toward limit) |
| Max concurrent nodes | 5 |

---

## Useful URLs

| Interface | URL |
|-----------|-----|
| CML Dashboard | `https://192.168.x.x` |
| Cockpit Admin | `https://192.168.x.x:9090` |
| REST API | `https://192.168.x.x/api/v0/` |
| API Docs | `https://192.168.x.x/api/v0/ui/` |