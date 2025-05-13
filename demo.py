# /// script
# requires-python = ">=3.12"
# dependencies = ["gradio", "soundfile", "boto3", "loguru", "typer"]
# ///
# DO NOT MODIFY THE COMMENT ABOVE
# Usage: install uv from https://docs.astral.sh/uv/, then simply
# `uv run demo.py <cutset> <backup_bucket_name> <UI_password>`.
# No need to set up a virtual env etc
# For EC2 deployment, generate a self-signed key
# openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 3650 -nodes -subj "/C=XX/ST=StateName/L=CityName/O=CompanyName/OU=CompanySectionName/CN=CommonNameOrHostname"
# edit service.txt to set a password
# sudo cp service.txt /etc/systemd/system/gradio.service
# sudo systemctl daemon-reload
# sudo systemctl enable gradio
# sudo systemctl start gradio
# Then monitor the app:
# sudo systemctl status gradio
# or get logs
# sudo journalctl -u gradio -f

import gzip
import os
import random
import threading
from pathlib import Path
from time import sleep
from typing import Optional

import soundfile as sf
import typer
from loguru import logger

cli_app = typer.Typer()

# Constants
UTTERANCES = []
RECORDINGS_DIR = 'recordings'
RECORDED_LOG = 'recorded_utterances.txt'
ACTIVE_UTTERANCES_FILE = 'active_utterances.txt'

BACKUP_DIR = Path('labelled_audio')
# Add this to your constants at the top
METADATA_FILE = 'metadata.json'
metadata_lock = threading.Lock()  # Add this with your other locks

# Instructions text
INSTRUCTIONS = """
Welcome to the audio recording interface!

Instructions:
1. Enter your native language or accent type in the box below (only used to study whether models underperform for specific accents)
2. Read the sentence displayed below
3. Click the record button and speak clearly
4. Click stop when you're done
5. You can listen to your recording
6. If you're satisfied, click 'Save and Next', otherwise 'Record Again'
7. If you want to skip the current sentence, click 'Skip'
8. Reach out to mirobat@ or alexnls@ for support
"""

# Create directories if they don't exist
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Thread-safe locks
recorded_lock = threading.Lock()
active_lock = threading.Lock()

import gradio as gr
import os
import json
import hashlib


# Function to generate a unique user fingerprint
def generate_fingerprint(request: gr.Request) -> str:
    """Generate a consistent fingerprint for a user based on request data."""
    # Collect identifying information
    fingerprint_data = {
        "ip": request.client.host,
        "user_agent": request.headers.get("User-Agent", ""),
        "language": request.headers.get("Accept-Language", "")
    }

    # Create a hash of the data
    fingerprint_str = json.dumps(fingerprint_data, sort_keys=True)
    return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]


class BackupThread(threading.Thread):
    def __init__(self, bucket):
        super().__init__()
        self.bucket = bucket
        self.files = [ACTIVE_UTTERANCES_FILE, RECORDED_LOG, METADATA_FILE]
        self.directories = [RECORDINGS_DIR]

    def _backup(self):
        import boto3
        s3 = boto3.client('s3')

        # Upload individual files
        for file in self.files:
            if file and os.path.exists(file):
                logger.info(f'\tcopy {file} to {str(BACKUP_DIR / os.path.basename(file))} ')
                s3.upload_file(file, self.bucket,
                               str(BACKUP_DIR / os.path.basename(file)))

        # Upload directories recursively
        for directory in self.directories:
            if os.path.exists(directory):
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        local_path = os.path.join(root, file)
                        # Preserve directory structure in S3 key
                        s3_path = os.path.join(os.path.relpath(root), file)
                        logger.info(f'\tcopy {file} to {str(BACKUP_DIR / s3_path)} ')
                        s3.upload_file(local_path, self.bucket, str(BACKUP_DIR / s3_path))

    def run(self):
        while True:
            logger.info(f'backing up data to {self.bucket}')
            self._backup()
            sleep(30)


def load_cutset(cutset_path):
    logger.info(f'Cutset path is {cutset_path}')
    utterances = []
    with gzip.open(cutset_path, 'rt') as f:
        for line in f:
            utterance = json.loads(line)
            utterances.append(utterance)
    logger.info(f"Found {len(utterances)} utterances")

    global UTTERANCES
    UTTERANCES = utterances


def get_recorded_utterances():
    if not os.path.exists(RECORDED_LOG):
        return set()
    with open(RECORDED_LOG, 'r') as f:
        return set(line.strip() for line in f)


def get_active_utterances():
    if not os.path.exists(ACTIVE_UTTERANCES_FILE):
        return set()
    with open(ACTIVE_UTTERANCES_FILE, 'r') as f:
        return set(line.strip() for line in f)


def add_active_utterance(utterance_id):
    with active_lock:
        with open(ACTIVE_UTTERANCES_FILE, 'a') as f:
            f.write(f"{utterance_id}\n")


def remove_active_utterance(utterance_id):
    with active_lock:
        active = get_active_utterances()
        active.remove(utterance_id)
        with open(ACTIVE_UTTERANCES_FILE, 'w') as f:
            for uid in active:
                f.write(f"{uid}\n")


def get_next_utterance():
    recorded = get_recorded_utterances()
    active = get_active_utterances()

    available = [u for u in UTTERANCES
                 if u['id'] not in recorded
                 and u['id'] not in active]

    if not available:
        return None

    utterance = random.choice(available)
    add_active_utterance(utterance['id'])
    return utterance


def save_recording(audio, utterance_id, utterance_data, **extras):
    if audio is None:
        return False

    # First save the audio file
    filename = os.path.join(RECORDINGS_DIR, f"{utterance_id}.wav")
    sf.write(filename, audio[1], audio[0])

    # Then update metadata with thread safety
    update_metadata(utterance_id, utterance_data, **extras)

    with recorded_lock:
        with open(RECORDED_LOG, 'a') as f:
            f.write(f"{utterance_id}\n")

    remove_active_utterance(utterance_id)
    return True


class RecordingInterface:
    def __init__(self):
        self.current_utterance = None
        self.utterance_count = 0

    def get_text(self):
        if self.current_utterance is None:
            self.current_utterance = get_next_utterance()
            if self.current_utterance is None:
                return "No more utterances available."
        return self.current_utterance['supervisions'][0]['text']

    def skip(self):
        if self.current_utterance:
            remove_active_utterance(self.current_utterance['id'])
        self.current_utterance = get_next_utterance()
        if self.current_utterance is None:
            return "No more utterances available.", None, self.utterance_count
        return self.current_utterance['supervisions'][0]['text'], None, self.utterance_count

    def save_and_next(self, audio, accent, request: gr.Request):
        user = generate_fingerprint(request)
        logger.info(f'Saving a file from user {user}')
        if not accent:
            accent = ""
        if self.current_utterance and audio is not None:
            save_recording(audio, self.current_utterance['id'], self.current_utterance, accent=accent, user=user)
            self.utterance_count += 1
            self.current_utterance = get_next_utterance()
            if self.current_utterance is None:
                return "No more utterances available.", None, self.utterance_count
            return self.current_utterance['supervisions'][0]['text'], None, self.utterance_count
        return "Please record audio before saving.", None, self.utterance_count

    def record_again(self):
        if self.current_utterance is None:
            return "No current utterance.", None, self.utterance_count
        return self.current_utterance['supervisions'][0]['text'], None, self.utterance_count


def _load_metadata():
    try:
        with open(METADATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_metadata(metadata):
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)


def update_metadata(utterance_id, utterance_data, **extras):
    with metadata_lock:
        # Load current metadata
        metadata = _load_metadata()

        # Create metadata entry
        metadata[f"{utterance_id}.wav"] = {
            "sentence_text": utterance_data['supervisions'][0]['text'],
            "entity_text": utterance_data['supervisions'][0]['custom']['NE_text'],
            "entity_id": utterance_data['supervisions'][0]['custom']['NE_id'],
            "recording_id": utterance_id,
            "engine": "human",
            **extras
        }

        # Save updated metadata
        _save_metadata(metadata)


def get_static_file(name):
    with (Path('static') / name).open() as f:
        return f.read()


def get_app():
    interface = RecordingInterface()

    with gr.Blocks(css=get_static_file('demo.css')) as app:
        gr.Markdown(
            INSTRUCTIONS,
            elem_id="instruction-box"
        )

        accent = gr.Textbox(
            label="Your native language or accent type",
            placeholder="English",
            elem_id="accent"
        )

        gr.Markdown(
            "### Please read this sentence:",
            elem_id="sentence-label"
        )
        text = gr.Textbox(
            label="",
            value=interface.get_text,
            elem_id="sentence-text",
            container=False,  # Removes the container border
            scale=3  # Makes the textbox larger
        )

        audio = gr.Audio(
            sources=["microphone"],
            type="numpy",
            label="Record",
            interactive=True
        )

        with gr.Row():
            save_btn = gr.Button("Save and Next")
            again_btn = gr.Button("Cancel and Record Again")
            skip_btn = gr.Button("Skip")

        gr.Markdown(
            "### Recordings completed: ",
            elem_id="counter"
        )
        counter = gr.Number(value=0, label="", elem_id="counter")

        skip_btn.click(
            interface.skip,
            outputs=[text, audio, counter]
        )

        save_btn.click(
            interface.save_and_next,
            inputs=[audio, accent],
            outputs=[text, audio, counter]
        )

        again_btn.click(
            interface.record_again,
            outputs=[text, audio, counter]
        )
    return app


@cli_app.command()
def main(cutset_file: str, backup_bucket: str, password: Optional[str] = None):
    load_cutset(cutset_file)
    args = dict(server_name="0.0.0.0", server_port=7860, share=False)
    if password:
        # don't back up locally to avoid overwriting prod data
        BackupThread(backup_bucket).start()
        # use a password and an SSL cert
        auth = ('demo', password) if password else None
        get_app().launch(**args, auth=auth, ssl_keyfile='key.pem', ssl_certfile='cert.pem', ssl_verify=False)
    else:
        get_app().launch(**args)


if __name__ == "__main__":
    cli_app()
