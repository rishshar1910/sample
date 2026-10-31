#!/usr/bin/env bash
set -u
fail=0

docker network create irrig-net
docker run -d --name mysql --network irrig-net \
  -e MYSQL_ROOT_PASSWORD=rootpass -e MYSQL_DATABASE=irrigation \
  -e MYSQL_USER=irrig -e MYSQL_PASSWORD=irrigpass mysql:8.0
docker run -d --name redis --network irrig-net redis:7-alpine
until docker exec mysql mysqladmin ping -uroot -prootpass --silent; do sleep 3; done
sleep 5

docker run -d --name api --network irrig-net -p 5000:5000 \
  --security-opt apparmor=irrigation-profile \
  -e DB_HOST=mysql -e REDIS_HOST=redis irrigation-api:1.0
docker run -d --name api-control --network irrig-net \
  --security-opt apparmor=unconfined \
  -e DB_HOST=mysql -e REDIS_HOST=redis irrigation-api:1.0

echo "--- Profile enforced by Docker ---"
docker inspect -f '{{.AppArmorProfile}}' api
docker inspect -f '{{.AppArmorProfile}}' api-control

echo "--- App works under the profile ---"
for i in $(seq 1 20); do curl -sf localhost:5000/health && break; sleep 3; done
echo
curl -s -X POST localhost:5000/reading -H 'Content-Type: application/json' \
  -d '{"field_id":"field-1","moisture":15}'
echo
curl -s localhost:5000/recommend/field-1
echo

check() {
  out=$(docker exec -u root "$1" python -c "$3" 2>&1); rc=$?
  last=$(echo "$out" | tail -1)
  if [ "$4" = denied ] && [ $rc -ne 0 ]; then echo "[OK] $2: blocked (exit $rc) $last"
  elif [ "$4" = allowed ] && [ $rc -eq 0 ]; then echo "[OK] $2: allowed"
  else echo "[FAIL] $2: exit $rc $last"; fail=1; fi
}

echo "--- With the AppArmor profile (should be blocked) ---"
check api "read /etc/shadow"  "open('/etc/shadow').read()" denied
check api "write /etc/x"      "open('/etc/x','w')" denied
check api "write /app/x"      "open('/app/x','w')" denied
check api "start /bin/sh"     "import subprocess;subprocess.run(['/bin/sh','-c','echo hi'],check=True)" denied
check api "write /tmp/x"      "open('/tmp/x','w')" allowed

echo "--- Control without a profile (should work) ---"
check api-control "read /etc/shadow" "open('/etc/shadow').read()" allowed
check api-control "write /app/x"     "open('/app/x','w')" allowed
check api-control "start /bin/sh"    "import subprocess;subprocess.run(['/bin/sh','-c','echo hi'],check=True)" allowed

echo "--- Kernel denial log ---"
sudo dmesg | grep -i 'apparmor="DENIED"' | tail -5 || true

docker logs api | tail -5
if [ $fail -eq 0 ]; then echo "All AppArmor checks passed"; else echo "Some checks failed"; fi
exit $fail
