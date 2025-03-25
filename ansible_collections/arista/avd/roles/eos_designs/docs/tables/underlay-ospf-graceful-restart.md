<!--
  ~ Copyright (c) 2025 Arista Networks, Inc.
  ~ Use of this source code is governed by the Apache License 2.0
  ~ that can be found in the LICENSE file.
  -->
=== "Table"

    | Variable | Type | Required | Default | Value Restrictions | Description |
    | -------- | ---- | -------- | ------- | ------------------ | ----------- |
    | [<samp>underlay_ospf_graceful_restart</samp>](## "underlay_ospf_graceful_restart") | Boolean |  | `False` |  | Enable graceful restart for OSPF underlay. |

=== "YAML"

    ```yaml
    # Enable graceful restart for OSPF underlay.
    underlay_ospf_graceful_restart: <bool; default=False>
    ```
