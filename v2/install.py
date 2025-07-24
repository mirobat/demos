import os

TEMPLATE = """
[Unit]
Description=AlpineInterface
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/demos/v2
ExecStart=/home/ec2-user/.local/bin/uv run main.py {cutset} --backup-bucket mirobat-share --password {PASS} --port {port}
Restart=on-failure
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=alpine{lang}
Environment="PATH=/usr/bin:/usr/local/bin:/home/ec2-user/.local/bin/uv"

[Install]
WantedBy=multi-user.target
"""

PASS="demo"
for i, (lang, cutset) in enumerate([
    ("en", "output_cutset_en_6000_07112025.jsonl"),
    ("demo", "demo_cutset.jsonl"), # ignore the data here, it's just for showing the tool
]):
    port = 7862 + i # starting at a port that has not been used before
    print(f'installing cutset {cutset} on port {port}')
    content = TEMPLATE.format(**locals())
    cutset = os.path.abspath(cutset)
    with open(f"/etc/systemd/system/alpine{lang}.service", "w") as f:
        f.write(content)
    os.popen("sudo systemctl daemon-reload")
    os.popen(f"sudo systemctl enable alpine{lang}")
    os.popen(f"sudo systemctl start alpine{lang}")
    os.popen(f"sudo systemctl restart alpine{lang}")