# Audio Recording Demo

Web application for collecting audio recordings from utterance cutsets with automatic S3 backup.

## Features

- Web-based audio recording interface
- User fingerprinting for session management
- Automatic S3 backup every 3 minutes
- Thread-safe metadata handling
- SSL support for production
- HTTP Basic Auth protection

## Architecture

- FastAPI backend with static file serving
- Gzipped JSON cutset input format
- WAV audio output with metadata tracking
- Concurrent user support with active utterance locking

## Requirements

- Python 3.12+ (managed via `uv`)
- EC2 instance with a static IP
- S3 bucket for data backups

## Quick Start

Install Python in whatever way you see fit- we recommend `uv`. Start the app

```
uv run v2/main.py <cutset_file>
```

This will serve the main interface, but the recordings will not be backed to to S3. For production use you want to enable S3 backups and protect the interface with a password

```
uv run v2/main.py <cutset_file> <bucket_name> --password <some_password>
```

The app is ready for recording. Instructions for users are at the top of the page. As users record, you should see the following files appear on disk:

1. `active_utterances.txt`- list of utterance IDs that users are currently looking at
2. `metadata.json` - data about each recording, containing info such as what the transcript shown on screen was, when the recording was taken, a user hash, etc
3. `recordings/*wav` - the actual recordings

If an S3 bucket name and a password have been provided, these files will be backed up every few minutes to `s3://<your-bucket>/labelled_audio_v2`

## Details
The app has a thin backend with 2 HTTP endpoint:

1. `/get-sentence` returns the text of the current utterance that the user needs to read (eg if they've reloaded the page), or the next utterance if they've clicked skip
2. `/upload-audio` saves the recorded audio that was provided by the user's browser. This is associated with the user's currently active utterance

There are no user accounts. The identity of each user is determined by fingerprinting their browser, using request headers such as IP, user agent and browser language.

The frontend is using Alpine.js to drive a simple state machine, either
- load page, get utterance, record audio, send to backend
- load page, get utterance, skip N times, record audio, send to backend
The actual audio recording is handled by Recorder.js 


## EC2 deployment
Launch an EC2 instance. The app is pretty light so a `t3.small` (2 vCPUs/2 GB RAM) instance is just fine. If you expect more than 10 concurrent users get a `t3.large` just to be safe. Follow [this doc] to allocate a public static IP to your instance. Make sure the instance role has permission to write into the S3 bucket you provide below.

Now connect to the instance via SSH and do the initial setup:

```bash
# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh
```

1. Clone this repository onto the instance

1. Generate a self-signed SSL certificate:
```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 3650 -nodes -subj "/C=XX/ST=StateName/L=CityName/O=CompanyName/OU=CompanySectionName/CN=CommonNameOrHostname"
```

1. Copy a dataset to the instance. This should be a `lhotset` [CutSet](https://lhotse.readthedocs.io/en/latest/api.html#lhotse.cut.CutSet), JSON serialized, one cut per line, gzip compressed. Here is what one line should look like after decompression:

```
{"id": "entity_N0_utterance_0", "start": 0.0, "duration": 3.2, "channel": 0, "supervisions": [{"id": "sup_entity_N0_utterance_0", "recording_id": "entity_N0_utterance_0", "start": 0.0, "duration": 3.2, "channel": 0, "text": "The latest 3M Filtrete model includes antimicrobial protection.", "custom": {"NE_text": "3M Filtrete", "NE_id": "N0", "raw_text": "The latest 3M Filtrete model includes antimicrobial protection.", "labels": ["synthetic", "16kHz"], "ignored": false}}], "type": "MonoCut"}
```

1. Configure systemd service that restarts the app in case it crashes:
```bash
# edit install.py and set a password and add datasets. Change the paths to uv and main.py if necessary
sudo uv run install.py
```

1. The app should be running on port 7861. Navigate to `<YOUR INSTANCE IP>:7861` to check.


3. Monitor:
```bash
sudo systemctl status alpine # get status
sudo journalctl -u alpine -f # get logs
```


## Legal approval

https://issues.amazon.com/issues/DMOPS-63225