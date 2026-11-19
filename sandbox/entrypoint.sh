#!/bin/sh
set -eu

mkdir -p /data
touch '/data/First Run'
rm -rf /data/Singleton*

/usr/local/bin/sandbox-proxy --init-ca
if [ -f /etc/sandbox-proxy/ca.crt ]; then
  cp /etc/sandbox-proxy/ca.crt /usr/local/share/ca-certificates/z3r0-sandbox-mitm-ca.crt
  update-ca-certificates >/dev/null 2>&1
  if command -v certutil >/dev/null 2>&1; then
    mkdir -p /root/.pki/nssdb
    if [ ! -f /root/.pki/nssdb/cert9.db ]; then
      certutil -d sql:/root/.pki/nssdb -N --empty-password
    fi
    certutil -d sql:/root/.pki/nssdb -D -n "Z3r0 Sandbox MITM Root CA" >/dev/null 2>&1 || true
    certutil -d sql:/root/.pki/nssdb -A -t "C,," -n "Z3r0 Sandbox MITM Root CA" -i /etc/sandbox-proxy/ca.crt
  fi
fi

echo "sandbox entrypoint starting supervisord"
exec supervisord -c /supervisord.conf
