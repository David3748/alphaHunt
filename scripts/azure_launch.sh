#!/bin/sh
set -eu

RESOURCE_GROUP=${ALPHAHUNT_RESOURCE_GROUP:-alphahunt-century-rg}
VM_NAME=${ALPHAHUNT_VM_NAME:-alphahunt-century}
LOCATION=${ALPHAHUNT_LOCATION:-westus2}
VM_SIZE=${ALPHAHUNT_VM_SIZE:-Standard_B4as_v2}
ADMIN_USER=${ALPHAHUNT_ADMIN_USER:-azureuser}
SSH_KEY=${ALPHAHUNT_SSH_KEY:-$HOME/.ssh/alphahunt_azure}
SOURCE_IP=${ALPHAHUNT_SOURCE_IP:-$(curl -fsS https://api.ipify.org)}

mkdir -p "$(dirname "$SSH_KEY")"
if [ ! -f "$SSH_KEY" ]; then
  ssh-keygen -q -t ed25519 -N '' -f "$SSH_KEY"
fi

if ! az group show --name "$RESOURCE_GROUP" --output none 2>/dev/null; then
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
fi
az vm create \
  --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" --location "$LOCATION" \
  --image Ubuntu2404 --size "$VM_SIZE" --admin-username "$ADMIN_USER" \
  --ssh-key-values "$SSH_KEY.pub" --os-disk-size-gb 256 --storage-sku StandardSSD_LRS \
  --public-ip-sku Standard --output none

NSG_NAME="${VM_NAME}NSG"
az network nsg rule update --resource-group "$RESOURCE_GROUP" --nsg-name "$NSG_NAME" \
  --name default-allow-ssh --source-address-prefixes "$SOURCE_IP/32" --output none
az network nsg rule create --resource-group "$RESOURCE_GROUP" --nsg-name "$NSG_NAME" \
  --name allow-monitor --priority 1010 --access Allow --protocol Tcp --direction Inbound \
  --source-address-prefixes "$SOURCE_IP/32" --destination-port-ranges 8080 --output none

PUBLIC_IP=$(az vm show --show-details --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" \
  --query publicIps --output tsv)
printf '%s\n' "$PUBLIC_IP"
