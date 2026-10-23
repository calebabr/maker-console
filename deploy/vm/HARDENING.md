# VM hardening checklist

Run once on the new VM, as `ubuntu`, right after `ORACLE_SETUP.md`. The VM holds no secrets and only makes outbound reads of public data, so the goal is "nothing listens except SSH".
Keep your current SSH window open until the test in step 2 passes, so you cannot lock yourself out.

1. **Automatic updates:**
   ```
   sudo apt -y install unattended-upgrades
   sudo dpkg-reconfigure -plow unattended-upgrades    # choose Yes
   ```
2. **Key-only SSH:**
   ```
   printf 'PasswordAuthentication no\nPermitRootLogin no\nKbdInteractiveAuthentication no\nMaxAuthTries 3\n' | sudo tee /etc/ssh/sshd_config.d/01-hardening.conf
   sudo sshd -t && sudo systemctl reload ssh
   ```
   Test from a SECOND window: `ssh -i ...oracle-recorder.key ubuntu@<PUBLIC_IP>` still works.
3. **Close everything but SSH at the Oracle level:** console > the instance > **Virtual cloud network** link > **Security Lists** > default list > **Ingress Rules**. Keep only: source `0.0.0.0/0`, TCP, destination port `22`. Delete any other rule (ICMP rules may stay). Egress: leave "all".
4. **Host firewall:** do NOT enable UFW or edit iptables on Oracle images; the preinstalled rules plus the security list from step 3 are the firewall. Check from your PC that only SSH answers: `Test-NetConnection <PUBLIC_IP> -Port 80` should say `TcpTestSucceeded : False`.
4b. **Set a console-recovery password for `ubuntu`** (so the Oracle Console connection works if SSH breaks): on the VM run `sudo passwd ubuntu`, enter a long random password twice (use a password manager to generate and store it; never in the repo, a note in git, or chat). SSH stays key-only (step 2), so this password cannot be used over the network. Recovery path: console > instance > **Console connection** > **Launch Cloud Shell connection**, log in as `ubuntu` with that password.
5. **Dedicated non-root user** `recorder` (created in `RECORDER_DEPLOY.md`): no login shell, no sudo, writes only to `/var/lib/book-recorder`.
6. **No secrets on the box.** The recorder needs no keys. Do not put exchange keys, tokens or passwords on this VM.
7. **Time sync:** `timedatectl` should show `System clock synchronized: yes`. If not: `sudo timedatectl set-ntp true`.
8. **fail2ban (optional):** `sudo apt -y install fail2ban`; check `sudo fail2ban-client status sshd`.
9. **Backups:** the data is public but gaps are permanent. Copy the data directory home weekly (command in `RECORDER_DEPLOY.md`). Optional VM backup: console > Boot volumes > **Create manual backup**.
10. **Monitoring that the timer ran:** weekly, `systemctl list-timers kalshi-book-recorder.timer` (LAST within 5 minutes) and `ls -lt /var/lib/book-recorder | head` (newest file is this hour). Optional free heartbeat at `https://healthchecks.io`: add `ExecStartPost=/usr/bin/curl -fsS -m 10 <YOUR_HEALTHCHECK_URL>` to the installed unit in `/etc/systemd/system/` only. The URL is a token: never put it in git.
11. **Rollback:** if a step breaks access, use the console **Console connection** (needs the step 4b password) to edit the file back, or terminate and recreate the VM (nothing is lost that is not already copied home).
