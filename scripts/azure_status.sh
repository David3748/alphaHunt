#!/bin/sh
set -eu
RESOURCE_GROUP=${ALPHAHUNT_RESOURCE_GROUP:-alphahunt-century-rg}
VM_NAME=${ALPHAHUNT_VM_NAME:-alphahunt-century}
az vm get-instance-view --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" \
  --query "{power:instanceView.statuses[1].displayStatus,provisioning:provisioningState}" -o table
az vm show --show-details --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" \
  --query "{ip:publicIps,size:hardwareProfile.vmSize}" -o table
