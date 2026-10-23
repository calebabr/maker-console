# Book recorder: deploy on the VM

Records public order-book snapshots, keyless and GET-only, into hourly gzip files. No trading, no secrets, no database. Needs a hardened VM (`ORACLE_SETUP.md`, `HARDENING.md`).
The script is `scripts/book_recorder.py`. **The exact flags are in `python3 scripts/book_recorder.py --help`; if they differ from this page, the script wins** (edit the `ExecStart` line to match). The series list is a text file you copy by hand; it is never committed to git.

## 1. Packages and user (on the VM, as `ubuntu`)
```
sudo apt update && sudo apt -y install git python3
sudo useradd --system --home /opt/book-recorder --shell /usr/sbin/nologin recorder
sudo mkdir -p /var/lib/book-recorder /etc/book-recorder
```

## 2. Get the code (the PUBLIC repo)
```
sudo git clone https://github.com/<OWNER>/<PUBLIC_REPO>.git /opt/book-recorder
sudo chown -R root:root /opt/book-recorder
python3 /opt/book-recorder/scripts/book_recorder.py --help
```
You should see a usage message listing the options. The script uses only the Python standard library, so nothing needs `pip install`.

## 3. Series list (by hand)
The list of series to record is given to you separately (one identifier per line). On the VM:
```
sudo nano /etc/book-recorder/series.txt     # paste the lines, Ctrl+O, Enter, Ctrl+X
sudo chown root:recorder /etc/book-recorder/series.txt
sudo chmod 0640 /etc/book-recorder/series.txt
sudo chown recorder:recorder /var/lib/book-recorder
sudo chmod 0750 /var/lib/book-recorder
```
Never commit this file or paste it into the repo or an issue.

## 4. Test one pass by hand
```
sudo -u recorder python3 /opt/book-recorder/scripts/book_recorder.py --once --series-file /etc/book-recorder/series.txt --out /var/lib/book-recorder
echo $?
ls -la /var/lib/book-recorder
```
Expected: `0` printed, and a new `.jsonl.gz` file for the current hour. Peek: `sudo zcat /var/lib/book-recorder/*.jsonl.gz | head -c 600` shows JSON lines. If it reports 429 or 5xx errors, wait a minute and retry.

## 5. Install the service and timer
```
sudo cp /opt/book-recorder/deploy/vm/kalshi-book-recorder.service /etc/systemd/system/
sudo cp /opt/book-recorder/deploy/vm/kalshi-book-recorder.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-book-recorder.timer
```
The timer runs one pass every 5 minutes. To change it, edit `OnCalendar` in the timer (for example `*:0/10`), then `sudo systemctl daemon-reload && sudo systemctl restart kalshi-book-recorder.timer`. (If you prefer the script's own `--interval-min` loop, run it as a long-lived service instead; the timer approach is the default.)

## 6. Check it
```
systemctl list-timers kalshi-book-recorder.timer
systemctl status kalshi-book-recorder.service
journalctl -u kalshi-book-recorder.service -n 30 --no-pager
du -sh /var/lib/book-recorder
```
Expected after 10 minutes: NEXT within 5 minutes, LAST filled in, status `0/SUCCESS`, and a growing hourly file. The script enforces its own disk cap and rotation; confirm the cap in `--help`.

## 7. Copy data home (weekly, from PowerShell on your PC)
```
scp -i "C:\Users\<you>\.ssh\oracle-recorder.key" -r ubuntu@<PUBLIC_IP>:/var/lib/book-recorder C:\Users\<you>\recorder_backup
```
If `ubuntu` cannot read the folder: `sudo chmod -R g+rX /var/lib/book-recorder && sudo usermod -aG recorder ubuntu`, then log out and in.

## 8. Update, stop, roll back
- Update: `cd /opt/book-recorder && sudo git pull && sudo systemctl restart kalshi-book-recorder.timer`
- Roll back: `sudo git -C /opt/book-recorder checkout <PREVIOUS_COMMIT>`, then restart the timer.
- Stop recording: `sudo systemctl disable --now kalshi-book-recorder.timer`. This only reads public data and places no orders, so there is nothing else to stop.
- Remove: stop the timer, delete the two unit files from `/etc/systemd/system/`, `sudo systemctl daemon-reload`.

## Troubleshooting
- No new files: `journalctl -u kalshi-book-recorder.service -n 50`. Permission denied on write means the `chown recorder` in step 3 was missed.
- `status=226/NAMESPACE` or similar: the kernel rejects a sandbox option; comment out the most recently added option in the service and retry.
- Gaps in data: the VM was stopped or reclaimed; see the idle-reclaim note in `ORACLE_SETUP.md`.
