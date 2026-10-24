#!/usr/bin/env bash
# Sets up (or updates) Devil's Workshop on an Ubuntu server, such as a Hetzner Cloud server: see
# docs/cloud-setup.md. Run it as root:
#
#   curl -fsSL https://raw.githubusercontent.com/wowanirudh-boop/devils-workshop/main/deploy/setup-server.sh | bash
#
# Running it again updates the app to the latest version and restarts it; your stories and settings stay.
#
# What it does: installs Python, a font for PDFs and Tailscale; makes a user "dw" for the app; puts the app in
# /opt/devils-workshop; starts it as a service (deploy/devils-workshop.service) that starts again with the
# server; and turns on the firewall so that only SSH, and Tailscale, can reach the server (on Oracle Cloud,
# its own firewall rules, with Tailscale added).
#
# Settings (environment variables): REPO, BRANCH (where the app comes from), SOURCE (a folder to copy the app
# from instead), SKIP_TAILSCALE=1, SKIP_FIREWALL=1 (both for testing).
set -euo pipefail

REPO=${REPO:-https://github.com/wowanirudh-boop/devils-workshop.git}
BRANCH=${BRANCH:-main}
SOURCE=${SOURCE:-}
DIR=/opt/devils-workshop
APP_USER=dw
PORT=8080

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this as root (for example: sudo bash setup-server.sh)." >&2
    exit 1
fi

echo "== Installing Python, git and a font for PDFs"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv git fonts-dejavu-core curl rsync

if [ "${SKIP_TAILSCALE:-}" != 1 ] && ! command -v tailscale >/dev/null; then
    echo "== Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
fi

if ! id -u "$APP_USER" >/dev/null 2>&1; then
    echo "== Making the user $APP_USER (the app runs as it; your stories live in /home/$APP_USER)"
    useradd --system --create-home --home-dir "/home/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "== Getting the app into $DIR"
if [ -n "$SOURCE" ]; then
    mkdir -p "$DIR"
    rsync -a --delete --exclude .git --exclude .venv --exclude build --exclude dist "$SOURCE"/ "$DIR"/
elif [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$DIR" checkout -q -B "$BRANCH" FETCH_HEAD
else
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi

echo "== Installing the app's libraries"
python3 -m venv "$DIR/.venv"
# pywebview draws the Windows app window and pystray its icon beside the clock; a server has neither.
grep -v -i -E '^(pywebview|pystray)' "$DIR/requirements.txt" > "$DIR/.server-requirements.txt"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/.server-requirements.txt"

echo "== Starting it as a service"
install -m 644 "$DIR/deploy/devils-workshop.service" /etc/systemd/system/devils-workshop.service
systemctl daemon-reload
systemctl enable devils-workshop >/dev/null 2>&1
systemctl restart devils-workshop

if [ "${SKIP_FIREWALL:-}" != 1 ] && dpkg -s iptables-persistent >/dev/null 2>&1 \
        && [ -f /etc/iptables/rules.v4 ]; then
    # Oracle Cloud's Ubuntu: its own rules (only SSH comes in) are loaded at every boot from rules.v4. Keep them
    # (installing ufw would remove what loads them) and let Tailscale in beside SSH.
    echo "== Firewall: this server's own rules (SSH only), plus Tailscale"
    if ! grep -q '^-A INPUT -i tailscale0 -j ACCEPT' /etc/iptables/rules.v4; then
        cp /etc/iptables/rules.v4 /etc/iptables/rules.v4.before-devils-workshop
        sed -i '/^-A INPUT -i lo -j ACCEPT/a -A INPUT -i tailscale0 -j ACCEPT' /etc/iptables/rules.v4
    fi
    grep -q '^-A INPUT -i tailscale0 -j ACCEPT' /etc/iptables/rules.v4 \
        || echo "Couldn't add Tailscale to /etc/iptables/rules.v4: add '-A INPUT -i tailscale0 -j ACCEPT' yourself." >&2
    iptables -C INPUT -i tailscale0 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -i tailscale0 -j ACCEPT
elif [ "${SKIP_FIREWALL:-}" != 1 ]; then
    echo "== Firewall: SSH from anywhere; everything else only over Tailscale"
    apt-get install -y -q ufw
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow OpenSSH >/dev/null
    ufw allow 41641/udp >/dev/null   # lets Tailscale connect your phone directly (faster)
    ufw allow in on tailscale0 >/dev/null
    ufw --force enable >/dev/null
fi

echo "== Checking that it answers"
for _ in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:$PORT/manifest.webmanifest" >/dev/null; then
        echo "Devil's Workshop is running."
        break
    fi
    sleep 1
done
if ! curl -fs "http://127.0.0.1:$PORT/manifest.webmanifest" >/dev/null; then
    echo "It didn't start. See: journalctl -u devils-workshop -n 50" >&2
    exit 1
fi

if [ "${SKIP_TAILSCALE:-}" != 1 ] && ! tailscale status >/dev/null 2>&1; then
    echo
    echo "== Connect this server to your Tailscale: open the link below and sign in with the same account as"
    echo "   your phone."
    tailscale up
fi

cat <<EOF

Done. Next (docs/cloud-setup.md, step 5): on your PC, run
    ssh -L $PORT:127.0.0.1:$PORT root@<this server's address>
and open http://localhost:$PORT in your browser to add your keys and switch on phone access.
EOF
