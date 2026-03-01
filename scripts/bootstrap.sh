#!/bin/bash
# =============================================================================
# SNIPER v10.10 — Bootstrap Script
# Run once after cloning: bash scripts/bootstrap.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${GREEN}SNIPER v10.10 — Bootstrap${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Check Docker
if ! command -v docker &>/dev/null; then
  echo -e "${RED}ERROR: Docker not found. Install Docker Desktop first.${NC}"; exit 1
fi
if ! command -v docker compose &>/dev/null; then
  echo -e "${RED}ERROR: docker compose not found. Update Docker to v2.x.${NC}"; exit 1
fi

# 2. Copy .env template
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo -e "${YELLOW}⚠ .env created from template. FILL IN your credentials before proceeding.${NC}"
else
  echo -e "${GREEN}✓ .env already exists${NC}"
fi

# 3. Create data directories (gitignored volumes)
mkdir -p data/{raw/{ohlcv,funding,unlocks},processed/parquet,models,calibration}
mkdir -p logs infra/nginx/ssl
echo -e "${GREEN}✓ Data directories created${NC}"

# 4. Set permissions
chmod 600 .env
echo -e "${GREEN}✓ .env permissions set to 600${NC}"

# 5. Validate critical .env keys are not placeholders
REQUIRED_KEYS=("BINANCE_API_KEY" "BINANCE_API_SECRET" "API_SECRET_KEY")
for key in "${REQUIRED_KEYS[@]}"; do
  val=$(grep "^${key}=" .env | cut -d= -f2)
  if [[ -z "$val" || "$val" == *"your_"* || "$val" == *"here"* ]]; then
    echo -e "${YELLOW}⚠ WARNING: ${key} appears to be a placeholder.${NC}"
  fi
done

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Bootstrap complete. Next steps:${NC}"
echo "  1. Edit .env with real credentials"
echo "  2. Verify BINANCE_TESTNET=true (required for Phase 1-7)"
echo "  3. docker compose build"
echo "  4. docker compose up -d"
echo "  5. Visit http://localhost:80"
