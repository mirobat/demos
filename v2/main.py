# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3", "loguru", "fastapi", "python-multipart", "uvicorn", "typer"]
# ///
# DO NOT MODIFY THE COMMENT ABOVE
# Usage: install uv from https://docs.astral.sh/uv/, then simply
# `uv run demo.py <cutset> <backup_bucket_name> <UI_password>`.
# No need to set up a virtual env etc
# For EC2 deployment, generate a self-signed key
# openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 3650 -nodes -subj "/C=XX/ST=StateName/L=CityName/O=CompanyName/OU=CompanySectionName/CN=CommonNameOrHostname"
# edit service2.txt to set a password
# sudo cp service2.txt /etc/systemd/system/alpine.service
# sudo systemctl daemon-reload
# sudo systemctl enable alpine
# sudo systemctl start alpine
# Then monitor the app:
# sudo systemctl status alpine
# or get logs
# sudo journalctl -u alpine -f
# to deploy: ssh into machine, `git pull && sudo systemctl start alpine`
import gzip
import json
import os
import random
import secrets
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Optional

import typer
import uvicorn
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Request, status, Form
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from loguru import logger

# Constants
UTTERANCES = []
RECORDINGS_DIR = 'recordings'
ACTIVE_UTTERANCES_FILE = 'active_utterances.txt'
IS_LOCAL_DEV = False
BACKUP_DIR = Path('labelled_audio_v2')
METADATA_FILE = 'metadata.json'
metadata_lock = threading.Lock()

os.makedirs(RECORDINGS_DIR, exist_ok=True)

active_lock = threading.Lock()
app = FastAPI()

# Initialize HTTP Basic Auth
security = HTTPBasic()
PASSWORD = "demo"
INTERFACE: Optional['RecordingInterface'] = None


def get_username_from_request(request: Request) -> str:
    """Get username from X-Username header."""
    username = request.headers.get('X-Username')
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username required in X-Username header"
        )
    return username.strip()


class BackupThread(threading.Thread):
    def __init__(self, bucket):
        super().__init__()
        self.bucket = bucket
        self.files = [ACTIVE_UTTERANCES_FILE, METADATA_FILE]
        self.directories = [RECORDINGS_DIR]
        logger.info('Will back data up to %s', bucket)

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
            sleep(180)


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
    return set(load_metadata().keys())


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

    user = extras.get("user", "user")
    filename = os.path.join(RECORDINGS_DIR, f"{utterance_id}_{user}.wav")
    with open(filename, 'wb') as outfile:
        outfile.write(audio[0])

    # Then update metadata with thread safety
    update_metadata(utterance_id, utterance_data, **extras)

    remove_active_utterance(utterance_id)
    return True


class RecordingInterface:
    def __init__(self):
        self.current_utterance = defaultdict(dict)  # user ID -> dict containing a cutset utterance (itself a dict)
        self.utterance_count = defaultdict(int)  # user ID -> integer

        meta = load_metadata()
        for entry in meta.values():
            if 'user' in entry:
                self.utterance_count[entry['user']] += 1
        logger.info(f'Found metadata for {len(meta)} files from {len(self.utterance_count)} users')
        # clear active utterances on startup
        if os.path.exists(ACTIVE_UTTERANCES_FILE):
            with open(ACTIVE_UTTERANCES_FILE, 'w') as f:
                f.write('')

    def get_text(self, request: Request):
        user = get_username_from_request(request)
        if not self.current_utterance[user]:
            self.current_utterance[user] = get_next_utterance()
            if not self.current_utterance[user]:
                return "No more utterances available."

        txt = self.current_utterance[user]['supervisions'][0]['text']
        logger.info(f'Getting text {txt} for user {user}')
        return txt

    def get_recording_count(self, request: Request):
        user = get_username_from_request(request)
        return self.utterance_count[user]

    def _init(self, request: Request):
        return self.get_recording_count(request), self.get_text(request)

    def skip(self, request: Request):
        user = get_username_from_request(request)
        logger.info(f'Skipping for user {user}')
        if self.current_utterance[user]:
            remove_active_utterance(self.current_utterance[user]['id'])
        self.current_utterance[user] = get_next_utterance()
        if not self.current_utterance[user]:
            return "No more utterances available.", None, self.utterance_count[user]
        return self.current_utterance[user]['supervisions'][0]['text'], None, self.utterance_count[user]

    def save_and_next(self, audio, accent, request: Request):
        user = get_username_from_request(request)
        if audio is None:
            logger.info('skipping loop')
            return self.current_utterance[user]['supervisions'][0]['text'], None, self.utterance_count[user]
        logger.info(f'Saving a file from user {user}')
        if not accent:
            accent = ""
        if self.current_utterance[user] and audio is not None:
            now = datetime.now(timezone.utc).isoformat()
            save_recording(audio, self.current_utterance[user]['id'], self.current_utterance[user], accent=accent,
                           user=user, recorded_at=now)
            self.utterance_count[user] += 1
            self.current_utterance[user] = get_next_utterance()
            if not self.current_utterance[user]:
                return "No more utterances available.", None, self.utterance_count[user]
            return self.current_utterance[user]['supervisions'][0]['text'], None, self.utterance_count[user]
        logger.error(f"Got to a bad place, {user}, {self.current_utterance[user]}, {audio is None})")
        return "Please record audio before saving.", None, self.utterance_count[user]  # TODO should never hit here

    def record_again(self, request: Request):
        user = get_username_from_request(request)
        if not self.current_utterance[user]:
            return "No current utterance.", None, self.utterance_count[user]
        return self.current_utterance[user]['supervisions'][0]['text'], None, self.utterance_count[user]


def load_metadata():
    with metadata_lock:
        try:
            with open(METADATA_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def save_metadata(metadata):
    with metadata_lock:
        with open(METADATA_FILE, 'w') as f:
            json.dump(metadata, f, indent=2, sort_keys=True)


def update_metadata(utterance_id, utterance_data, **extras):
    metadata = load_metadata()
    with metadata_lock:
        # Create metadata entry
        metadata[f"{utterance_id}.wav"] = {
            "sentence_text": utterance_data['supervisions'][0]['text'],
            "entity_text": utterance_data['supervisions'][0]['custom']['NE_text'],
            "entity_id": utterance_data['supervisions'][0]['custom']['NE_id'],
            "recording_id": utterance_id,
            "engine": "human",
            **extras
        }

    save_metadata(metadata)


def main(cutset_file: str, backup_bucket: Optional[str] = None, password: Optional[str] = None):
    logger.info(f"Arguments are: {cutset_file=}, {backup_bucket=}, {password=}")
    load_cutset(cutset_file)
    global INTERFACE, PASSWORD, IS_LOCAL_DEV
    INTERFACE = RecordingInterface()
    PASSWORD = password
    IS_LOCAL_DEV = (password is None) or (backup_bucket is None)

    args = dict(host="0.0.0.0", port=7861)
    if not IS_LOCAL_DEV:
        # don't back up locally to avoid overwriting prod data
        BackupThread(backup_bucket).start()
        # use a password and an SSL cert
        uvicorn.run(app, **args, ssl_keyfile='key.pem', ssl_certfile='cert.pem')
    else:
        logger.info('Starting in local mode. Data will NOT be backed up to S3')
        uvicorn.run(app, **args)


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials."""
    if not IS_LOCAL_DEV:
        correct_username = secrets.compare_digest(credentials.username, PASSWORD)
        correct_password = secrets.compare_digest(credentials.password, PASSWORD)

        if not (correct_username and correct_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
    return credentials


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def read_root(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Serve the main HTML page."""
    return FileResponse("static/index.html")


@app.get("/get-sentence")
async def get_sentence(request: Request, skip: bool = False):
    get_username_from_request(request) # fail fast if not authenticated
    if skip:
        INTERFACE.skip(request)
    return {"sentence": INTERFACE.get_text(request), "count": str(INTERFACE.get_recording_count(request))}


@app.post("/upload-audio")
async def upload_audio(request: Request, audio: UploadFile = File(...),
                       sampleRate: Optional[str] = Form(None),
                       accent: Optional[str] = Form(None)
                       ):
    """Save the uploaded audio file."""
    try:
        get_username_from_request(request) # fail fast if not authenticated
        content = await audio.read()
        INTERFACE.save_and_next((content, int(sampleRate)), accent, request)
        return {"message": "Audio uploaded successfully"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


if __name__ == "__main__":
    typer.run(main)
