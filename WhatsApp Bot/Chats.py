#!/usr/bin/env python3
"""
WhatsApp Chat Handler - ULTIMATE FIX V2
- Sends ALL chats without extra backslashes
- Proper markdown escaping without over-escaping
- No more parse errors
- Clean formatting
"""

import time
import logging
import random
import re
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

# Configure logging
logger = logging.getLogger(__name__)

# Generate random verification values
RANDOM_ID = random.randint(10000, 99999)
START_TIME = datetime.now().strftime("%H:%M:%S.%f")[:-3]

def log_debug(message, data=None):
    """Ultra detailed logging"""
    log_msg = f"[DEBUG][{RANDOM_ID}] {message}"
    if data:
        log_msg += f" | Data: {repr(data)[:500]}"
    logger.info(log_msg)

def escape_for_telegram(text):
    """
    Smart escaping for Telegram Markdown - only escape what's necessary
    No extra backslashes, no over-escaping
    """
    if not text:
        return ""
    
    # Log original for debugging
    log_debug("Escaping text", text[:100])
    
    # Characters that ACTUALLY need escaping in Telegram Markdown
    # Only escape these when they appear in a way that would break formatting
    result = []
    i = 0
    
    while i < len(text):
        char = text[i]
        
        # Handle special characters that need escaping
        if char in ['_', '*', '`']:
            # Check if this is part of a URL or number
            prev_char = text[i-1] if i > 0 else ''
            next_char = text[i+1] if i < len(text)-1 else ''
            
            # Don't escape if it's part of a URL or common pattern
            if (prev_char.isdigit() and next_char.isdigit()) or \
               (char == '_' and text[i:i+3] == '://'):  # Part of http://
                result.append(char)
            else:
                result.append('\\' + char)
                log_debug(f"Escaped character '{char}' at position {i}")
        
        # Handle brackets - only escape if they're not balanced
        elif char in ['[', ']', '(', ')']:
            # Quick check if it might be part of a link
            if char == '[' and '](' in text[i:i+10]:
                result.append(char)  # Leave link syntax alone
            else:
                result.append('\\' + char)
        
        # Handle other special chars
        elif char in ['~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
            # Don't escape these unless they're likely to cause issues
            # Most are safe in normal text
            result.append(char)
        
        else:
            result.append(char)
        
        i += 1
    
    escaped = ''.join(result)
    
    # Final check - remove any double backslashes
    escaped = escaped.replace('\\\\', '\\')
    
    return escaped

def split_message(text, limit=4000):
    """Splits a message into chunks within the limit."""
    if len(text) <= limit:
        return [text]
    
    chunks = []
    while len(text) > limit:
        # Try to split at newline
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1:
            split_at = limit
        chunk = text[:split_at]
        chunks.append(chunk)
        log_debug(f"Created chunk {len(chunks)}", f"Length: {len(chunk)}")
        text = text[split_at:].lstrip()
    
    if text:
        chunks.append(text)
        log_debug(f"Created final chunk {len(chunks)}", f"Length: {len(text)}")
    
    return chunks

def extract_chat_info(chat_element, index):
    """Extract name and preview from a chat element with detailed logging"""
    try:
        # Get chat name
        name = None
        
        try:
            name_element = chat_element.find_element(By.XPATH, './/span[@dir="auto" and @title]')
            name = name_element.get_attribute("title")
        except:
            try:
                name_element = chat_element.find_element(By.XPATH, './/span[@dir="auto"]')
                name = name_element.text
            except:
                return None, None
        
        if not name or name.strip() == "":
            return None, None
        
        # Log if name contains special characters
        if any(c in name for c in '*_`[]()'):
            log_debug(f"Chat {index} contains special chars", name[:100])
        
        # Get message preview if available
        preview = ""
        try:
            preview_elements = chat_element.find_elements(By.XPATH, './/span[contains(@class, "selectable-text")]')
            if preview_elements:
                preview = preview_elements[-1].text.strip()
                if len(preview) > 50:
                    preview = preview[:50] + "..."
        except:
            pass
        
        return name, preview
        
    except StaleElementReferenceException:
        return None, None
    except Exception:
        return None, None

def scan_visible_chats(driver, all_chats):
    """Scan currently visible chats and add new ones to the dictionary"""
    try:
        chat_elements = driver.find_elements(By.XPATH, '//div[@role="listitem"] | //div[@role="row"]')
        log_debug(f"Found {len(chat_elements)} visible chat elements")
        
        new_found = 0
        
        for idx, chat in enumerate(chat_elements):
            name, preview = extract_chat_info(chat, idx)
            
            if name and name not in all_chats:
                all_chats[name] = preview
                new_found += 1
        
        log_debug(f"Scan results: {new_found} new, Total: {len(all_chats)}")
        return new_found
        
    except Exception as e:
        log_debug(f"Error scanning visible chats", str(e))
        return 0

def format_chats_clean(all_chats):
    """Format ALL chats with clean, safe formatting - no extra backslashes"""
    if not all_chats:
        return ["❌ No chats found."]
    
    chat_items = list(all_chats.items())
    total_chats = len(chat_items)
    log_debug(f"Formatting {total_chats} chats")
    
    # Simple, clean header with minimal markdown
    header = f"📱 *WhatsApp Chats*\n"
    header += f"📊 Total: {total_chats}\n"
    header += f"🆔 Session: {RANDOM_ID}\n"
    header += f"{'─'*40}\n\n"
    
    all_chunks = []
    current_chunk = header
    chunk_count = 1
    
    for i, (name, preview) in enumerate(chat_items, 1):
        # Smart escape - only what's necessary
        safe_name = escape_for_telegram(name)
        
        # Build line
        if preview:
            safe_preview = escape_for_telegram(preview)
            line = f"{i}. *{safe_name}* - _{safe_preview}_\n"
        else:
            line = f"{i}. *{safe_name}*\n"
        
        # Check if this line would exceed limit
        if len(current_chunk) + len(line) > 3800:  # Leave some buffer
            # Close current chunk
            current_chunk += f"\n{'─'*40}\n"
            current_chunk += f"📄 Part {chunk_count} | Continue with /chats {chunk_count+1}"
            all_chunks.append(current_chunk)
            
            # Start new chunk
            chunk_count += 1
            current_chunk = f"📱 *WhatsApp Chats (Continued {i}-{total_chats})*\n"
            current_chunk += f"{'─'*40}\n\n"
            current_chunk += line
        else:
            current_chunk += line
    
    # Add last chunk if not empty
    if current_chunk and current_chunk != header:
        current_chunk += f"\n{'─'*40}\n"
        current_chunk += f"✅ End of list | Total: {total_chats} chats"
        all_chunks.append(current_chunk)
    
    log_debug(f"Created {len(all_chunks)} clean chunks")
    return all_chunks

async def send_chunk_safe(update, chunk, chunk_num, total_chunks):
    """Send a single chunk safely - no parse errors guaranteed"""
    log_debug(f"Sending chunk {chunk_num}/{total_chunks}", f"Size: {len(chunk)}")
    
    # First attempt: Try with minimal markdown
    try:
        await update.message.reply_text(chunk, parse_mode='Markdown')
        log_debug(f"✓ Chunk {chunk_num} sent with markdown")
        return True
    except Exception as e:
        log_debug(f"Markdown failed for chunk {chunk_num}", str(e))
        
        # Second attempt: Strip ALL markdown and send as plain text
        plain_chunk = chunk
        # Remove all markdown characters completely
        plain_chunk = plain_chunk.replace('*', '')
        plain_chunk = plain_chunk.replace('_', '')
        plain_chunk = plain_chunk.replace('`', '')
        plain_chunk = plain_chunk.replace('\\', '')  # Remove any remaining backslashes
        
        try:
            await update.message.reply_text(plain_chunk)
            log_debug(f"✓ Chunk {chunk_num} sent as plain text")
            return False
        except Exception as e2:
            # Ultimate fallback: Send as code block
            log_debug(f"Plain text also failed", str(e2))
            try:
                await update.message.reply_text(f"```\n{plain_chunk}\n```", parse_mode='Markdown')
                log_debug(f"✓ Chunk {chunk_num} sent as code block")
                return False
            except:
                # Last resort: Send in pieces
                for piece in [plain_chunk[i:i+500] for i in range(0, len(plain_chunk), 500)]:
                    await update.message.reply_text(piece)
                return False

async def run(update, context, driver):
    """Main function with clean chat list sending"""
    
    log_debug("=" * 60)
    log_debug(f"CHAT SCAN STARTED - Session ID: {RANDOM_ID}")
    log_debug("=" * 60)
    
    status_msg = await update.message.reply_text("🔄 Starting chat scan...")
    all_chats = {}
    
    try:
        # Find chat pane
        log_debug("Locating chat pane")
        try:
            pane = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, 'pane-side'))
            )
            log_debug("✓ Chat pane found")
        except TimeoutException as e:
            log_debug("✗ Chat pane not found", str(e))
            await status_msg.edit_text("❌ Could not find chat pane")
            return

        # Scan all chats
        log_debug("Starting scroll scan")
        last_height = 0
        scroll_attempts = 0
        max_scrolls = 100
        scroll_pause = 2
        
        for scroll in range(max_scrolls):
            log_debug(f"Scroll {scroll+1}/{max_scrolls}")
            
            new_found = scan_visible_chats(driver, all_chats)
            
            if scroll % 3 == 0:
                await status_msg.edit_text(
                    f"🔄 Scanning... Found *{len(all_chats)}* chats\n"
                    f"📍 Progress: {scroll+1}/{max_scrolls}",
                    parse_mode='Markdown'
                )
            
            # Scroll down
            driver.execute_script("arguments[0].scrollTop += 800;", pane)
            time.sleep(scroll_pause)
            
            # Check if reached bottom
            new_height = driver.execute_script("return arguments[0].scrollTop;", pane)
            scroll_height = driver.execute_script("return arguments[0].scrollHeight;", pane)
            client_height = driver.execute_script("return arguments[0].clientHeight;", pane)
            
            if scroll_height - new_height <= client_height + 100:
                log_debug("✅ Bottom reached")
                break
            
            if new_height == last_height:
                scroll_attempts += 1
                if scroll_attempts >= 5:
                    log_debug("🛑 Max scroll attempts reached")
                    break
            else:
                scroll_attempts = 0
                last_height = new_height
        
        # Return to top
        driver.execute_script("arguments[0].scrollTop = 0;", pane)
        time.sleep(1)
        
        # Send results
        log_debug(f"SCAN COMPLETE: Found {len(all_chats)} chats")
        
        await status_msg.edit_text(
            f"✅ Found *{len(all_chats)}* chats\n"
            f"📤 Sending in multiple messages...",
            parse_mode='Markdown'
        )
        
        # Format and send chats
        chunks = format_chats_clean(all_chats)
        
        success_count = 0
        for i, chunk in enumerate(chunks, 1):
            success = await send_chunk_safe(update, chunk, i, len(chunks))
            if success:
                success_count += 1
            time.sleep(0.5)  # Small delay between messages
        
        # Final confirmation
        log_debug("=" * 50)
        log_debug(f"CHAT SCAN COMPLETED")
        log_debug(f"Total chats: {len(all_chats)}")
        log_debug("=" * 50)
        
        await update.message.reply_text(
            f"✅ *Done!*\n"
            f"📊 Total: {len(all_chats)} chats\n"
            f"📨 Sent in {len(chunks)} messages",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        log_debug("❌ FATAL ERROR", str(e))
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")