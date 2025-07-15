# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3", "loguru", "fastapi", "python-multipart", "uvicorn", "typer"]
# ///
# DO NOT MODIFY THE COMMENT ABOVE
# Usage: install uv from https://docs.astral.sh/uv/, then simply
# `uv run demo.py <cutset> <backup_bucket_name> <UI_password>`.
# No need to set up a virtual env etc
# For EC2 deployment, see a readme
import gzip
import json
import os
import random
import secrets
import threading
from collections import defaultdict
from dataclasses import dataclass
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
IS_LOCAL_DEV = False
# All file paths are deliberately pattern string and not valid paths to force the user to provide a %sroot dir
RECORDINGS_DIR = '%s/recordings'
ACTIVE_UTTERANCES_FILE = '%s/active_utterances.txt'
BACKUP_DIR = 'labelled_audio_v2/%s'
METADATA_FILE = '%s/metadata.json'
metadata_lock = threading.Lock()

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
        assert bucket
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
                target = str(BACKUP_DIR / os.path.basename(file))
                logger.info(f'\tcopy {file} to {target} ')
                s3.upload_file(file, self.bucket,
                               target)

        # Upload directories recursively
        for directory in self.directories:
            if os.path.exists(directory):
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        local_path = os.path.join(root, file)
                        # Preserve directory structure in S3 key
                        s3_path = os.path.join(os.path.relpath(root), file)
                        target = str(BACKUP_DIR / Path(*Path(s3_path).parts[1:]))
                        logger.info(f'\tcopy {file} to {target} ')
                        s3.upload_file(local_path, self.bucket, target)

    def run(self):
        while True:
            logger.info(f'backing up data to {self.bucket}')
            self._backup()
            sleep(180)


def open_file(filename):
    """
    Opens a file that could be either plain text or gzipped.
    Returns a file object that can be read.
    """
    try:
        f = gzip.open(filename, 'rt')  # 'rt' for text mode
        # Try reading a small bit to verify it's actually gzipped
        f.read(1)
        f.seek(0)  # Reset to beginning of file
        return f
    except gzip.BadGzipFile:  # Not a gzip file
        # Close the failed gzip attempt and open as regular file
        f.close()
        return open(filename, 'r')


def load_cutset(cutset_path):
    logger.info(f'Cutset path is {cutset_path}')
    utterances = []
    with open_file(cutset_path) as f:
        for line in f:
            utterance = json.loads(line)
            utterances.append(utterance)
    logger.info(f"Found {len(utterances)} utterances")

    global UTTERANCES
    UTTERANCES = utterances


def get_recorded_utterances():
    return set(line.replace('.wav', '') for line in load_metadata().keys())


def get_active_utterances():
    if not os.path.exists(ACTIVE_UTTERANCES_FILE):
        return set()
    with open(ACTIVE_UTTERANCES_FILE, 'r') as f:
        return set(line.strip().replace('.wav', '') for line in f)


def add_active_utterance(utterance_id):
    with active_lock:
        with open(ACTIVE_UTTERANCES_FILE, 'a') as f:
            f.write(f"{utterance_id}\n")


def remove_active_utterance(utterance_id):
    with active_lock:
        active = get_active_utterances()
        if utterance_id in active:
            active.remove(utterance_id)
        with open(ACTIVE_UTTERANCES_FILE, 'w') as f:
            for uid in active:
                f.write(f"{uid}\n")


def get_next_utterance(user) -> Optional[dict]:
    recorded = get_recorded_utterances()
    active = get_active_utterances()

    # available = not already recorded (by anyone) and not currently being shown to anyone else
    available = [u for u in UTTERANCES if u['id'] not in recorded and u['id'] not in active]

    if not available:
        return None

    metadata = load_metadata()
    # there can be multiple utterances for the same entity- make sure this user sees this entity once
    entities_for_this_user = {m['entity_id'] for m in metadata.values() if m.get('user') == user}
    available_to_this_user = [x for x in available if
                              x['supervisions'][0]['custom']['NE_id'] not in entities_for_this_user]

    if not available_to_this_user:
        # no more work left to do for this user
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

@dataclass
class NextUtterance:
    text: Optional[str]
    total_for_user: int
    is_nothing: bool = False
    is_error: bool = False

    @classmethod
    def none(cls, cnt):
        return NextUtterance(None, cnt, is_nothing=True)

    @classmethod
    def error(cls, cnt):
        return NextUtterance(None, cnt, is_error=True)


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
            self.current_utterance[user] = get_next_utterance(user)
            if not self.current_utterance[user]:
                return None

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
        next_ = get_next_utterance(user)
        if next_:
            if self.current_utterance[user]:
                remove_active_utterance(self.current_utterance[user]['id'])
            self.current_utterance[user] = next_

    def save_and_next(self, audio, accent, request: Request) -> NextUtterance:
        user = get_username_from_request(request)
        if audio is None:
            logger.info('skipping loop')
            return NextUtterance(self.current_utterance[user]['supervisions'][0]['text'], self.utterance_count[user])
        logger.info(f'Saving a file from user {user}')
        if not accent:
            accent = ""
        if self.current_utterance[user] and audio is not None:
            now = datetime.now(timezone.utc).isoformat()
            save_recording(audio, self.current_utterance[user]['id'], self.current_utterance[user], accent=accent,
                           user=user, recorded_at=now)
            self.utterance_count[user] += 1
            next_ = get_next_utterance(user)
            if not next_:
                # we're done recording
                del self.current_utterance[user]
                return NextUtterance.none(self.utterance_count[user])
            self.current_utterance[user] = next_
            return NextUtterance(self.current_utterance[user]['supervisions'][0]['text'], self.utterance_count[user])

        logger.error(f"Got to a bad place, {user}, {self.current_utterance[user]}, {audio is None})")
        return NextUtterance.error(self.utterance_count[user])


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


def main(cutset_file: str, backup_bucket: Optional[str] = None,
         password: Optional[str] = None,
         port: Optional[int] = 7861):
    logger.info(f"Arguments are: {cutset_file=}, {backup_bucket=}, {password=}")
    load_cutset(cutset_file)
    global INTERFACE, PASSWORD, IS_LOCAL_DEV, METADATA_FILE, RECORDINGS_DIR, ACTIVE_UTTERANCES_FILE, BACKUP_DIR
    PASSWORD = password
    IS_LOCAL_DEV = (password is None) or (backup_bucket is None)
    data_root_dir = os.path.basename(cutset_file).split(".")[0]
    RECORDINGS_DIR = RECORDINGS_DIR % data_root_dir
    ACTIVE_UTTERANCES_FILE = ACTIVE_UTTERANCES_FILE % data_root_dir
    BACKUP_DIR = Path(BACKUP_DIR % data_root_dir)
    METADATA_FILE = METADATA_FILE % data_root_dir
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    INTERFACE = RecordingInterface()

    args = dict(host="0.0.0.0", port=port)
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
async def get_sentence(request: Request, skip: bool = False,
                       credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    get_username_from_request(request)  # fail fast if not authenticated
    try:
        if skip:
            INTERFACE.skip(request)
        sentence = INTERFACE.get_text(request)
        # codes: 0=success, 1=unexpected error, 2=you're done
        return {"sentence": sentence, "count": str(INTERFACE.get_recording_count(request)), "status": 0 if sentence else 2}
    except Exception as e:
        logger.error(f"Error in get_sentence: {e}")
        return {"sentence": None, "count": "0", "status": 1}


@app.post("/upload-audio")
async def upload_audio(request: Request, audio: UploadFile = File(...),
                       sampleRate: Optional[str] = Form(None),
                       accent: Optional[str] = Form(None),
                       credentials: HTTPBasicCredentials = Depends(verify_credentials)
                       ):
    """Save the uploaded audio file."""
    get_username_from_request(request)  # fail fast if not authenticated
    try:
        content = await audio.read()
        result = INTERFACE.save_and_next((content, int(sampleRate)), accent, request)
        if result.is_nothing:
            return {"status": 2}
        if result.is_error:
            return {"message": "Error", "status": 1}
        return {"status": 0}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": 1}


if __name__ == "__main__":
    typer.run(main)
