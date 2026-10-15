#!/usr/bin/env bash
# Step 7: build the three images in Cloud Build and push them to Artifact Registry.
#   api  (one image for every backend role)   web  (Next.js, API URL baked in)   auth  (Keycloak, pre-built)
source "$(dirname "$0")/lib.sh"
TAG="${TAG:-$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
say "Cloud Build: tag ${TAG}"
gcloud builds submit "$REPO" --config="$HERE/cloudbuild.yaml" --region="$REGION" \
  --substitutions="_REGION=${REGION},_NAME=${NAME},_TAG=${TAG},_API_URL=${API_URL},_PLANT_TZ=${TZ_PLANT},_DEPLOY=false"
echo "$TAG" > "$HERE/.last_tag"
echo "images: ${AR}/{api,web,auth}:${TAG}"
