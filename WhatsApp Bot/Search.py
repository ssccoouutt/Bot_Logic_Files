import os
import time
import re
import io
import json
import logging
import requests
import traceback
from datetime import datetime, timezone, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# ======================== CONFIGURATION ========================
GROUP_LINKS_FILE_ID = "1JsbQRBaKtAefA60P7aaOdjOc9Zi5etPQ"
GROUP_LINKS_FILENAME = "whatsapp_group_links.txt"
# New file for temporary new links (clear previous data each time)
TEMP_NEW_LINKS_FILE_ID = "1a2CMxij0K7ZcvZEsxCEKNwvDW5hHSGqH"
TEMP_NEW_LINKS_FILENAME = "new_whatsapp_links.txt"
logger = logging.getLogger(__name__)

# ======================== DRIVE UTILITIES ========================

def get_drive_service(token_file_url):
    """Authenticated Google Drive service using the token URL from context"""
    try:
        token_response = requests.get(token_file_url, stream=True)
        token_response.raise_for_status()
        token_data = token_response.json()
        
        # Check and Refresh Token if expired
        expiry_date = datetime.fromisoformat(token_data['expiry'].replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > expiry_date:
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
        
        creds = Credentials(
            token=token_data['token'],
            refresh_token=token_data['refresh_token'],
            token_uri=token_data['token_uri'],
            client_id=token_data['client_id'],
            client_secret=token_data['client_secret'],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        logger.error(f"Drive Auth Error: {e}")
        return None

def update_temp_new_links_file(service, links_list):
    """Update the temporary file with new links (clearing previous content)"""
    try:
        # Create temporary file with new links only
        with open(TEMP_NEW_LINKS_FILENAME, 'w') as f:
            for link in links_list:
                f.write(link + '\n')
        
        # Upload to Drive (this will replace the entire file content)
        media = MediaFileUpload(TEMP_NEW_LINKS_FILENAME, mimetype='text/plain', resumable=True)
        service.files().update(fileId=TEMP_NEW_LINKS_FILE_ID, media_body=media).execute()
        
        # Clean up local temp file
        if os.path.exists(TEMP_NEW_LINKS_FILENAME):
            os.remove(TEMP_NEW_LINKS_FILENAME)
            
        logger.info(f"Updated temp new links file with {len(links_list)} links")
        return True
    except Exception as e:
        logger.error(f"Error updating temp new links file: {e}")
        return False

async def take_screenshot(driver, update, prefix="screenshot"):
    """Take a screenshot and send it to the user"""
    try:
        # Create screenshots directory if it doesn't exist
        if not os.path.exists("screenshots"):
            os.makedirs("screenshots")
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshots/{prefix}_{timestamp}.png"
        
        # Take screenshot
        driver.save_screenshot(filename)
        
        # Send screenshot to user
        with open(filename, 'rb') as f:
            await update.message.reply_photo(
                photo=f, 
                caption=f"📸 {prefix.replace('_', ' ').title()}"
            )
        
        # Clean up local file
        os.remove(filename)
        return True
    except Exception as e:
        logger.error(f"Screenshot error: {e}")
        return False

# ======================== MAIN RUN FUNCTION ========================

async def run(update, context, driver):
    token_url = context.user_data.get('TOKEN_FILE_URL')
    num_results = 20
    if context.args:
        try:
            num_results = int(context.args[0])
        except ValueError:
            pass
    
    status_msg = await update.message.reply_text(f"🔍 Searching for {num_results} unique group links...")
    
    try:
        # 1. Access WhatsApp Search
        search_box_selectors = [
            '//div[@contenteditable="true"][@data-tab="3"]',
            '//div[@contenteditable="true"]'
        ]
        
        search_box = None
        for selector in search_box_selectors:
            try:
                search_box = driver.find_element(By.XPATH, selector)
                if search_box: break
            except: continue
            
        if not search_box:
            await status_msg.edit_text("❌ Could not find search box.")
            return

        search_box.click()
        time.sleep(1)
        search_box.send_keys(Keys.CONTROL + "a", Keys.BACKSPACE)
        search_box.send_keys("chat.whatsapp.com", Keys.ENTER)
        
        await status_msg.edit_text("🔍 Search query entered. Scanning results...")
        time.sleep(10) # Allow results to load
        
        # 2. Sync with Drive (Download current file to check for duplicates)
        service = get_drive_service(token_url)
        existing_links = set()
        all_existing_links_list = []  # Store as list to maintain order
        if service:
            try:
                request = service.files().get_media(fileId=GROUP_LINKS_FILE_ID)
                file_content = request.execute()
                with open(GROUP_LINKS_FILENAME, 'wb') as f:
                    f.write(file_content)
                with open(GROUP_LINKS_FILENAME, 'r') as f:
                    all_existing_links_list = [line.strip() for line in f.readlines() if line.strip()]
                    existing_links = set(all_existing_links_list)
            except:
                logger.warning("Starting fresh group links file.")

        # 3. Extraction Loop
        try:
            side_pane = driver.find_element(By.XPATH, '//div[@id="pane-side"]')
        except:
            await status_msg.edit_text("❌ Could not find results pane.")
            return

        # Take screenshot before starting scroll
        await take_screenshot(driver, update, "search_results_start")

        unique_found_this_run = set()
        new_links_to_append = []
        all_found_links = []  # Store all links found in search (including duplicates)
        attempts = 0
        
        while len(unique_found_this_run) < num_results and attempts < 30:
            page_source = driver.page_source
            found_links = re.findall(r'chat\.whatsapp\.com/[a-zA-Z0-9_-]+', page_source)
            
            for link in found_links:
                full_url = "https://" + link
                all_found_links.append(full_url)  # Store all found links
                
                if full_url not in existing_links and full_url not in unique_found_this_run:
                    unique_found_this_run.add(full_url)
                    new_links_to_append.append(full_url)
                    
                    # Append to local file immediately (End of file)
                    with open(GROUP_LINKS_FILENAME, 'a') as f:
                        f.write(full_url + '\n')
                        
                if len(unique_found_this_run) >= num_results: break
            
            if len(unique_found_this_run) >= num_results: break
            
            # Scroll down
            driver.execute_script("arguments[0].scrollTop += 800;", side_pane)
            time.sleep(3)
            attempts += 1

        # Take screenshot at end of scroll
        await take_screenshot(driver, update, "search_results_end")
        
        # Scroll back to top
        driver.execute_script("arguments[0].scrollTop = 0;", side_pane)
        time.sleep(2)
        
        # Take screenshot after scrolling back to top
        await take_screenshot(driver, update, "search_results_top")

        # 4. Upload updated main file back to Drive
        if new_links_to_append and service:
            media = MediaFileUpload(GROUP_LINKS_FILENAME, mimetype='text/plain', resumable=True)
            service.files().update(fileId=GROUP_LINKS_FILE_ID, media_body=media).execute()
            
            # 5. Update temporary new links file (clearing previous data)
            update_temp_new_links_file(service, new_links_to_append)

        # 6. Prepare links for response
        # Get existing links found in this search (excluding new ones)
        existing_found_links = []
        for link in all_found_links:
            if link in existing_links and link not in existing_found_links:
                existing_found_links.append(link)
        
        # Limit to requested number
        existing_found_links = existing_found_links[:num_results]
        
        # 7. Final Report with both existing and new links
        total_count = len(existing_links) + len(new_links_to_append)
        response_text = (
            f"✅ *Search Complete*\n\n"
            f"✨ New links found: {len(new_links_to_append)}\n"
            f"📊 Total links in main Drive file: {total_count}\n"
            f"📄 [View Main Drive File](https://drive.google.com/file/d/{GROUP_LINKS_FILE_ID}/view)\n"
            f"📄 [View New Links Temp File](https://drive.google.com/file/d/{TEMP_NEW_LINKS_FILE_ID}/view)\n\n"
        )
        
        # Add existing links section if any found
        if existing_found_links:
            response_text += "📌 *Existing Links Found:*\n"
            for i, link in enumerate(existing_found_links, 1):
                response_text += f"{i}. {link}\n"
            
            # Add empty line separator if there are new links
            if new_links_to_append:
                response_text += "\n"
        
        # Add new links section if any found
        if new_links_to_append:
            response_text += "🆕 *New Links Added:*\n"
            for i, link in enumerate(new_links_to_append, 1):
                response_text += f"{i}. {link}\n"
        
        # If no links found at all
        if not existing_found_links and not new_links_to_append:
            response_text += "❌ No links found in search results."

        if len(response_text) > 4000:
            # Create a text file with the results
            with io.BytesIO(response_text.encode()) as f:
                f.name = "search_results.txt"
                await update.message.reply_document(
                    document=f, 
                    caption=f"✅ Found {len(existing_found_links)} existing and {len(new_links_to_append)} new links"
                )
        else:
            await update.message.reply_text(
                response_text, 
                parse_mode='Markdown', 
                disable_web_page_preview=True
            )
            
        await status_msg.delete()

    except Exception as e:
        await update.message.reply_text(f"❌ Error in Search Handler: {e}")
        logger.error(traceback.format_exc())