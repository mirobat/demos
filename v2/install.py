import os

TEMPLATE = f"""
[Unit]
Description=AlpineInterface
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/demos/v2
ExecStart=/home/ec2-user/.local/bin/uv run main.py {cutset}  mirobat-share --password {PASS} --port {port}
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
for i, (lang, cutset) in enumerate(("en", "../cutset_eval_34080utt_10129NEs_31.3h_05_08_2025.jsonl.gz")):
    """
    sudo cp service2.txt /etc/systemd/system/alpine.service
    sudo systemctl daemon-reload
    sudo systemctl enable alpine
    sudo systemctl start alpine
    """
    port = 7862 + i # starting at a port that has not been used before
    with open(f"/etc/systemd/system/alpine{lang}.service", "w") as f:
        f.write(TEMPLATE.format(locals()))
    os.popen("sudo systemctl daemon-reload")
    os.popen(f"sudo systemctl enable alpine{lang}")
    os.popen(f"sudo systemctl start alpine{lang}")