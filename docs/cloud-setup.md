# Running Devil's Workshop on a cloud server

Your PC doesn't have to stay on: a small Linux server runs the app around the clock, and your phone reaches
it privately over Tailscale, from anywhere. The server does no heavy work (the models run at OpenRouter,
Venice or Experiential Labs), so the smallest server is plenty.

**What it costs:** a Hetzner Cloud CX23 (2 CPUs, 4 GB of memory, 40 GB disk) in Germany or Finland, about
€5.50–6.50 a month with its IPv4 address (October 2026; check hetzner.com/cloud). It is billed by the hour,
and deleting the server stops the bill. Tailscale's personal plan is free.

**What you need:** a Hetzner account (they may ask for ID when you sign up), a Tailscale account (the one on
your phone, if you already use it), and about 20 minutes. Everything is typed in **Windows Terminal** (or
PowerShell) on your PC.

## 1. Make an SSH key (once)

The key lets your PC sign in to the server without a password. In Windows Terminal:

```powershell
ssh-keygen -t ed25519
```

Press Enter at each question. Then show the public half, to paste into Hetzner in the next step:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

## 2. Create the server on Hetzner

In the Hetzner Cloud Console (console.hetzner.cloud): **New project**, open it, then **Add Server**:

- **Location:** Nuremberg, Falkenstein or Helsinki (the cheap servers are only there).
- **Image:** Ubuntu 24.04.
- **Type:** Shared vCPU → **CX23** (x86). The ARM **CAX11** works just as well.
- **Networking:** keep **Public IPv4** on (the app is downloaded from GitHub, which needs IPv4) and IPv6.
- **SSH keys:** **Add SSH key**, paste the line from step 1.
- **Name:** for example `devils-workshop`. Then **Create & Buy now**.

Write down the server's **IPv4 address** (like `203.0.113.10`). Below, `SERVER` means that address.

## 3. Install the app on the server

Sign in to the server:

```powershell
ssh root@SERVER
```

(Type `yes` the first time.) Then, on the server, run this one line:

```bash
curl -fsSL https://raw.githubusercontent.com/wowanirudh-boop/devils-workshop/main/deploy/setup-server.sh | bash
```

It installs Python and Tailscale, puts the app in `/opt/devils-workshop`, starts it as a service that comes
back by itself whenever the server restarts, and turns on the firewall: only SSH can reach the server from
the internet; your phone gets in only through Tailscale. It takes two or three minutes.

At the end it prints a Tailscale link. Open it in your browser and sign in with **the same Tailscale account
as your phone**. Then type `exit` to leave the server.

## 4. Tailscale on your phone

If it isn't there yet: install Tailscale from the App Store, sign in with the same account, and leave it
switched on. The server now shows in the Tailscale app as `devils-workshop`.

## 5. Add your keys and switch on phone access

The key and phone settings can only be changed from "this PC", which for the server means through an SSH
tunnel. On your PC:

```powershell
ssh -L 8080:127.0.0.1:8080 root@SERVER
```

Leave that window open, and open **http://localhost:8080** in your browser. This is the server's app, with
the full Settings:

1. **Settings → Experiential Labs:** paste your key and **Save key**, then choose the writer's and the planner's
   models in **Writer models / Planner models**. Add your OpenRouter and Venice keys too, if you use them. On the
   server, keys are kept in a file in the app's folder that only the app's user can read.
2. **Settings → Phone and other devices:** set a password, then switch on **Allow access from my phone**.
   The **Anywhere (Tailscale)** tab shows the address, like `http://devils-workshop.tail1234.ts.net:8730`,
   and its QR code.

Close the browser tab and type `exit` in the SSH window. You only need the tunnel again to change keys or
phone settings.

## 6. Use it on your phone

In Chrome on the iPhone, scan the QR code or type the Tailscale address, enter the password once and leave
**Remember this phone** ticked. **Share → Add to Home Screen** gives it an icon. It works the same as with your
PC, except that the PC can now be off.

## Your stories on the server

New stories are saved on the server, in `/home/dw/Devil's Workshop`. **Read → Download…** brings the PDF
to your phone.

To bring stories over from your PC, copy each story folder (on your PC):

```powershell
ssh root@SERVER "mkdir -p /home/dw/stories"
scp -r "$env:USERPROFILE\Documents\Devil's Workshop\My Story" root@SERVER:/home/dw/stories/
ssh root@SERVER "chown -R dw:dw /home/dw/stories"
```

Then, in the app: **All stories → Open a folder** → `/home/dw/stories/My Story`. Your default
instructions can come too:

```powershell
scp -r "$env:APPDATA\DevilsWorkshop\Default instructions" root@SERVER:/home/dw/.config/devils-workshop/
ssh root@SERVER "chown -R dw:dw /home/dw/.config/devils-workshop"
```

The server and your PC keep separate stories: write each story in one place, or copy it back the same way
(`scp -r root@SERVER:"/home/dw/stories/My Story" .`).

## Updating

Sign in (`ssh root@SERVER`) and run the same `curl … | bash` line as in step 3. It fetches the latest
version from GitHub and restarts the app; your stories, settings and keys stay.

## If something goes wrong

On the server (`ssh root@SERVER`):

| | |
|---|---|
| Is it running? | `systemctl status devils-workshop` |
| What did it do? | `journalctl -u devils-workshop -n 100` and `/home/dw/.config/devils-workshop/app.log` |
| Restart it | `systemctl restart devils-workshop` |
| Is Tailscale connected? | `tailscale status` |

- **The phone can't connect:** check that Tailscale is on, on the phone, and that `tailscale status` on the
  server lists the phone. Phone access must be switched on (step 5).
- **http://localhost:8080 doesn't open:** the SSH window from step 5 must stay open. If another program on
  your PC already uses 8080, use `ssh -L 8090:127.0.0.1:8080 root@SERVER` and open http://localhost:8090.
- **Backups:** Hetzner's **Backups** (20% on top of the server's price) keep seven daily copies of the whole
  server. Or copy your stories to your PC now and then with `scp -r`.

## Free instead: Oracle Cloud

Oracle Cloud's Always Free tier includes a server that runs the app at no cost. The catch: getting one can
take patience (free servers are often "out of capacity", and Oracle cut the free allowance in half in 2026
without announcing it), and Oracle takes back free servers that look idle, which this app usually does (see
step O1). If you'd rather it just works, Hetzner (above) is about €6 a month.

Make the SSH key first (step 1 above), then:

**O1. Sign up, and keep the server from being taken back.** At oracle.com/cloud/free, sign up; Oracle asks for
a card to check who you are, but Always Free resources aren't charged. Choose your **home region** with care:
it can't be changed, and the free ARM servers are only made there (a big region near you has more of them).

Oracle reclaims an Always Free server that looks idle for 7 days (CPU, network and, on ARM, memory all under
20%), and this app is idle most of the time. Oracle has said servers on a **Pay As You Go** account aren't
reclaimed, and that you still pay nothing while you stay within the Always Free limits. So: **Billing → Upgrade
and manage payment → Pay As You Go**. Then set a safety net: **Billing → Budgets → Create budget**, $1 a month,
with an alert to your email, so you'd hear at once if anything ever cost money. (Check the terms Oracle shows
when you upgrade; they may have changed since.)

**O2. Create the server.** Compute → Instances → **Create instance**:

- **Image:** Canonical Ubuntu 24.04.
- **Shape:** Ampere → **VM.Standard.A1.Flex**, 1 OCPU and 6 GB of memory: plenty, and inside the free 2 OCPUs
  and 12 GB. If it says "out of capacity", try another availability domain, or again later. The AMD
  **VM.Standard.E2.1.Micro** (1 GB of memory, also free) is usually available, and should run the app too.
- **Networking:** create a new virtual cloud network with a public subnet, and keep **Assign a public IPv4
  address** on.
- **SSH keys:** **Paste public keys**, with the line from step 1.

**Create**, and write down the **Public IP address**. Below, `SERVER` means that address. If it shows only
"-", the server has no public address yet: **Networking** tab → the VNIC under **Attached VNICs** → **IP
administration** → **⋯** on the private IP → **Edit** → **Ephemeral public IP** → **Update**.

**O3. Install the app.** Sign in (the user is `ubuntu`, not root):

```powershell
ssh ubuntu@SERVER
```

The server has only 1 GB of memory if you took the Micro shape, so first give it 2 GB of swap (memory on disk
for when it runs short):

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
```

Then install:

```bash
curl -fsSL https://raw.githubusercontent.com/wowanirudh-boop/devils-workshop/main/deploy/setup-server.sh | sudo bash
```

At the end, open the Tailscale link it prints and sign in with the same account as your phone.

**O4. The firewall.** Nothing to do. Oracle's Ubuntu comes with firewall rules of its own, which let in only SSH
and are loaded at every boot; the setup keeps them and adds one rule that lets Tailscale in. Oracle's network
rules (its "security list") also let in only SSH from the internet, so the app's phone port stays closed to
everyone but your Tailscale devices. Leave both as they are.

**O5. Carry on from step 4 above** (Tailscale on your phone), with these differences:

- everywhere it says `root@SERVER`, use `ubuntu@SERVER`: for the tunnel in step 5,
  `ssh -L 8080:127.0.0.1:8080 ubuntu@SERVER`;
- when copying stories, put `sudo` in front of the server commands:
  `ssh ubuntu@SERVER "sudo mkdir -p /home/dw/stories"`, copy to `ubuntu@SERVER:/tmp/`, then
  `ssh ubuntu@SERVER "sudo mv /tmp/'My Story' /home/dw/stories/ && sudo chown -R dw:dw /home/dw/stories"`
  (the `ubuntu` user can't write in `/home/dw` itself);
- to update, run the install line from O3 again.
