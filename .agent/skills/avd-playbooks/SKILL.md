<!--
  ~ Copyright (c) 2026 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->

---
name: AVD Playbooks
description: Create separate build, deploy, and validate playbooks for AVD projects
version: "1.0.0"
tags:

- avd
- ansible
- playbooks
- arista
- network-automation

---

# AVD Playbooks Skill

This skill provides templates for creating separate build, deploy, and validate playbooks for AVD projects.

## Playbook Overview

| Playbook | Purpose | Roles Used |
| -------- | ------- | ---------- |
| `build.yml` | Generate configs and documentation | `eos_designs`, `eos_cli_config_gen` |
| `deploy.yml` | Deploy configs to devices via eAPI | `eos_config_deploy_eapi` |
| `deploy-cvp.yml` | Deploy configs via CloudVision | `cv_deploy` |
| `validate.yml` | Validate network state with ANTA | `anta_runner` |

## Build Playbook

The build playbook generates structured configurations and device configs/documentation.

```yaml
---
# build.yml
- name: Build Configurations and Documentation
  hosts: FABRIC
  gather_facts: false
  tasks:
    - name: Generate AVD Structured Configurations and Fabric Documentation
      ansible.builtin.import_role:
        name: arista.avd.eos_designs

    - name: Generate Device Configurations and Documentation
      ansible.builtin.import_role:
        name: arista.avd.eos_cli_config_gen
```

**Output directories:**

- `intended/structured_configs/` - Structured YAML per device
- `intended/configs/` - EOS CLI configuration per device
- `documentation/` - Fabric and device documentation

## Deploy Playbook (eAPI)

Deploy configurations directly to devices using eAPI. Requires network access to devices.

```yaml
---
# deploy.yml
- name: Deploy Configurations to Devices using eAPI
  hosts: FABRIC
  gather_facts: false
  tasks:
    - name: Deploy Configurations to Devices
      ansible.builtin.import_role:
        name: arista.avd.eos_config_deploy_eapi
```

**Prerequisites:**

- Devices must be reachable via `ansible_host`
- Authentication configured in group_vars (e.g., `ansible_user`, `ansible_password`)

## Deploy Playbook (CloudVision)

Deploy configurations through CloudVision Portal/CVaaS.

```yaml
---
# deploy-cvp.yml
- name: Deploy Configurations via CloudVision
  hosts: FABRIC
  gather_facts: false
  connection: local
  tasks:
    - name: Deploy Configurations via CloudVision
      ansible.builtin.import_role:
        name: arista.avd.cv_deploy
      vars:
        cv_server: <cloudvision_ip_or_hostname>
        cv_token: "{{ lookup('file', '~/.cv_token') }}"  # Or use Ansible Vault
        # Optional: Auto-execute change control
        # cv_run_change_control: true
```

**Prerequisites:**

- CloudVision service account token
- Network access to CloudVision API

**Inventory for CVaaS:**

```yaml
# Add to inventory.yml
all:
  children:
    cloudvision:
      hosts:
        cvp:
          ansible_host: www.arista.io  # Or on-prem CVP address
```

## Validate Playbook

Validate network state using ANTA (Arista Network Test Automation).

```yaml
---
# validate.yml
- name: Validate Network State
  hosts: FABRIC
  gather_facts: false
  connection: local
  tasks:
    - name: Run ANTA Validation
      ansible.builtin.import_role:
        name: arista.avd.anta_runner
      vars:
        # Optional: Expand results in report
        anta_report_expand_results: true
```

**Output directories:**

- `anta/avd_catalogs/` - Generated test catalogs per device
- `anta/reports/` - Validation reports (CSV, Markdown, JSON)

### Advanced Validation Options

```yaml
- name: Run ANTA Validation with Options
  ansible.builtin.import_role:
    name: arista.avd.anta_runner
  vars:
    # Timeout for test execution (seconds)
    anta_runner_timeout: 30
    # Parallel device processing
    anta_runner_batch_size: 5
    # Enable extra fabric-wide tests (reachability, routing)
    avd_catalogs_extra_fabric_validation: true
    # Skip specific tests
    avd_catalogs_filters:
      - skip_tests: [VerifyNTP]
```

## Complete Workflow

Run playbooks in order:

```bash
# Step 1: Build configurations
ansible-playbook build.yml

# Step 2: Deploy configurations (choose one)
ansible-playbook deploy.yml      # Direct to devices via eAPI
ansible-playbook deploy-cvp.yml  # Via CloudVision

# Step 3: Validate network state
ansible-playbook validate.yml
```

## Best Practices

1. **Always build before deploy** - Ensure configs are generated
2. **Review before deploy** - Check `intended/configs/` before pushing
3. **Validate after deploy** - Run ANTA to confirm operational state
4. **Use Ansible Vault** - Protect passwords and tokens
5. **Separate playbooks** - Keep build/deploy/validate separate for flexibility
