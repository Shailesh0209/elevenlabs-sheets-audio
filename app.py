import asyncio
import concurrent.futures
import os
import time
import uuid
import json
import subprocess
import pickle
from typing import List, Dict, Any
import sys

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import google.auth.transport.requests

# Load environment variables
load_dotenv()

# Configure 11Labs API
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Configure Google Sheets API
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Semaphore specifically for limiting concurrent ElevenLabs TTS calls
TTS_CONCURRENCY_LIMIT = 10
tts_semaphore = asyncio.Semaphore(TTS_CONCURRENCY_LIMIT)

# For storing completed rows to allow resume after failure
CHECKPOINT_FILE = "checkpoint.pkl"

# Create a clean temp directory
TEMP_DIR = os.path.join(os.path.expanduser('~'), 'temp_elevenlabs')
os.makedirs(TEMP_DIR, exist_ok=True)

def load_checkpoint():
    """Load checkpoint of completed rows if exists"""
    try:
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, 'rb') as f:
                return pickle.load(f)
    except Exception as e:
        print(f"Error loading checkpoint: {e}")
    return set()

def save_checkpoint(completed_rows):
    """Save checkpoint of completed rows"""
    try:
        with open(CHECKPOINT_FILE, 'wb') as f:
            pickle.dump(completed_rows, f)
    except Exception as e:
        print(f"Error saving checkpoint: {e}")

def generate_audio_with_curl(sentence: str, voice_id: str = "EXAVITQu4vr4xnSDxMaL") -> bytes:
    """Generate audio using curl subprocess to avoid SSL issues."""
    # Create unique filenames for this request
    payload_file = os.path.join(TEMP_DIR, f"payload_{uuid.uuid4().hex}.json")
    output_file = os.path.join(TEMP_DIR, f"audio_{uuid.uuid4().hex}.mp3")
    
    try:
        # Write payload to temp file
        with open(payload_file, 'w') as f:
            json.dump({"text": sentence, "model_id": "eleven_monolingual_v1"}, f)
        
        # Run curl command with less verbose output, more reliable options
        cmd = [
            'curl', '-s', '-X', 'POST',  # Silent mode, specify method
            f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}',
            '-H', f'xi-api-key: {ELEVENLABS_API_KEY}',
            '-H', 'Content-Type: application/json',
            '-d', f'@{payload_file}',
            '--output', output_file,
            '--max-time', '120',  # 2 minute timeout
            '--retry', '3',       # Built-in retry
            '--retry-delay', '2', # Wait 2 seconds between retries
            '--http1.1',          # Force HTTP/1.1 to avoid HTTP/2 framing errors
        ]
        
        # Execute command
        result = subprocess.run(cmd, check=True, capture_output=True)
        
        # Verify file exists and has content
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            with open(output_file, 'rb') as f:
                audio_content = f.read()
            
            # Clean up after successful read
            try:
                os.remove(payload_file)
                os.remove(output_file)
            except Exception:
                pass  # Best effort cleanup
                
            return audio_content
            
        print(f"Audio file missing or empty: {output_file}")
        return None
        
    except Exception as e:
        print(f"Error generating audio: {e}")
        # Clean up on error
        try:
            if os.path.exists(payload_file):
                os.remove(payload_file)
            if os.path.exists(output_file):
                os.remove(output_file)
        except Exception:
            pass  # Best effort cleanup
        return None

def get_access_token():
    """Get an OAuth2 access token for the service account."""
    credentials = service_account.Credentials.from_service_account_file(
        'credentials.json',
        scopes=['https://www.googleapis.com/auth/drive']
    )
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token

def upload_to_google_drive(audio_bytes, filename, drive_service=None):
    """Upload audio file to Google Drive using curl and service account token."""
    temp_file = os.path.join(TEMP_DIR, filename)
    try:
        # Write audio to disk
        with open(temp_file, 'wb') as f:
            f.write(audio_bytes)
        if os.path.getsize(temp_file) < 1000:
            print(f"Warning: Generated file is too small ({os.path.getsize(temp_file)} bytes)")
            return None

        # Get access token
        token = get_access_token()

        # Upload file using curl
        upload_cmd = [
            "curl", "-s", "-X", "POST",
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            "-H", f"Authorization: Bearer {token}",
            "-F", f"metadata={{\"name\":\"{filename}\",\"mimeType\":\"audio/mpeg\"}};type=application/json",
            "-F", f"file=@{temp_file};type=audio/mpeg"
        ]
        result = subprocess.run(upload_cmd, capture_output=True)
        if result.returncode != 0:
            print(f"Drive upload curl error: {result.stderr.decode()}")
            return None

        # Parse file ID from response
        try:
            resp_json = json.loads(result.stdout.decode())
            file_id = resp_json["id"]
        except Exception as e:
            print(f"Failed to parse Drive upload response: {e}")
            return None

        # Make file public using curl
        perm_cmd = [
            "curl", "-s", "-X", "POST",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-d", '{"role": "reader", "type": "anyone"}'
        ]
        perm_result = subprocess.run(perm_cmd, capture_output=True)
        if perm_result.returncode != 0:
            print(f"Drive permission curl error: {perm_result.stderr.decode()}")

        # Get webViewLink using curl
        meta_cmd = [
            "curl", "-s", "-X", "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=webViewLink",
            "-H", f"Authorization: Bearer {token}"
        ]
        meta_result = subprocess.run(meta_cmd, capture_output=True)
        if meta_result.returncode != 0:
            print(f"Drive meta curl error: {meta_result.stderr.decode()}")
            return None
        try:
            meta_json = json.loads(meta_result.stdout.decode())
            return meta_json.get("webViewLink")
        except Exception as e:
            print(f"Failed to parse Drive meta response: {e}")
            return None

    finally:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass

async def process_row(sentence, row_idx, audio_col, drive_service, 
                     executor, semaphore, batch_updates, completed_rows):
    """Process a single row with better error handling and resource management"""
    if row_idx in completed_rows:
        print(f"Skipping already processed row {row_idx}")
        return
        
    # Use outer semaphore for overall concurrency
    async with semaphore:
        try:
            # Generate unique filename
            filename = f"audio_{uuid.uuid4().hex}.mp3"
            
            # Use inner semaphore specifically for TTS API
            async with tts_semaphore:
                # Generate audio with curl (HTTP/1.1)
                audio_bytes = await asyncio.to_thread(
                    generate_audio_with_curl, sentence
                )
            
            if not audio_bytes or len(audio_bytes) < 1000:
                print(f"Skipping row {row_idx}: Failed to generate audio")
                return
            
            # Upload to Drive with safe synchronous API call
            link = await asyncio.to_thread(
                upload_to_google_drive, audio_bytes, filename, drive_service
            )
            
            if link:
                # Update sheet references
                cell = f"{audio_col}{row_idx}"
                batch_updates[cell] = link
                completed_rows.add(row_idx)
                save_checkpoint(completed_rows)  # Save after each success
                print(f"Processed row {row_idx}: {sentence[:30]}...")
            else:
                print(f"Failed to upload row {row_idx} audio")
            
        except Exception as e:
            print(f"Error processing row {row_idx}: {e}")
            # Don't terminate on individual row failures

async def process_batch(worksheet, text_col: str, audio_col: str, max_workers: int):
    """Process all sentences in batches with checkpoint/resume capability"""
    print("Fetching spreadsheet data...")
    all_values = worksheet.get_all_values()
    
    rows = all_values  # No header to skip
    total_rows = len(rows)
    print(f"Found {total_rows} rows to process (assuming no header)")
    
    # Load checkpoint of already processed rows
    completed_rows = load_checkpoint()
    
    # Find the index of the text column
    try:
        text_col_idx = ord(text_col.upper()) - 65  # Convert A->0, B->1, etc.
    except:
        print(f"Invalid column reference: {text_col}")
        return
    
    # Set up Google Drive service
    creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    
    # Process in batches
    batch_size = 20  # Smaller batches for better error recovery
    
    # Set up semaphore for overall concurrency
    semaphore = asyncio.Semaphore(max_workers)
    
    try:
        for batch_start in range(0, total_rows, batch_size):
            batch_end = min(batch_start + batch_size, total_rows)
            pending_count = sum(1 for i in range(batch_start + 1, batch_end + 1) if i not in completed_rows)
            
            print(f"\nProcessing batch {batch_start//batch_size + 1}: rows {batch_start+1} to {batch_end}")
            print(f"Pending items in batch: {pending_count}")
            
            if pending_count == 0:
                print("All rows in this batch already processed, skipping")
                continue
                
            batch_tasks = []
            batch_updates = {}
            
            for i, row in enumerate(rows[batch_start:batch_end], start=batch_start + 1):
                try:
                    # Skip already processed rows
                    if i in completed_rows:
                        continue
                        
                    sentence = row[text_col_idx]
                    if not sentence or len(sentence.strip()) == 0:
                        print(f"Skipping empty row {i}")
                        completed_rows.add(i)  # Mark empty rows as completed
                        continue
                    
                    batch_tasks.append(process_row(
                        sentence, i, audio_col, drive_service, None, 
                        semaphore, batch_updates, completed_rows
                    ))
                except IndexError:
                    print(f"No text found in row {i}, column {text_col}")
            
            if batch_tasks:
                # Process this batch and wait for completion
                await asyncio.gather(*batch_tasks)
                
                # Apply batch updates to the sheet if any
                if batch_updates:
                    try:
                        print(f"Updating {len(batch_updates)} cells")
                        entries = []
                        for cell, link in batch_updates.items():
                            entries.append({
                                'range': cell,
                                'values': [[link]]
                            })
                        
                        # Perform batch update with error handling
                        if entries:
                            worksheet.batch_update(entries)
                            print(f"Updated {len(entries)} cells")
                    except Exception as e:
                        print(f"Error updating cells: {e}")
                        # Individual cell fallback
                        try:
                            for cell, link in batch_updates.items():
                                worksheet.update(cell, link)
                            print("Updated cells individually as fallback")
                        except Exception as e2:
                            print(f"Individual updates also failed: {e2}")
                
            print(f"Completed batch {batch_start//batch_size + 1}")
            save_checkpoint(completed_rows)  # Save after each batch
    except KeyboardInterrupt:
        print("Process interrupted, saving checkpoint...")
        save_checkpoint(completed_rows)
        print(f"Checkpoint saved. Resume later to continue from row {max(completed_rows) if completed_rows else 0}")
        sys.exit(1)

async def main():
    # Get sheet_id from .env file, prompt if not available
    sheet_id = os.getenv("sheet_id")
    if not sheet_id:
        sheet_id = input("Enter Google Sheet ID: ")
    else:
        print(f"Using Google Sheet ID from .env: {sheet_id}")
    
    # Get remaining user inputs
    sheet_name = input("Enter Sheet name (default: Sheet1): ") or "Sheet1"
    text_col = input("Enter column with sentences (default: A): ") or "A"
    audio_col = input("Enter column for audio links (default: B): ") or "B"
    max_workers = int(input(f"Enter number of threads to use (recommended: 5): "))
    
    # Show checkpoint info
    if os.path.exists(CHECKPOINT_FILE):
        completed = len(load_checkpoint())
        print(f"Found checkpoint with {completed} completed rows")
        resume = input("Resume from checkpoint? (y/n): ").lower() == 'y'
        if not resume:
            os.remove(CHECKPOINT_FILE)
            print("Checkpoint cleared, starting fresh")
    
    start_time = time.time()
    print(f"Starting processing with {max_workers} threads")
    print(f"Using HTTP/1.1 curl for API calls to avoid connection issues")
    
    try:
        # Authenticate and open Google Sheet
        creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        
        print("Note: For large sheets, this process could take several hours.")
        print("Progress is saved after each row and batch for resume capability.")
        print("Press Ctrl+C at any time to safely interrupt processing.")
        
        # Process all sentences in the sheet
        await process_batch(worksheet, text_col, audio_col, max_workers)
        
        # Calculate elapsed time and print completion message
        elapsed_time = time.time() - start_time
        print(f"Processing completed in {elapsed_time:.2f} seconds")
        print(f"Audio files have been uploaded and links added to the sheet")
        
        # Clean up successful run
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
