<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

---
name: AVD Environment Setup
description: Create a Python virtual environment with AVD dependencies (pyavd and arista.avd collection)
version: "1.0.0"
tags:

- avd
- python
- ansible
- virtual-environment
- setup

---

# AVD Environment Setup Skill

This skill provides instructions for setting up a Python virtual environment with all required AVD dependencies.

## Prerequisites

- Python 3.10 or higher
- pip (Python package manager)

## Project Structure

Every AVD project should have these files at the root:

```text
<project_root>/
├── .venv/                  # Virtual environment (git-ignored)
├── requirements.txt        # Python dependencies
├── requirements.yml        # Ansible Galaxy collections
├── inventory.yml
├── build.yml
├── deploy.yml
└── group_vars/
```

## Required Files

### requirements.txt

Create `requirements.txt` at the project root:

```text
pyavd[ansible]
```

### requirements.yml

Create `requirements.yml` at the project root:

```yaml
---
collections:
  - name: arista.avd
```

## Quick Setup

Run these commands from your AVD project root:

```bash
# Create virtual environment at project root
python3 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Upgrade pip
pip install --upgrade pip

# Install Python dependencies from requirements.txt
pip install -r requirements.txt

# Install Ansible collections from requirements.yml
ansible-galaxy collection install -r requirements.yml
```

## Detailed Steps

### 1. Create Virtual Environment

```bash
python3 -m venv .venv
```

This creates an isolated Python environment in the `.venv` directory.

### 2. Activate the Environment

**Linux/macOS:**

```bash
source .venv/bin/activate
```

**Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

**Windows (CMD):**

```cmd
.venv\Scripts\activate.bat
```

Your prompt should now show `(.venv)` prefix.

### 3. Install PyAVD with Ansible Extras

```bash
pip install "pyavd[ansible]"
```

This installs:

- `pyavd` - Python AVD library for schema validation and config generation
- `ansible-core` - Ansible automation engine
- `jinja2` - Template engine
- Other required dependencies

### 4. Install Arista AVD Collection

```bash
ansible-galaxy collection install arista.avd
```

This installs the Ansible collection with all AVD roles:

- `eos_designs` - Generate structured configurations
- `eos_cli_config_gen` - Generate EOS CLI configurations
- `eos_config_deploy_eapi` - Deploy via eAPI
- `cv_deploy` - Deploy via CloudVision
- `anta_runner` - Network validation with ANTA

### 5. Verify Installation

```bash
# Check pyavd version
python -c "import pyavd; print(f'pyavd version: {pyavd.__version__}')"

# Check Ansible version
ansible --version

# Check AVD collection
ansible-galaxy collection list | grep arista.avd
```

## Deactivate Environment

When finished working:

```bash
deactivate
```

## Troubleshooting

### Permission Denied

Use `--user` flag or ensure you're in the virtual environment:

```bash
pip install --user "pyavd[ansible]"
```

### Collection Not Found

Ensure Ansible is installed first:

```bash
pip install ansible-core
ansible-galaxy collection install arista.avd
```

### Python Version Too Old

AVD requires Python 3.10+. Check your version:

```bash
python3 --version
```
