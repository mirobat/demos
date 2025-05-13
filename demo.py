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

import gradio as gr
import gzip
import json
import json
import os
import random
import soundfile as sf
import threading
import typer
from loguru import logger
from pathlib import Path
from time import sleep
from typing import Optional

cli_app = typer.Typer()

# Constants
CUTSET_PATH = None
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
1. Read the sentence displayed below
2. Click the record button and speak clearly
3. Click stop when you're done
4. You can listen to your recording
5. If you're satisfied, click 'Save and Next', otherwise 'Record Again'
6. If you want to skip the current sentence, click 'Skip'
7. Reach out to mirobat@ or alexnls@ for support
"""

# Create directories if they don't exist
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Thread-safe locks
recorded_lock = threading.Lock()
active_lock = threading.Lock()


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


def load_cutset():
    logger.info(f'Cutset path is {CUTSET_PATH}')
    utterances = []
    with gzip.open(CUTSET_PATH, 'rt') as f:
        for line in f:
            utterance = json.loads(line)
            utterances.append(utterance)
    logger.info(f"Found {len(utterances)} utterances")
    return utterances


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
    utterances = load_cutset()
    recorded = get_recorded_utterances()
    active = get_active_utterances()

    available = [u for u in utterances
                 if u['id'] not in recorded
                 and u['id'] not in active]

    if not available:
        return None

    utterance = random.choice(available)
    add_active_utterance(utterance['id'])
    return utterance


def save_recording(audio, utterance_id, utterance_data):
    if audio is None:
        return False

    # First save the audio file
    filename = os.path.join(RECORDINGS_DIR, f"{utterance_id}.wav")
    sf.write(filename, audio[1], audio[0])

    # Then update metadata with thread safety
    update_metadata(utterance_id, utterance_data)

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

    def save_and_next(self, audio):
        if self.current_utterance and audio is not None:
            save_recording(audio, self.current_utterance['id'], self.current_utterance)
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

    def cancel(self):
        return None


def _load_metadata():
    try:
        with open(METADATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_metadata(metadata):
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)


def update_metadata(utterance_id, utterance_data):
    with metadata_lock:
        # Load current metadata
        metadata = _load_metadata()

        # Create metadata entry
        metadata[f"{utterance_id}.wav"] = {
            "sentence_text": utterance_data['supervisions'][0]['text'],
            "entity_text": utterance_data['supervisions'][0]['custom']['NE_text'],
            "entity_id": utterance_data['supervisions'][0]['custom']['NE_id'],
            "recording_id": utterance_id,
            "engine": "human"
        }

        # Save updated metadata
        _save_metadata(metadata)


def get_app():
    interface = RecordingInterface()

    with gr.Blocks(css="""
        #instruction-box { margin-bottom: 20px; }
        #sentence-label { font-size: 24px; font-weight: bold; }
        #sentence-text { 
            font-size: 48px !important; 
            line-height: 1.5;
            padding: 30px;
            min-height: 150px;
            background-color: #f8f9fa;
            border-radius: 10px;
        }
        #counter { font-size: 18px; font-weight: bold; color: #2196F3; }
    """) as app:
        gr.Markdown(
            INSTRUCTIONS,
            elem_id="instruction-box"
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
            inputs=[audio],
            outputs=[text, audio, counter]
        )

        again_btn.click(
            interface.record_again,
            outputs=[text, audio, counter]
        )
    return app


@cli_app.command()
def main(cutset_file: str, backup_bucket: str, password: Optional[str] = None):
    global CUTSET_PATH
    CUTSET_PATH = cutset_file
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
