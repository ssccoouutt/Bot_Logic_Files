#!/usr/bin/env python3
"""
WhatsApp Save Handler - COMPLETE SESSION SAVING
- Creates FULL zip of Chrome profile (includes all session data)
- Correctly handles the directory structure so it matches what main script expects
- Downloads token.json from Google Drive using token URL from main script
- Uploads session zip to Google Drive
- Returns shareable link
"""

import os
import time
import logging
import zipfile
import shutil
import json
import tempfile
from datetime import datetime, timezone
import requests
import traceback

# Configure logging
logger = logging.getLogger(__name__)

# ======================== GOOGLE DRIVE FUNCTIONS ========================

def download_token_file(token_file_url):
    """Download token.json from Google Drive"""
    try:
        if not token_file_url:
            logger.error("Token file URL is None or empty")
            return None
            
        logger.debug(f"Downloading token.json from: {token_file_url}")
        token_response = requests.get(token_file_url, stream=True)
        token_response.raise_for_status()
        token_filename = "token.json"
        with open(token_filename, 'wb') as f:
            for chunk in token_response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        logger.debug(f"Successfully downloaded token file: {token_filename}")
        return token_filename
    except Exception as e:
        logger.error(f"Failed to download token file: {e}")
        return None

def refresh_google_token(token_data):
    """Refresh expired Google token"""
    try:
        logger.debug("Token expired. Refreshing...")
        refresh_data = {
            'client_id': token_data['client_id'],
            'client_secret': token_data['client_secret'],
            'refresh_token': token_data['refresh_token'],
            'grant_type': 'refresh_token'
        }
        refresh_response = requests.post(token_data['token_uri'], data=refresh_data)
        refresh_response.raise_for_status()
        new_token = refresh_response.json()
        token_data['token'] = new_token['access_token']
        token_data['expiry'] = datetime.now(timezone.utc).isoformat()
        logger.debug("Token refreshed successfully")
        return token_data
    except Exception as e:
        logger.error(f"Failed to refresh token: {e}")
        return None

def create_session_zip(profile_dir):
    """Create a FULL zip file of the Chrome profile with correct structure"""
    try:
        if not os.path.exists(profile_dir):
            logger.error(f"Profile directory {profile_dir} does not exist")
            return None

        zip_path = os.path.join(tempfile.gettempdir(), "whatsapp_session.zip")
        logger.info(f"Creating session zip at {zip_path} from {profile_dir}")

        # Count files for progress tracking
        total_files = 0
        for root, dirs, files in os.walk(profile_dir):
            total_files += len(files)
        
        logger.info(f"Found {total_files} files to compress")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # We must include EVERYTHING in the profile_dir.
            # The main script expects the zip to contain a 'chrome_profile/' folder.
            files_added = 0
            skipped_files = 0
            
            for root, dirs, files in os.walk(profile_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    # Skip lock files as they can be locked by the browser process
                    if file.endswith('.lock') or file == "SingletonLock":
                        skipped_files += 1
                        continue
                    
                    try:
                        # Get path relative to the profile_dir (e.g., 'Default/Cookies')
                        rel_path = os.path.relpath(file_path, profile_dir)
                        # Add to zip under 'chrome_profile/' folder
                        zipf.write(file_path, os.path.join("chrome_profile", rel_path))
                        files_added += 1
                        
                        # Log progress every 100 files
                        if files_added % 100 == 0:
                            logger.info(f"Added {files_added}/{total_files} files to zip")
                            
                    except (PermissionError, OSError) as e:
                        # File might be locked by Chrome, try to copy it
                        try:
                            # Try to read the file content
                            with open(file_path, 'rb') as f:
                                content = f.read()
                            # Add to zip using the content
                            zipf.writestr(os.path.join("chrome_profile", rel_path), content)
                            files_added += 1
                            logger.debug(f"Added locked file via content copy: {rel_path}")
                        except Exception as e2:
                            skipped_files += 1
                            logger.debug(f"Skipped file {rel_path}: {e2}")
                    except Exception as e:
                        skipped_files += 1
                        logger.debug(f"Unexpected error with {file_path}: {e}")

        if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
            file_size = os.path.getsize(zip_path)
            logger.info(f"Successfully created zip: {file_size} bytes")
            logger.info(f"Added {files_added} files, skipped {skipped_files} files")
            
            # If we added very few files compared to total, something might be wrong
            if files_added < total_files * 0.5:  # Less than 50% added
                logger.warning(f"Only added {files_added}/{total_files} files. Session may be incomplete.")
            
            return zip_path
        return None
    except Exception as e:
        logger.error(f"Failed to create session zip: {e}")
        return None

def upload_file_to_drive(file_path, filename, token_data):
    """Upload file to Google Drive using token"""
    try:
        upload_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
        headers = {"Authorization": f"Bearer {token_data['token']}"}
        metadata = {"name": filename, "parents": ["root"]}
        with open(file_path, 'rb') as file_content:
            files = {
                "data": ("metadata", json.dumps(metadata), "application/json"),
                "file": (filename, file_content)
            }
            upload_response = requests.post(upload_url, headers=headers, files=files)
        upload_response.raise_for_status()
        return upload_response.json()
    except Exception as e:
        logger.error(f"Failed to upload to Drive: {e}")
        return None

# ======================== MAIN FUNCTION ========================

async def run(update, context, driver):
    """Main function called from main bot"""
    try:
        user_id = update.effective_user.id
        profile_path = context.user_data.get('profile_path')
        token_file_url = context.user_data.get('token_file_url')
        
        if not profile_path:
            await update.message.reply_text("❌ Profile path not found in context")
            return
        
        if not token_file_url:
            await update.message.reply_text("❌ Token file URL not found in context")
            return
        
        status_msg = await update.message.reply_text("💾 Saving COMPLETE session to Google Drive...")
        
        # Check if profile directory exists and has content
        if not os.path.exists(profile_path):
            await status_msg.edit_text("❌ Profile directory doesn't exist yet. Please login first or use the bot to create a session.")
            return
        
        # Count files before zipping
        total_files = sum([len(files) for r, d, files in os.walk(profile_path)])
        await status_msg.edit_text(f"📊 Found {total_files} files in profile. Creating archive...")
        
        # Create zip (will try to save everything possible)
        zip_path = create_session_zip(profile_path)
        if not zip_path:
            await status_msg.edit_text("❌ Failed to create session archive. The profile directory might be empty or inaccessible.")
            return
        
        file_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        await status_msg.edit_text(f"📦 Archive created ({file_size_mb:.2f} MB). Uploading to Google Drive...")
        
        # Step 2: Handle token and upload
        token_filename = download_token_file(token_file_url)
        if not token_filename:
            await status_msg.edit_text("❌ Failed to download token.json")
            return
            
        with open(token_filename, 'r') as f:
            token_data = json.load(f)
            
        # Refresh if needed
        expiry_date = datetime.fromisoformat(token_data['expiry'].replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > expiry_date:
            token_data = refresh_google_token(token_data)
            
        if not token_data:
            await status_msg.edit_text("❌ Token refresh failed")
            return

        filename = f"whatsapp_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        result = upload_file_to_drive(zip_path, filename, token_data)
        
        # Cleanup
        if os.path.exists(zip_path): 
            try:
                os.remove(zip_path)
            except:
                pass
        if os.path.exists(token_filename): 
            try:
                os.remove(token_filename)
            except:
                pass
        
        if result:
            # Fix: Use HTML parse mode instead of Markdown to avoid parsing issues
            await status_msg.edit_text(
                f"✅ <b>COMPLETE Session Saved!</b>\n\n"
                f"📁 File: <code>{filename}</code>\n"
                f"📦 Size: {file_size_mb:.2f} MB\n"
                f"📊 Files: {total_files}\n\n"
                f"🔗 <a href='https://drive.google.com/file/d/{result['id']}/view'>View on Drive</a>\n"
                f"📥 <a href='https://drive.google.com/uc?export=download&id={result['id']}'>Download URL</a>\n\n"
                f"ℹ️ Update your SESSION_URL in main script with this download URL to persist login.",
                parse_mode='HTML'
            )
        else:
            await status_msg.edit_text("❌ Upload failed")
            
    except Exception as e:
        logger.error(f"Error in save command: {e}")
        logger.error(traceback.format_exc())
        await update.message.reply_text(f"❌ Error: {str(e)}")