# ElevenLabs Google Sheets Audio Automation

This project automates the process of generating audio files from text using the [ElevenLabs Text-to-Speech API](https://elevenlabs.io/) and uploads the resulting audio files to Google Drive, updating a Google Sheet with the shareable audio links. It is designed for large-scale batch processing (10,000+ rows) and is robust against network and SSL issues, supporting checkpointing and resume.

---

## Features

- **Batch Text-to-Speech**: Converts each row of text in a Google Sheet to audio using ElevenLabs.
- **Google Drive Upload**: Uploads generated audio files to Google Drive using a service account.
- **Sheet Link Update**: Automatically writes the shareable audio link back to the Google Sheet.
- **Concurrency**: Supports concurrent processing with configurable worker and API concurrency limits.
- **Checkpoint/Resume**: Progress is saved after each row and batch, allowing safe interruption and resume.
- **Robustness**: Uses `curl` for both ElevenLabs and Google Drive to avoid SSL issues on macOS.
- **Error Handling**: Handles API rate limits, network errors, and partial failures gracefully.
- **No Header Required**: Works with Google Sheets that do not have a header row.

---

## Requirements

- Python 3.8+
- `curl` installed and available in your system path
- ElevenLabs API key (Business plan recommended for high concurrency)
- Google Cloud service account with Drive API enabled
- Google Sheet with text data (no header required)

### Python Dependencies

Install with:

```bash
pip install -r requirements.txt
```

**requirements.txt** (example):

```
gspread
google-auth
google-auth-oauthlib
google-api-python-client
python-dotenv
```

---

## Setup

### 1. Enable Google APIs

- Enable **Google Drive API** and **Google Sheets API** in your Google Cloud project.
- Create a **service account** and download the `credentials.json` file.
- Share your target Google Drive folder (if needed) with the service account email.

### 2. Environment Variables

Create a `.env` file in the project root:

```
ELEVENLABS_API_KEY="your_elevenlabs_api_key"
sheet_id="your_google_sheet_id"
```

### 3. Prepare Your Google Sheet

- Place your sentences in column A (or any column you specify).
- The script will write audio links to column B (or any column you specify).
- No header row is required.

---

## Usage

```bash
python app.py
```

You will be prompted for:
- Google Sheet name (default: Sheet1)
- Column with sentences (default: A)
- Column for audio links (default: B)
- Number of threads to use (recommended: 5, max: your ElevenLabs plan limit)

**Features:**
- Progress is saved after each row and batch in `checkpoint.pkl`.
- If interrupted, you can resume from the last checkpoint.
- All temporary files are managed in `~/temp_elevenlabs`.

---

## How It Works

1. **Reads all rows** from the specified Google Sheet column.
2. **Generates audio** for each sentence using ElevenLabs (via `curl` for SSL reliability).
3. **Uploads audio** to Google Drive (via `curl` and service account token).
4. **Updates the sheet** with the shareable audio link.
5. **Handles errors** and retries automatically.
6. **Saves progress** so you can safely stop and resume processing.

---

## Troubleshooting

- **SSL Errors**: This script uses `curl` for all network operations to avoid Python SSL issues on macOS.
- **API Limits**: Set `TTS_CONCURRENCY_LIMIT` in `app.py` to match your ElevenLabs plan.
- **Google Drive Permissions**: Ensure your service account has access to the target Drive folder.
- **Large Sheets**: For 10,000+ rows, processing may take several hours. You can safely stop and resume.

---

## Example: Resume After Interruption

If the script is interrupted, simply run it again and choose to resume from the checkpoint when prompted.

---

## License

MIT License

---

## Author

Shailesh (add your contact or GitHub link here)

---

## Show on Resume

**Project:** ElevenLabs Google Sheets Audio Automation  
**Description:** Automated batch TTS audio generation and Google Drive upload for 10,000+ Google Sheet rows, with robust error handling, checkpointing, and concurrency, using Python, Google APIs, and ElevenLabs API.
