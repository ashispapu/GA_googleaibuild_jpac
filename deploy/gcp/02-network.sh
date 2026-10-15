#!/usr/bin/env bash
# Step 2: private network. Nothing except the public Cloud Run services has a public address.
#   - subnet for Cloud Run direct VPC egress (services reach Cloud SQL, Redis and Qdrant on private IPs)
#   - subnet for the Qdrant VM
#   - private services access (Cloud SQL + Memorystore get addresses in a peered range)
#   - Cloud NAT (the Qdrant VM pulls its image / OS updates without a public IP)
#   - private DNS zone <name>.internal (qdrant.<name>.internal)
source "$(dirname "$0")/lib.sh"

say "VPC ${VPC}"
exists gcloud compute networks describe "$VPC" || \
  gcloud compute networks create "$VPC" --subnet-mode=custom --bgp-routing-mode=regional
for sn in "$RUN_SUBNET:$RUN_SUBNET_RANGE" "$SVC_SUBNET:$SVC_SUBNET_RANGE"; do
  exists gcloud compute networks subnets describe "${sn%%:*}" --region="$REGION" || \
    gcloud compute networks subnets create "${sn%%:*}" --network="$VPC" --region="$REGION" \
      --range="${sn#*:}" --enable-private-ip-google-access --enable-flow-logs
done

say "Private services access (Cloud SQL, Memorystore)"
exists gcloud compute addresses describe "${NAME}-psa" --global || \
  gcloud compute addresses create "${NAME}-psa" --global --purpose=VPC_PEERING \
    --prefix-length="$PSA_RANGE_PREFIX" --network="$VPC"
gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com \
  --ranges="${NAME}-psa" --network="$VPC" 2>/dev/null || \
gcloud services vpc-peerings update --service=servicenetworking.googleapis.com \
  --ranges="${NAME}-psa" --network="$VPC" --force

say "Firewall"
exists gcloud compute firewall-rules describe "${NAME}-qdrant-from-run" || \
  gcloud compute firewall-rules create "${NAME}-qdrant-from-run" --network="$VPC" --direction=INGRESS \
    --action=ALLOW --rules=tcp:6333 --source-ranges="$RUN_SUBNET_RANGE,$SVC_SUBNET_RANGE" \
    --target-tags=qdrant --description="Qdrant REST from Cloud Run egress only"
exists gcloud compute firewall-rules describe "${NAME}-iap-ssh" || \
  gcloud compute firewall-rules create "${NAME}-iap-ssh" --network="$VPC" --direction=INGRESS \
    --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=qdrant \
    --description="SSH through Identity-Aware Proxy only"

say "Cloud NAT"
exists gcloud compute routers describe "${NAME}-router" --region="$REGION" || \
  gcloud compute routers create "${NAME}-router" --network="$VPC" --region="$REGION"
exists gcloud compute routers nats describe "${NAME}-nat" --router="${NAME}-router" --region="$REGION" || \
  gcloud compute routers nats create "${NAME}-nat" --router="${NAME}-router" --region="$REGION" \
    --nat-custom-subnet-ip-ranges="$SVC_SUBNET" --auto-allocate-nat-external-ips

say "Private DNS ${NAME}.internal"
exists gcloud dns managed-zones describe "${NAME}-internal" || \
  gcloud dns managed-zones create "${NAME}-internal" --dns-name="${NAME}.internal." --visibility=private \
    --networks="$VPC" --description="PlantGuard private names"
echo "done"
