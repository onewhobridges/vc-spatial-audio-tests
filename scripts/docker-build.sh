#!/bin/bash
# scripts/docker-build.sh
# Builds and tags the Vesktop test image.
# Usage: scripts/docker-build.sh [vesktop-version]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VESKTOP_VERSION="${1:-1.6.5}"
TAG="vesktop-test:${VESKTOP_VERSION}"

echo "Building $TAG (VESKTOP_VERSION=$VESKTOP_VERSION)..."
docker build \
    --build-arg "VESKTOP_VERSION=$VESKTOP_VERSION" \
    -f "$REPO_ROOT/docker/Dockerfile" \
    -t "$TAG" \
    "$REPO_ROOT"

echo "Built $TAG"
