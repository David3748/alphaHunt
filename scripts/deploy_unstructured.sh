#!/usr/bin/env bash
# deploy_unstructured.sh - rsync the unstructured proto code to the VM and launch
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
VM_IP="${ALPHAHUNT_VM_IP:?set ALPHAHUNT_VM_IP to the VM public IP}"
SSH_KEY="$HOME/.ssh/alphahunt_azure"
SSH="ssh -i $SSH_KEY azureuser@$VM_IP"
RSYNC="rsync -avz -e 'ssh -i $SSH_KEY'"

echo "=== Deploying unstructured_proto to VM ==="

# Sync source files
$RSYNC "$ROOT/src/unstructured_proto.py" "azureuser@$VM_IP:/opt/alphahunt/src/"
$RSYNC "$ROOT/config/unstructured_proto.json" "azureuser@$VM_IP:/opt/alphahunt/config/"
$RSYNC "$ROOT/scripts/run_unstructured.sh" "azureuser@$VM_IP:/opt/alphahunt/"

# Set executable
$SSH "chmod +x /opt/alphahunt/run_unstructured.sh"

echo "=== Deploy complete ==="

# Launch if requested
if [ "${1:-}" = "--launch" ]; then
    echo "=== Launching pipeline in tmux 'unstructured' ==="
    $SSH "tmux kill-session -t unstructured 2>/dev/null; tmux new-session -d -s unstructured 'cd /opt/alphahunt && bash run_unstructured.sh 2>&1 | tee lab_runs/unstructured_proto/pipeline.log'"
    echo "Monitor: ssh -i $SSH_KEY azureuser@$VM_IP 'tmux attach -t unstructured'"
fi