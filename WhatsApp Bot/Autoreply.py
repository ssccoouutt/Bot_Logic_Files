#!/usr/bin/env python3
"""
WhatsApp Auto-Reply Handler - Core Functionality
- Auto-reply to 'king' messages
- Log WhatsApp group links to Google Drive file
- EXACT same session loading logic as working version
"""

import os
import sys
import time
import re
import json
import threading
import requests
import tempfile
import zipfile
import shutil
import socket
import random
import io
import argparse
import http.server
import socketserver
from datetime import datetime, timedelta, timezone
from threading import Thread, Lock

# Import required libraries
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ======================== CONFIGURATION ========================
COOLDOWN_MS = 3000  # 3 seconds cooldown for "king" replies
SCAN_INTERVAL_MS = 100  # 100ms scan interval (10x per second)
MONITOR_CHATS_COUNT = 3  # Monitor only first 3 chats for speed

# Auto-reply paths - EXACT same as previous working version
AUTOREPLY_SESSION_DIR = "whatsapp_session_autoreply"
AUTOREPLY_PROFILE_DIR = os.path.join(AUTOREPLY_SESSION_DIR, "chrome_profile")

# Session URL - EXACT same as previous working version
AUTOREPLY_SESSION_URL = "https://drive.usercontent.google.com/download?id=1Mc3sUPM_e2W603NfWKCYq--RixgFlsPo&export=download&confirm=t&uuid=0b821276-ae72-4e09-ae66-9a5719d351bd"

# Google Drive configuration for group links file
TOKEN_URL = "https://drive.usercontent.google.com/download?id=1NZ3NvyVBnK85S8f5eTZJS5uM5c59xvGM&export=download"
GROUP_LINKS_FILE_ID = "1JsbQRBaKtAefA60P7aaOdjOc9Zi5etPQ"
GROUP_LINKS_FILENAME = "whatsapp_group_links.txt"
DRIVE_SYNC_INTERVAL = 60  # Sync with Drive every 60 seconds

def find_free_port():
    """Find a random free port between 9000-9999"""
    for _ in range(20):
        port = random.randint(9000, 9999)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        if result != 0:
            return port
    return 9225

# ======================== GLOBAL STATE ========================
driver = None
monitor_running = False
autoreply_active = False
monitor_thread = None
keep_alive_thread = None
driver_lock = Lock()
chrome_ready = False
httpd = None
http_port = None

reply_cooldown = {}
cooldown_lock = Lock()
last_previews = {}
monitor_lock = Lock()

# Group link tracking
group_links_cache = set()
group_links_cache_lock = Lock()
last_drive_sync_time = 0
last_drive_auth_failure_time = 0
DRIVE_AUTH_FAILURE_COOLDOWN = 300  # Wait 5 minutes after auth failure

def debug_print(message):
    """Simple debug output - EXACT same format as working version"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    pid = os.getpid()
    print(f"[{timestamp}] [AUTOREPLY-PID:{pid}] {message}")

def extract_whatsapp_group_link(text):
    """Extract WhatsApp group invite link from text"""
    patterns = [
        r'(https?://chat\.whatsapp\.com/[a-zA-Z0-9_-]+[^\s]*)',
        r'(https?://wa\.me/[a-zA-Z0-9_-]+[^\s]*)',
        r'(https?://whatsapp\.com/channel/[a-zA-Z0-9_-]+[^\s]*)',
        r'(https?://[^\s]+?whatsapp\.com[^\s]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            url = match.group(1)
            url = re.sub(r'[.,;:!?)$]$', '', url)
            return url
    return None

def is_whatsapp_group_link(text):
    """Check if text contains a WhatsApp group link"""
    patterns = [
        r'chat\.whatsapp\.com/[a-zA-Z0-9_-]+',
        r'wa\.me/[a-zA-Z0-9_-]+',
        r'whatsapp\.com/channel/[a-zA-Z0-9_-]+',
        r'whatsapp\.com'
    ]
    
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def download_token_from_drive():
    """Download token.json from Google Drive"""
    try:
        debug_print("📥 Downloading token.json from Google Drive...")
        
        token_response = requests.get(TOKEN_URL, stream=True)
        token_response.raise_for_status()
        
        token_filename = "token.json"
        
        with open(token_filename, 'wb') as f:
            for chunk in token_response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        debug_print(f"✅ Successfully downloaded token file")
        return token_filename
    except Exception as e:
        debug_print(f"❌ Failed to download token: {e}")
        return None

def get_drive_service():
    """Get authenticated Google Drive service"""
    global last_drive_auth_failure_time
    
    current_time = time.time()
    if current_time - last_drive_auth_failure_time < DRIVE_AUTH_FAILURE_COOLDOWN:
        debug_print(f"⏳ In auth failure cooldown, skipping Drive auth")
        return None
    
    try:
        token_filename = download_token_from_drive()
        if not token_filename:
            last_drive_auth_failure_time = current_time
            return None
        
        with open(token_filename, 'r') as f:
            token_data = json.load(f)
        
        expiry_date = datetime.fromisoformat(token_data['expiry'].replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > expiry_date:
            debug_print("🔄 Token expired. Refreshing...")
            
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
            token_data['expiry'] = (datetime.now(timezone.utc) + timedelta(seconds=new_token.get('expires_in', 3600))).isoformat()
            
            debug_print("✅ Token refreshed successfully")
        
        creds = Credentials(
            token=token_data['token'],
            refresh_token=token_data['refresh_token'],
            token_uri=token_data['token_uri'],
            client_id=token_data['client_id'],
            client_secret=token_data['client_secret'],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        
        service = build('drive', 'v3', credentials=creds)
        
        if os.path.exists(token_filename):
            os.remove(token_filename)
        
        last_drive_auth_failure_time = 0
        return service
    except Exception as e:
        debug_print(f"❌ Error getting Drive service: {e}")
        if os.path.exists("token.json"):
            os.remove("token.json")
        last_drive_auth_failure_time = current_time
        return None

def download_group_links_file():
    """Download the current group links file from Google Drive"""
    global group_links_cache
    
    try:
        service = get_drive_service()
        if not service:
            debug_print("❌ Could not get Drive service")
            return False
        
        debug_print(f"📥 Downloading group links file from Drive...")
        
        request = service.files().get_media(fileId=GROUP_LINKS_FILE_ID)
        file_content = request.execute()
        
        with open(GROUP_LINKS_FILENAME, 'wb') as f:
            f.write(file_content)
        
        if os.path.exists(GROUP_LINKS_FILENAME):
            with open(GROUP_LINKS_FILENAME, 'r') as f:
                links = [line.strip() for line in f.readlines() if line.strip()]
            
            with group_links_cache_lock:
                group_links_cache = set(links)
            
            debug_print(f"✅ Downloaded {len(links)} group links from Drive")
            return True
        else:
            with group_links_cache_lock:
                group_links_cache = set()
            debug_print("⚠️ No existing group links file, starting fresh")
            return False
            
    except Exception as e:
        debug_print(f"❌ Error downloading group links file: {e}")
        with group_links_cache_lock:
            group_links_cache = set()
        return False

def upload_group_links_file():
    """Upload the updated group links file to Google Drive"""
    try:
        service = get_drive_service()
        if not service:
            debug_print("❌ Could not get Drive service")
            return False
        
        if not os.path.exists(GROUP_LINKS_FILENAME):
            debug_print("⚠️ No local file to upload")
            return False
        
        debug_print(f"📤 Uploading group links file to Drive...")
        
        media = MediaFileUpload(GROUP_LINKS_FILENAME, mimetype='text/plain', resumable=True)
        
        updated_file = service.files().update(
            fileId=GROUP_LINKS_FILE_ID,
            media_body=media
        ).execute()
        
        debug_print(f"✅ Successfully uploaded group links file to Drive")
        return True
    except Exception as e:
        debug_print(f"❌ Error uploading group links file: {e}")
        return False

def add_group_link(link):
    """Add a new group link to the file if it doesn't exist"""
    global group_links_cache, last_drive_sync_time
    
    try:
        link = link.strip()
        
        with group_links_cache_lock:
            if link in group_links_cache:
                debug_print(f"⏭️ Group link already exists in cache: {link}")
                return False
            
            group_links_cache.add(link)
            
            with open(GROUP_LINKS_FILENAME, 'w') as f:
                for saved_link in sorted(group_links_cache):
                    f.write(saved_link + '\n')
            
            debug_print(f"✅ Added new group link to local file: {link}")
            
            # Upload to Drive in background thread
            def upload():
                success = upload_group_links_file()
                if success:
                    last_drive_sync_time = time.time()
                    debug_print(f"✅ Synced with Drive: {link}")
                else:
                    debug_print(f"⚠️ Added locally but Drive sync failed")
            
            Thread(target=upload, daemon=True).start()
            
            return True
    except Exception as e:
        debug_print(f"❌ Error adding group link: {e}")
        return False

def sync_with_drive():
    """Synchronize local cache with Drive file"""
    global group_links_cache, last_drive_sync_time
    
    current_time = time.time()
    if current_time - last_drive_sync_time < DRIVE_SYNC_INTERVAL:
        return True
    
    try:
        service = get_drive_service()
        if not service:
            return False
        
        debug_print(f"🔄 Syncing with Drive...")
        
        request = service.files().get_media(fileId=GROUP_LINKS_FILE_ID)
        file_content = request.execute()
        
        with open(GROUP_LINKS_FILENAME + '.tmp', 'wb') as f:
            f.write(file_content)
        
        with open(GROUP_LINKS_FILENAME + '.tmp', 'r') as f:
            drive_links = set([line.strip() for line in f.readlines() if line.strip()])
        
        with group_links_cache_lock:
            merged_links = group_links_cache.union(drive_links)
            
            if len(merged_links) > len(group_links_cache):
                group_links_cache = merged_links
                
                with open(GROUP_LINKS_FILENAME, 'w') as f:
                    for link in sorted(group_links_cache):
                        f.write(link + '\n')
                
                upload_group_links_file()
                debug_print(f"✅ Merged {len(drive_links)} Drive links with {len(group_links_cache)} local links")
            else:
                debug_print(f"✅ Local cache is up to date ({len(group_links_cache)} links)")
        
        if os.path.exists(GROUP_LINKS_FILENAME + '.tmp'):
            os.remove(GROUP_LINKS_FILENAME + '.tmp')
        
        last_drive_sync_time = current_time
        return True
    except Exception as e:
        debug_print(f"❌ Error syncing with Drive: {e}")
        return False

# ======================== EXACT SAME SESSION LOADING LOGIC ========================

def cleanup_old_autoreply():
    """Clean up any old auto-reply processes and files - EXACT same as working version"""
    try:
        if os.path.exists(AUTOREPLY_SESSION_DIR):
            shutil.rmtree(AUTOREPLY_SESSION_DIR)
            debug_print("🧹 Cleaned up old auto-reply session directory")
    except Exception as e:
        debug_print(f"⚠️ Cleanup warning: {e}")

def download_and_extract_session():
    """Download session zip and extract it - EXACT same as working version"""
    try:
        debug_print(f"📥 Loading session from Drive for auto-reply...")
        
        os.makedirs(AUTOREPLY_PROFILE_DIR, exist_ok=True)
        
        response = requests.get(AUTOREPLY_SESSION_URL, stream=True, timeout=60)
        response.raise_for_status()
        
        zip_path = os.path.join(tempfile.gettempdir(), f"session_autoreply_{int(time.time())}.zip")
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            for name in zipf.namelist():
                if name.startswith('chrome_profile/'):
                    target_path = os.path.join(AUTOREPLY_PROFILE_DIR, name.replace('chrome_profile/', '', 1))
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    if not name.endswith('/'):
                        with zipf.open(name) as source, open(target_path, 'wb') as target:
                            shutil.copyfileobj(source, target)
        
        os.remove(zip_path)
        debug_print(f"✅ Auto-reply session loaded")
        return True
        
    except Exception as e:
        debug_print(f"❌ Session load failed: {e}")
        return False

def start_chrome():
    """Start Chrome instance - EXACT same as working version"""
    global driver, chrome_ready, httpd, http_port
    
    with driver_lock:
        try:
            debug_print("🚀 Starting Chrome instance for auto-reply...")
            
            cleanup_old_autoreply()
            
            chrome_options = Options()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
            
            abs_profile_dir = os.path.abspath(AUTOREPLY_PROFILE_DIR)
            chrome_options.add_argument(f"--user-data-dir={abs_profile_dir}")
            chrome_options.add_argument("--profile-directory=Default")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # Find a free port for remote debugging
            debug_port = find_free_port()
            chrome_options.add_argument(f"--remote-debugging-port={debug_port}")
            
            if os.path.exists("/usr/bin/google-chrome"):
                chrome_options.binary_location = "/usr/bin/google-chrome"
            elif os.path.exists("/usr/bin/chromium-browser"):
                chrome_options.binary_location = "/usr/bin/chromium-browser"
            
            # Download session first
            if not download_and_extract_session():
                debug_print("⚠️ Failed to download session, continuing with empty profile")
            
            service = Service()
            driver = webdriver.Chrome(options=chrome_options, service=service)
            
            driver.get("https://web.whatsapp.com")
            debug_print("🌐 Navigated to WhatsApp Web")
            
            chrome_ready = True
            debug_print(f"✅ Chrome ready")

            # Start HTTP server for screenshot requests
            start_http_server()
            
            return True
            
        except Exception as e:
            debug_print(f"❌ Chrome start failed: {e}")
            chrome_ready = False
            return False

# ======================== REST OF FUNCTIONS ========================

def start_http_server():
    """Start HTTP server for screenshot requests"""
    global httpd, http_port
    
    try:
        http_port = find_free_port()
        
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/screenshot':
                    self.handle_screenshot()
                elif self.path == '/status':
                    self.handle_status()
                else:
                    self.send_error(404, "Not Found")
            
            def handle_screenshot(self):
                debug_print("📸 Received screenshot request")
                screenshot_data = take_screenshot()
                if screenshot_data:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(screenshot_data)))
                    self.end_headers()
                    self.wfile.write(screenshot_data)
                    debug_print("✅ Screenshot sent")
                else:
                    self.send_error(500, "Failed to capture screenshot")
            
            def handle_status(self):
                status = {
                    'running': True,
                    'logged_in': check_login_status(),
                    'monitoring': monitor_running and autoreply_active,
                    'links_count': len(group_links_cache) if group_links_cache else 0,
                    'timestamp': datetime.now().isoformat()
                }
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(status).encode('utf-8'))
            
            def log_message(self, format, *args):
                pass  # Suppress HTTP server logs
        
        httpd = socketserver.TCPServer(("", http_port), Handler)
        server_thread = threading.Thread(target=httpd.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        debug_print(f"✅ HTTP server started on port {http_port}")
        
        # Write port to file for main script to read
        port_file = os.path.join(AUTOREPLY_SESSION_DIR, "http_port.txt")
        os.makedirs(AUTOREPLY_SESSION_DIR, exist_ok=True)
        with open(port_file, 'w') as f:
            f.write(str(http_port))
        
    except Exception as e:
        debug_print(f"⚠️ Failed to start HTTP server: {e}")

def stop_chrome():
    """Stop Chrome instance"""
    global driver, chrome_ready, monitor_running, autoreply_active, httpd
    
    monitor_running = False
    autoreply_active = False
    
    time.sleep(1)
    
    with driver_lock:
        if driver:
            try:
                driver.quit()
            except:
                pass
            driver = None
    
    if httpd:
        try:
            httpd.shutdown()
            httpd.server_close()
            debug_print("🛑 HTTP server stopped")
        except:
            pass

    chrome_ready = False
    debug_print("🛑 Chrome stopped")

def take_screenshot():
    """Take screenshot"""
    with driver_lock:
        try:
            if not driver or not chrome_ready:
                debug_print("❌ Chrome not ready for screenshot")
                return None
            
            screenshot_bytes = driver.get_screenshot_as_png()
            
            img = Image.open(io.BytesIO(screenshot_bytes))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85)
            return output.getvalue()
                
        except Exception as e:
            debug_print(f"❌ Screenshot failed: {e}")
            return None

def check_login_status():
    """Check if WhatsApp is logged in"""
    with driver_lock:
        try:
            if not driver:
                return False
            # Check for chat input box (logged in)
            if driver.find_elements(By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]'):
                return True
            # Check for QR code (not logged in)
            if driver.find_elements(By.XPATH, '//canvas[@aria-label="Scan me!"]'):
                return False
            return False
        except:
            return False

def ultra_fast_reply(chat_name, reply_text):
    """Send reply as fast as possible"""
    try:
        start = time.time()
        
        if not chat_name:
            return False
        
        # Find and click chat
        try:
            chat_element = driver.find_element(By.XPATH, f'//span[@title="{chat_name}"]')
            chat_element.click()
        except:
            try:
                driver.execute_script(f"""
                    var spans = document.evaluate("//span[@title='{chat_name}']", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    if(spans.singleNodeValue) {{
                        spans.singleNodeValue.click();
                    }}
                """)
            except:
                return False
        
        time.sleep(0.1)
        
        # Find input box and send message
        input_box = driver.find_element(By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
        input_box.clear()
        input_box.send_keys(reply_text)
        time.sleep(0.1)
        input_box.send_keys(Keys.ENTER)
        
        elapsed = (time.time() - start) * 1000
        debug_print(f"✅ Replied to '{chat_name}' in {elapsed:.0f}ms")
        return True
        
    except Exception as e:
        debug_print(f"❌ Reply failed: {e}")
        return False

def monitor_chats_ultra_fast():
    """Monitor chats every 100ms for instant response"""
    global monitor_running, last_drive_sync_time
    
    debug_print("🚀 Ultra-fast monitor active (100ms intervals)")
    
    consecutive_failures = 0
    last_log_time = time.time()
    
    # Initialize group links
    download_group_links_file()
    last_drive_sync_time = time.time()
    
    while monitor_running:
        loop_start = time.time()
        
        try:
            with driver_lock:
                if not driver or not chrome_ready:
                    time.sleep(1)
                    continue

                if not check_login_status():
                    time.sleep(5)
                    continue
                
                # Periodic Drive sync
                if time.time() - last_drive_sync_time > DRIVE_SYNC_INTERVAL:
                    Thread(target=sync_with_drive, daemon=True).start()
                
                # Get chat elements - fast XPATH
                try:
                    chat_elements = driver.find_elements(By.XPATH, '//div[@role="listitem"]')
                    
                    if len(chat_elements) == 0:
                        chat_elements = driver.find_elements(By.XPATH, '//div[@role="row"]')
                    
                    chat_count = len(chat_elements)
                    
                    # Log chat count occasionally
                    if time.time() - last_log_time > 10:
                        debug_print(f"Found {chat_count} chats in sidebar")
                        last_log_time = time.time()
                    
                    # Monitor only first few chats for speed
                    for chat in chat_elements[:MONITOR_CHATS_COUNT]:
                        try:
                            # Get chat name - fast
                            chat_name = None
                            try:
                                name_element = chat.find_element(By.XPATH, './/span[@dir="auto" and @title]')
                                chat_name = name_element.get_attribute("title")
                            except:
                                try:
                                    name_element = chat.find_element(By.XPATH, './/span[@dir="auto"]')
                                    chat_name = name_element.text
                                except:
                                    continue
                            
                            if not chat_name:
                                continue
                            
                            # Get message preview - fast
                            preview = ""
                            try:
                                preview_elements = chat.find_elements(By.XPATH, './/span[contains(@class, "selectable-text")]')
                                if preview_elements:
                                    preview = preview_elements[-1].text.strip()
                                else:
                                    spans = chat.find_elements(By.XPATH, './/span[@dir="ltr"]')
                                    for span in spans[-3:]:
                                        if span.text and len(span.text.strip()) > 0:
                                            preview = span.text.strip()
                                            break
                            except:
                                pass
                            
                            if not preview:
                                continue
                            
                            with monitor_lock:
                                last_preview = last_previews.get(chat_name, "")
                                
                                if preview != last_preview:
                                    debug_print(f"🆕 New message in '{chat_name}': '{preview[:50]}...'")
                                    
                                    # Check for "king" keyword
                                    if re.search(r'king', preview, re.IGNORECASE):
                                        debug_print(f"🎯 TRIGGER: 'king' found in '{chat_name}'")
                                        
                                        with cooldown_lock:
                                            last_reply_time = reply_cooldown.get(chat_name, 0)
                                            current_time = time.time() * 1000
                                            
                                            if current_time - last_reply_time > COOLDOWN_MS:
                                                reply_cooldown[chat_name] = current_time
                                                # Send reply in background thread
                                                Thread(target=ultra_fast_reply, args=(chat_name, "us"), daemon=True).start()
                                    
                                    # Check for WhatsApp group links
                                    elif is_whatsapp_group_link(preview):
                                        debug_print(f"🔗 WhatsApp group link detected in '{chat_name}'")
                                        
                                        group_link = extract_whatsapp_group_link(preview)
                                        if group_link:
                                            debug_print(f"📋 Extracted group link: {group_link}")
                                            
                                            # Add to Drive in background
                                            Thread(target=add_group_link, args=(group_link,), daemon=True).start()
                                    
                                    last_previews[chat_name] = preview
                                
                        except StaleElementReferenceException:
                            continue
                        except Exception:
                            continue
                            
                except Exception as e:
                    consecutive_failures += 1
                    
                    if consecutive_failures > 10:
                        debug_print("⚠️ Too many failures, refreshing page")
                        try:
                            driver.refresh()
                            time.sleep(5)
                        except:
                            pass
                        consecutive_failures = 0
            
            # Maintain 100ms loop
            elapsed = (time.time() - loop_start) * 1000
            sleep_time = max(0.001, 0.1 - (elapsed / 1000))
            time.sleep(sleep_time)
            
        except Exception:
            time.sleep(0.5)

def keep_alive():
    """Keep session alive"""
    debug_print("💓 Keep-alive thread started")
    while monitor_running and autoreply_active:
        time.sleep(30)
        with driver_lock:
            try:
                if driver and chrome_ready:
                    driver.execute_script("window.scrollBy(0,1)")
            except Exception:
                try:
                    driver.get("https://web.whatsapp.com")
                    time.sleep(5)
                except:
                    pass

def start_monitoring():
    """Start monitoring threads"""
    global monitor_running, autoreply_active, monitor_thread, keep_alive_thread
    
    if not check_login_status():
        debug_print("📱 Not logged in yet - waiting for QR scan")
        return False
    
    monitor_running = True
    autoreply_active = True
    
    with cooldown_lock:
        reply_cooldown.clear()
    with monitor_lock:
        last_previews.clear()
    
    monitor_thread = Thread(target=monitor_chats_ultra_fast, daemon=True)
    monitor_thread.start()
    
    keep_alive_thread = Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    
    debug_print("✅ Monitoring started")
    return True

def stop_monitoring():
    """Stop monitoring threads"""
    global monitor_running, autoreply_active
    monitor_running = False
    autoreply_active = False
    time.sleep(1)
    debug_print("🛑 Monitoring stopped")

def login_checker():
    """Check login status periodically and start monitoring when logged in"""
    time.sleep(5)
    last_status = None
    while chrome_ready:
        current_status = check_login_status()
        if current_status != last_status:
            if current_status:
                debug_print("✅ Logged in! Starting monitoring...")
                start_monitoring()
            else:
                debug_print("📱 Not logged in - QR code detected. Waiting for scan...")
            last_status = current_status
        time.sleep(5)

# ======================== RUN FUNCTION FOR MAIN SCRIPT ========================

async def run(update, context, driver_param=None):
    """Run function for main script compatibility"""
    debug_print("⚠️ run() called - this should not happen in normal operation")
    return True

async def main(update, context, driver_param):
    """Main function for main script compatibility"""
    return await run(update, context, driver_param)

# ======================== MAIN ENTRY POINT ========================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", help="Profile directory (ignored)")
    parser.add_argument("--session-url", help="Session URL (ignored - using internal)")
    args = parser.parse_args()
    
    print("="*50)
    print("🚀 Auto-Reply Handler Starting")
    print("="*50)
    print("⚡ Features:")
    print("  • Auto-reply to 'king' → 'us'")
    print("  • WhatsApp group link logging to Drive")
    print(f"  • Group links file ID: {GROUP_LINKS_FILE_ID}")
    print("="*50)
    
    if start_chrome():
        debug_print("✅ Auto-reply handler ready")
        
        # Start login checker thread
        Thread(target=login_checker, daemon=True).start()
        
        debug_print(f"🌐 HTTP server running on port {http_port}")
        debug_print("📡 Available endpoints:")
        debug_print(f"   • Screenshot: http://localhost:{http_port}/screenshot")
        debug_print(f"   • Status: http://localhost:{http_port}/status")
        debug_print("⌨️ Press Ctrl+C to stop")
        
        # Keep running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            debug_print("\n🛑 Shutting down...")
            stop_monitoring()
            stop_chrome()
            debug_print("👋 Goodbye!")
    else:
        debug_print("❌ Failed to start Chrome")
        sys.exit(1)