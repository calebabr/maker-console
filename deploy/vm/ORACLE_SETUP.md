# Oracle Cloud Always Free VM (step by step)

Goal: a small always-on Ubuntu VM that runs the public order-book recorder. Cost: $0. Time: about 45 minutes.
Do this from a PC. Nothing here involves trading keys. Next: `HARDENING.md`, then `RECORDER_DEPLOY.md`.

## 0. Before you start
- Have ready: an email address, a phone that can receive a text, and a credit or debit card (Oracle uses it only to verify identity; Always Free resources are not charged. Stay on the free tier and do not click "Upgrade").
- Pick the home region carefully: it **cannot be changed later**. Choose a US East region (Ashburn). Free Arm capacity is sometimes full there, see section 4.

## 1. Sign up
1. Go to `https://www.oracle.com/cloud/free/` and click **Start for free**.
2. Enter country, name, email. Click **Verify my email**, open the email, click the link.
3. Set a password, a **Cloud Account Name** (any short name) and **Home Region** = **US East (Ashburn)**. Click **Continue**.
4. Enter address and phone, then the card. You may see a small temporary authorization hold; it is released.
5. Accept the agreement, click **Start my free trial**.
6. You should see "Setting up your account", then (up to 30 minutes) an email "Your Oracle Cloud account is ready". Sign in at `https://cloud.oracle.com` with the account name.
- If sign-up is rejected: try another card, a different browser without extensions, and do not use a VPN. Retry the next day.

## 2. Create the VM
1. Top-left menu (three lines) > **Compute** > **Instances** > **Create instance**.
2. **Name:** `recorder`. Compartment: leave default.
3. **Image and shape** > **Edit**:
   - **Image:** click **Change image** > **Canonical Ubuntu** > the newest **24.04** (or 22.04) LTS > **Select image**.
   - **Shape:** **Change shape** > **Ampere** > **VM.Standard.A1.Flex** > set **1 OCPU** and **6 GB** memory (small is plenty; the free allowance is 4 OCPU / 24 GB total). **Select shape**.
4. **Networking:** keep "Create new virtual cloud network", and keep **Assign a public IPv4 address** ticked.
5. **Add SSH keys:** choose **Generate a key pair for me**, click **Save private key** and **Save public key**. Put them in a folder only you use (for example `C:\Users\<you>\.ssh\`). Rename the private key `oracle-recorder.key`. Losing it means rebuilding the VM.
6. **Boot volume:** leave default (about 47 GB; the free allowance is 200 GB total).
7. Click **Create**. Wait until the status square turns green **RUNNING** (1-3 minutes).
- Error "Out of capacity for shape VM.Standard.A1.Flex": see section 4.

## 3. Connect
1. On the instance page copy the **Public IP address**.
2. In PowerShell on your PC (it has `ssh`):
   ```
   icacls "C:\Users\<you>\.ssh\oracle-recorder.key" /inheritance:r /grant:r "$($env:USERNAME):R"
   ssh -i "C:\Users\<you>\.ssh\oracle-recorder.key" ubuntu@<PUBLIC_IP>
   ```
3. Type `yes` to the fingerprint question. You should see a prompt like `ubuntu@recorder:~$`.
4. Run:
   ```
   sudo apt update && sudo apt -y upgrade
   sudo reboot
   ```
   Wait 1 minute, SSH in again. Continue with `HARDENING.md`.

## 4. Caveats
- **Capacity:** if creation fails with "Out of capacity", retry every few hours (US mornings work best), or pick a different **Availability domain** in the Placement section of the create form. Do not leave the free tier to get capacity.
- **Idle reclaim:** Oracle may reclaim Always Free instances that sit nearly idle for 7 days (CPU, network and memory all under about 20% at the 95th percentile). A recorder alone may look idle. Sign in to the console now and then and check the instance is RUNNING each week. If it is reclaimed, recreate it with these steps and redo `RECORDER_DEPLOY.md`; data you already copied home is safe.
- **Trial:** the account starts with a 30-day trial credit; after it ends, Always Free resources keep running. Never click **Upgrade to paid** unless you intend to.
- **Billing check:** Menu > **Billing & Cost Management** > **Cost analysis**; it should read $0.

## 5. Fallback: Google Cloud e2-micro
Use this only if Oracle sign-up or capacity fails.
1. `https://console.cloud.google.com` > create a project > **Compute Engine** > **VM instances** > **Create instance**.
2. Name `recorder`; region `us-east1`, `us-central1` or `us-west1` only (the free tier); machine type **e2-micro**; boot disk Ubuntu 24.04 LTS, **Standard persistent disk**, 30 GB max.
3. The external IPv4 address is billed (about $3.65/month) after the trial credit. If that is not acceptable, use Oracle.
4. Connect with the **SSH** button in the console, then continue with `HARDENING.md` (skip the Oracle security-list step; use the VPC firewall to allow only TCP 22).

## 6. Delete / rollback
Instance page > **More actions** > **Terminate** (tick "permanently delete the attached boot volume"). Nothing else is billed.
