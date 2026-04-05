#!/usr/bin/env python3
"""
WhatsApp Extract Handler - ULTIMATE FIXED VERSION
- Extracts messages from a specific chat
- Clean formatting without extra backslashes
- Proper reply message handling
- CORRECT Incoming/Outgoing labeling
- NO merged phone numbers with text
- Clean message formatting
"""

import time
import logging
import random
import re
import traceback
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Configure logging
logger = logging.getLogger(__name__)

# Generate random verification values
RANDOM_ID = random.randint(10000, 99999)

def log_debug(message, data=None):
    """Detailed logging"""
    log_msg = f"[EXTRACT][{RANDOM_ID}] {message}"
    if data:
        log_msg += f" | Data: {repr(data)[:500]}"
    logger.info(log_msg)

def escape_minimal(text):
    """
    Minimal escaping for Telegram Markdown - only escape what's necessary
    No extra backslashes
    """
    if not text:
        return ""
    
    # Only escape * and _ when they're likely to cause formatting
    if '*' in text and not (text.startswith('*') and text.endswith('*')):
        text = text.replace('*', '\\*')
    if '_' in text and not (text.startswith('_') and text.endswith('_')):
        text = text.replace('_', '\\_')
    
    return text

def split_message(text, limit=4000):
    """Splits a message into chunks within the limit."""
    if len(text) <= limit:
        return [text]
    
    chunks = []
    while len(text) > limit:
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    
    if text:
        chunks.append(text)
    
    return chunks

def find_and_open_chat(driver, chat_name):
    """Find and open a chat by name"""
    try:
        log_debug(f"Searching for chat: {chat_name}")
        
        # Try different search box selectors
        search_box_selectors = [
            '//div[@contenteditable="true"][@data-tab="3"]',
            '//div[@contenteditable="true"][@data-tab="6"]',
            '//div[contains(@class, "lexical-rich-text-input")]//div[@contenteditable="true"]',
            '//div[@contenteditable="true"][@spellcheck="true"]'
        ]
        
        search_box = None
        for selector in search_box_selectors:
            try:
                search_box = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                log_debug(f"Found search box with selector: {selector}")
                break
            except:
                continue
        
        if not search_box:
            log_debug("Could not find search box")
            return False
        
        # Clear search box
        search_box.click()
        time.sleep(0.5)
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        time.sleep(0.5)
        
        # Type chat name
        search_box.send_keys(chat_name)
        time.sleep(3)  # Wait for search results
        
        # Try different chat selectors
        chat_selectors = [
            f'//span[@title="{chat_name}"]',
            f'//span[contains(@title, "{chat_name}")]',
            f'//div[@role="listitem"]//span[contains(text(), "{chat_name}")]',
            f'//div[contains(@class, "chat")]//span[contains(text(), "{chat_name}")]'
        ]
        
        for selector in chat_selectors:
            try:
                chat_element = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                chat_element.click()
                time.sleep(3)  # Wait for chat to open
                log_debug(f"Successfully opened chat: {chat_name}")
                return True
            except:
                continue
        
        log_debug(f"Chat not found: {chat_name}")
        return False
        
    except Exception as e:
        log_debug(f"Error finding chat: {str(e)}")
        return False

def extract_messages_from_chat(driver):
    """Extract messages from currently opened chat using JavaScript"""
    try:
        # Scroll to top to load messages
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
        
        # JavaScript to extract messages with proper cleaning
        js_script = """
        function cleanMessageText(text) {
            if (!text) return '';
            
            // Remove leading dots and spaces
            text = text.replace(/^\\.+\\s*/, '');
            
            // Remove phone numbers at the beginning of the message
            text = text.replace(/^\\+?\\d[\\d\\s-]{7,15}\\s*/, '');
            
            // Remove any remaining phone number patterns
            text = text.replace(/\\+?\\d{10,15}/g, '');
            
            // Remove trailing timestamps
            text = text.replace(/\\s*\\d{1,2}:\\d{2}\\s[AP]M\\s*$/i, '');
            
            // Clean up multiple spaces
            text = text.replace(/\\s+/g, ' ').trim();
            
            return text;
        }
        
        let messages = [];
        let messageElements = document.querySelectorAll('div.message-in, div.message-out');
        
        messageElements.forEach((msg) => {
            try {
                let info = "";
                let messageText = "";
                let quotedContent = "";
                let isOutgoing = msg.classList.contains('message-out');
                
                // Get timestamp and sender info
                let copyableText = msg.querySelector('div.copyable-text');
                if (copyableText) {
                    info = copyableText.getAttribute('data-pre-plain-text') || "";
                    info = info.replace('[', '').replace(']', '');
                    
                    // Clone to avoid modifying original
                    let clone = copyableText.cloneNode(true);
                    
                    // Extract quoted message if present
                    let quoted = clone.querySelector('div[aria-label="Quoted Message"], span.quoted-mention, div._am_v, div._am_w, div._am_x');
                    if (quoted) {
                        quotedContent = quoted.innerText.trim();
                        quoted.remove();
                    }
                    
                    // Get the message text and clean it
                    messageText = clone.innerText.trim();
                    
                    // Clean up the message text
                    messageText = cleanMessageText(messageText);
                    
                    // If there's quoted content, clean it too
                    if (quotedContent) {
                        quotedContent = cleanMessageText(quotedContent);
                    }
                }
                
                // Only add if we have actual content
                if (messageText && messageText.length > 0) {
                    if (isOutgoing) {
                        let formattedMsg = `[Outgoing] ${info}${messageText}`;
                        if (quotedContent) {
                            formattedMsg += `\\n📎 Replying to: ${quotedContent}`;
                        }
                        messages.push(formattedMsg);
                    } else {
                        let formattedMsg = `[Incoming] ${info}${messageText}`;
                        if (quotedContent) {
                            formattedMsg += `\\n📎 Replying to: ${quotedContent}`;
                        }
                        messages.push(formattedMsg);
                    }
                }
                
            } catch (e) {
                // Skip problematic messages
            }
        });
        
        return messages;
        """
        
        messages = driver.execute_script(js_script)
        log_debug(f"Extracted {len(messages)} messages")
        return messages
        
    except Exception as e:
        log_debug(f"Error extracting messages: {str(e)}")
        return []

def format_messages_clean(chat_name, messages):
    """Format messages with clean, readable formatting"""
    if not messages:
        return ["❌ No messages found."]
    
    total_messages = len(messages)
    log_debug(f"Formatting {total_messages} messages from {chat_name}")
    
    # Simple, clean header
    header = f"📝 Messages from {chat_name}\n"
    header += f"📊 Total: {total_messages} (showing last 50)\n"
    header += f"{'─'*40}\n\n"
    
    all_chunks = []
    current_chunk = header
    chunk_count = 1
    
    # Take last 50 messages
    for i, msg in enumerate(messages[-50:], 1):
        # Remove any backslashes
        msg = msg.replace('\\', '')
        
        # Further clean the message
        # Fix any remaining phone number issues
        msg = re.sub(r'(\+\d+\s*)([A-Za-z])', r'\1 \2', msg)  # Add space between phone and text
        msg = re.sub(r'(\d{10,15})([A-Za-z])', r'\1 \2', msg)  # Add space between number and text
        msg = re.sub(r'\.(\+\d+)', r'\1', msg)  # Remove dot before phone numbers
        
        # Check if message has a reply
        if "📎 Replying to:" in msg:
            parts = msg.split("📎 Replying to:", 1)
            main_part = parts[0].strip()
            reply_part = parts[1].strip()
            
            # Clean up main part
            main_part = re.sub(r'\s+', ' ', main_part)  # Clean multiple spaces
            
            # Clean up reply part
            reply_part = re.sub(r'\s+', ' ', reply_part)
            
            # Escape minimal characters
            main_safe = escape_minimal(main_part)
            reply_safe = escape_minimal(reply_part)
            
            line = f"{i}. {main_safe}\n"
            line += f"   └─ 💬 {reply_safe}\n"
        else:
            # Normal message
            msg = re.sub(r'\s+', ' ', msg)  # Clean multiple spaces
            safe_msg = escape_minimal(msg)
            line = f"{i}. {safe_msg}\n"
        
        # Add separator
        line += f"{'─'*30}\n\n"
        
        # Check if this line would exceed limit
        if len(current_chunk) + len(line) > 3800:
            # Close current chunk
            current_chunk += f"\n📄 Part {chunk_count} | Continue..."
            all_chunks.append(current_chunk)
            
            # Start new chunk
            chunk_count += 1
            current_chunk = f"📝 {chat_name} (Continued)\n"
            current_chunk += f"{'─'*40}\n\n"
            current_chunk += line
        else:
            current_chunk += line
    
    # Add last chunk
    if current_chunk and current_chunk != header:
        current_chunk += f"\n✅ End of messages | Total: {total_messages}"
        all_chunks.append(current_chunk)
    
    log_debug(f"Created {len(all_chunks)} message chunks")
    return all_chunks

async def send_chunk_safe(update, chunk, chunk_num, total_chunks):
    """Send a single chunk safely with fallbacks"""
    log_debug(f"Sending chunk {chunk_num}/{total_chunks}", f"Size: {len(chunk)}")
    
    # Try with markdown
    try:
        await update.message.reply_text(chunk, parse_mode='Markdown')
        log_debug(f"✓ Chunk {chunk_num} sent with markdown")
        return True
    except Exception as e:
        log_debug(f"Markdown failed for chunk {chunk_num}", str(e))
        
        # Fallback: Strip all markdown and send as plain text
        plain_chunk = chunk
        plain_chunk = plain_chunk.replace('*', '')
        plain_chunk = plain_chunk.replace('_', '')
        plain_chunk = plain_chunk.replace('`', '')
        plain_chunk = plain_chunk.replace('\\', '')
        
        try:
            await update.message.reply_text(plain_chunk)
            log_debug(f"✓ Chunk {chunk_num} sent as plain text")
            return False
        except:
            # Last resort: Send in small pieces
            for piece in [plain_chunk[i:i+500] for i in range(0, len(plain_chunk), 500)]:
                await update.message.reply_text(piece)
            return False

async def run(update, context, driver):
    """Main extract function"""
    
    log_debug("=" * 60)
    log_debug(f"EXTRACT STARTED - Session ID: {RANDOM_ID}")
    log_debug("=" * 60)
    
    # Get chat name from arguments
    if not context.args:
        await update.message.reply_text("❌ Usage: `/extract [chat name]`")
        return
    
    chat_name = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Searching for: *{chat_name}*...", parse_mode='Markdown')
    
    try:
        # Find and open chat
        log_debug(f"Looking for chat: {chat_name}")
        found = find_and_open_chat(driver, chat_name)
        
        if not found:
            await status_msg.edit_text(f"❌ Chat '{chat_name}' not found.")
            return
        
        await status_msg.edit_text(f"📥 Extracting messages from *{chat_name}*...", parse_mode='Markdown')
        
        # Extract messages
        messages = extract_messages_from_chat(driver)
        
        if not messages:
            await status_msg.edit_text(f"❌ No messages found in '{chat_name}'.")
            return
        
        # Format and send messages
        await status_msg.edit_text(
            f"✅ Found *{len(messages)}* messages\n"
            f"📤 Sending in multiple messages...",
            parse_mode='Markdown'
        )
        
        chunks = format_messages_clean(chat_name, messages)
        
        for i, chunk in enumerate(chunks, 1):
            await send_chunk_safe(update, chunk, i, len(chunks))
            time.sleep(0.5)
        
        # Delete status message
        await status_msg.delete()
        
        log_debug("=" * 50)
        log_debug(f"EXTRACT COMPLETED - Chat: {chat_name}, Messages: {len(messages)}")
        log_debug("=" * 50)
        
    except Exception as e:
        log_debug("❌ FATAL ERROR", str(e))
        log_debug(traceback.format_exc())
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")