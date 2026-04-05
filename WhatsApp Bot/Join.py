#!/usr/bin/env python3
"""
WhatsApp Bulk Group Join Handler
- Processes multiple group links from a TXT file
- Shows detailed status for each link including community joins
- Reports progress and final summary
- FIXED: Executor error resolved
"""

import os
import time
import logging
import asyncio
import traceback
import re
import requests
import sys
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

# Selenium imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException

logger = logging.getLogger(__name__)

def clean_group_name(text):
    """Clean group name by removing phone numbers and extra text"""
    if not text:
        return "Unknown Group"
    
    # If it looks like a list of phone numbers or is too long
    if re.search(r'\+?\d[\d\s\(\)\-]{7,}', text) or len(text) > 50:
        return "WhatsApp Group"
    
    return text

def get_status_emoji_and_details(status):
    """Get emoji and detailed description for each status"""
    status_lower = status.lower()
    
    if "successfully joined" in status_lower:
        return "✅", "Successfully joined the group/community"
    elif "clicked: join group" in status_lower:
        return "🟢", "Join group request sent (open group)"
    elif "clicked: join community" in status_lower:
        return "🌐", "Join community request sent - community joined"
    elif "clicked: request to join" in status_lower:
        return "🟡", "Request to join sent (pending admin approval)"
    elif "already in group" in status_lower:
        return "🔵", "Already a member of this group/community"
    elif "request already sent" in status_lower:
        return "🟡", "Join request already pending (waiting for approval)"
    elif "group is full" in status_lower:
        return "🔴", "Group is full - cannot join"
    elif "link revoked" in status_lower:
        return "⛔", "Link is invalid or has been reset"
    elif "failed to load" in status_lower:
        return "⚠️", "WhatsApp Web failed to load properly"
    else:
        return "❓", status

def process_single_group(driver, group_link, index, total):
    """
    Process a single group link with improved detection logic
    Returns: (success, status, group_name, detailed_status)
    """
    try:
        # Link Normalization
        if "chat.whatsapp.com/" in group_link:
            code = group_link.split("chat.whatsapp.com/")[1].split("?")[0]
        else:
            code = group_link
            
        direct_link = f"https://web.whatsapp.com/accept?code={code}"
        
        logger.info(f"[{index}/{total}] Navigating to: {direct_link}")
        driver.get(direct_link)
        
        # --- UNIFIED 5 MINUTE MONITORING LOOP ---
        max_wait = 300  # 5 minutes
        start_time = time.time()
        status = "Unknown"
        group_name = "Unknown Group"
        action_taken = False
        detailed_status = "Status could not be determined"
        
        logger.info(f"[{index}/{total}] Starting 5-minute monitoring for group...")
        
        while time.time() - start_time < max_wait:
            current_time = time.time()
            
            # A. First, check if WhatsApp Web base is even loaded
            base_loaded = driver.find_elements(By.XPATH, '//div[@contenteditable="true"]')
            if not base_loaded:
                time.sleep(5)
                continue

            # B. CHECK FOR "ALREADY IN GROUP" FIRST
            # Check if we're directly in the chat (group opened)
            chat_indicators = [
                "//footer//div[@contenteditable='true']",
                "//div[@role='textbox'][@contenteditable='true']",
                "//div[contains(@class, '_ak1q')]//div[@contenteditable='true']",
                "//div[@role='main']//footer"
            ]
            
            for xpath in chat_indicators:
                if driver.find_elements(By.XPATH, xpath):
                    header_elements = driver.find_elements(By.XPATH, "//header//span[@title]")
                    if header_elements:
                        group_name = header_elements[0].get_attribute("title")
                        participants = driver.find_elements(By.XPATH, "//div[contains(@title, 'participants')]")
                        if participants or group_name:
                            status = "Already in group"
                            detailed_status = "You are already a member of this group/community"
                            logger.info(f"[{index}/{total}] Detected: Already in group - {group_name}")
                            break
            
            # Check for explicit "already a member" messages
            already_in_indicators = [
                "//div[contains(text(), 'You are already a member')]",
                "//div[contains(text(), 'Already a member')]",
                "//div[contains(text(), 'already a participant')]",
                "//span[contains(text(), 'already in this group')]"
            ]
            
            for xpath in already_in_indicators:
                elements = driver.find_elements(By.XPATH, xpath)
                if elements:
                    for el in elements:
                        if el.is_displayed():
                            status = "Already in group"
                            detailed_status = "Already a member (explicit message)"
                            logger.info(f"[{index}/{total}] Detected: Already in group (explicit message)")
                            
                            # Try to extract group name
                            try:
                                parent_dialog = el.find_element(By.XPATH, "ancestor::div[@role='dialog']")
                                if parent_dialog:
                                    spans = parent_dialog.find_elements(By.XPATH, ".//span")
                                    for span in spans:
                                        txt = span.text.strip()
                                        if txt and len(txt) > 1 and txt not in ["You are already a member", "Already a member", "OK", "Cancel"]:
                                            group_name = txt
                                            break
                            except:
                                pass
                            break
            
            if status == "Already in group":
                group_name = clean_group_name(group_name)
                break

            # C. Check for Join Buttons (these appear BEFORE error states for valid links)
            join_targets = ["Join group", "Join community", "Request to join"]
            join_button_found = False
            
            for target in join_targets:
                join_buttons = driver.find_elements(By.XPATH, f"//*[contains(text(), '{target}')]")
                for button in join_buttons:
                    if button.is_displayed():
                        join_button_found = True
                        logger.info(f"[{index}/{total}] Found '{target}' button - WILL CLICK IT")
                        
                        # Extract group name
                        try:
                            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                            if dialogs:
                                potentials = dialogs[0].find_elements(By.XPATH, ".//span | .//strong")
                                for p in potentials:
                                    txt = p.text.strip()
                                    if txt and len(txt) > 1 and txt not in join_targets + ["Cancel", "WhatsApp", "Already a member"]:
                                        group_name = clean_group_name(txt)
                                        break
                        except: 
                            pass
                        
                        # Triple-redundant click
                        try:
                            button.click()
                            logger.info(f"[{index}/{total}] Clicked '{target}' button using native click")
                        except:
                            try:
                                ActionChains(driver).move_to_element(button).click().perform()
                                logger.info(f"[{index}/{total}] Clicked '{target}' button using ActionChains")
                            except:
                                driver.execute_script("arguments[0].click();", button)
                                logger.info(f"[{index}/{total}] Clicked '{target}' button using JavaScript")
                        
                        status = f"Clicked: {target}"
                        if target == "Join group":
                            detailed_status = "Join request sent - you can now access the group"
                        elif target == "Join community":
                            detailed_status = "Community join request sent - community joined successfully"
                        elif target == "Request to join":
                            detailed_status = "Request to join sent - waiting for admin approval"
                        
                        action_taken = True
                        time.sleep(12)
                        break
                if action_taken: 
                    break
            
            if action_taken:
                time.sleep(3)
                if driver.find_elements(By.XPATH, "//footer//div[@contenteditable='true']"):
                    status = "Successfully joined"
                    detailed_status = "Successfully joined the group/community - chat is now accessible"
                break

            # D. Check for Error States - ONLY after giving the page time to load and if no join button found
            if current_time - start_time > 15 and not join_button_found:  # Wait at least 15 seconds
                # Check for "Group is full" first
                full_indicators = [
                    "//div[contains(text(), 'group is full')]",
                    "//div[contains(text(), 'Group is full')]",
                    "//div[contains(text(), 'This group is full')]",
                    "//div[contains(text(), 'reached maximum number of participants')]"
                ]
                for xpath in full_indicators:
                    full_elements = driver.find_elements(By.XPATH, xpath)
                    for element in full_elements:
                        if element.is_displayed() and element.text:
                            status = "Group is full"
                            detailed_status = "Cannot join - group has reached maximum participant limit"
                            logger.info(f"[{index}/{total}] Group is full")
                            break
                    if status == "Group is full":
                        break
                
                # Check for revoked/invalid links
                if status != "Group is full":
                    error_indicators = [
                        "//div[contains(text(), 'This invite link is no longer valid')]",
                        "//div[contains(text(), 'link is invalid')]",
                        "//div[contains(text(), 'reset') and contains(text(), 'link')]",
                        "//div[contains(text(), 'Link is invalid')]",
                        "//div[contains(text(), 'Unable to join this group')]",
                        "//div[contains(text(), 'invite link has been reset')]"
                    ]
                    for xpath in error_indicators:
                        error_elements = driver.find_elements(By.XPATH, xpath)
                        for element in error_elements:
                            if element.is_displayed() and element.text and len(element.text) > 5:
                                text_lower = element.text.lower()
                                if any(word in text_lower for word in ['invalid', 'reset', 'no longer', 'unable']):
                                    status = "Link revoked"
                                    detailed_status = f"Link is invalid: {element.text[:100]}"
                                    logger.info(f"[{index}/{total}] Link revoked: {element.text}")
                                    break
                        if status == "Link revoked":
                            break

            if status in ["Link revoked", "Group is full"]:
                break

            # E. Check for pending requests
            if not join_button_found and not action_taken:
                pending_indicators = [
                    "//*[contains(text(), 'Cancel request')]",
                    "//*[contains(text(), 'Request sent. Waiting for admin approval')]",
                    "//*[contains(text(), 'Request pending')]"
                ]
                for xpath in pending_indicators:
                    pending_elements = driver.find_elements(By.XPATH, xpath)
                    if pending_elements:
                        for el in pending_elements:
                            if el.is_displayed():
                                logger.info(f"[{index}/{total}] Found pending request")
                                status = "Request already sent"
                                detailed_status = "Join request already pending - waiting for admin approval"
                                
                                # Try to extract group name
                                try:
                                    dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                                    if dialogs:
                                        potentials = dialogs[0].find_elements(By.XPATH, ".//span | .//strong")
                                        for p in potentials:
                                            txt = p.text.strip()
                                            if txt and len(txt) > 1 and txt not in ["Cancel request", "Request sent. Waiting for admin approval.", "Join group", "Join community", "Request to join"]:
                                                group_name = clean_group_name(txt)
                                                break
                                except: 
                                    pass
                                break
                        if status == "Request already sent": 
                            break
            
            if status == "Request already sent": 
                break
                
            time.sleep(5)
        
        # Final check for WhatsApp load
        if not driver.find_elements(By.XPATH, '//div[@contenteditable="true"]') and status == "Unknown":
            return False, "WhatsApp Web failed to load within 5 minutes", "Unknown Group", "WhatsApp Web interface did not load completely"

        # Extract group name if still unknown
        if group_name == "Unknown Group" or group_name == "WhatsApp Group":
            try:
                # Try header first
                header_elements = driver.find_elements(By.XPATH, "//header//span[@title]")
                if header_elements:
                    group_name = header_elements[0].get_attribute("title")
                
                # Try dialog title
                if group_name == "Unknown Group":
                    dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                    if dialogs:
                        potentials = dialogs[0].find_elements(By.XPATH, ".//span | .//strong")
                        for p in potentials:
                            txt = p.text.strip()
                            if txt and len(txt) > 1 and txt not in ["Join group", "Join community", "Request to join", "Cancel", "WhatsApp", "Already a member", "Cancel request", "Unable to join this group", "Request sent. Waiting for admin approval."]:
                                group_name = txt
                                break
                
                group_name = clean_group_name(group_name)
            except: 
                pass
        
        # If detailed_status not set, set it based on status
        if not detailed_status or detailed_status == "Status could not be determined":
            if status == "Unknown":
                detailed_status = "Could not determine group status after 5 minutes"
            elif status == "Link revoked":
                detailed_status = "This invite link is no longer valid or has been reset"
            elif status == "Group is full":
                detailed_status = "Group has reached maximum participant capacity"
            elif status == "Already in group":
                detailed_status = "You are already a member of this group/community"
            elif status == "Request already sent":
                detailed_status = "Join request is pending admin approval"
            elif "Clicked:" in status:
                if "Join group" in status:
                    detailed_status = "Successfully joined the group"
                elif "Join community" in status:
                    detailed_status = "Successfully joined the community"
                elif "Request to join" in status:
                    detailed_status = "Request to join sent - waiting for admin approval"
                else:
                    detailed_status = f"Action completed: {status}"
            elif status == "Successfully joined":
                detailed_status = "Successfully joined the group/community - chat is now accessible"
        
        return True, status, group_name, detailed_status
        
    except Exception as e:
        logger.error(f"[{index}/{total}] Error processing group: {traceback.format_exc()}")
        return False, str(e), "Unknown", f"Error occurred: {str(e)[:100]}"

async def run(update: Update, context: ContextTypes.DEFAULT_TYPE, driver):
    """
    Main entry point for the /join command.
    Downloads TXT file and processes all group links.
    """
    try:
        # Check if URL provided
        if not context.args:
            await update.message.reply_text(
                "❌ *Missing File URL*\n"
                "Usage: `/join https://example.com/groups.txt`\n\n"
                "The text file should contain one WhatsApp group link per line.",
                parse_mode='Markdown'
            )
            return
        
        file_url = context.args[0]
        status_msg = await update.message.reply_text(
            "📥 *Downloading group links file...*",
            parse_mode='Markdown'
        )
        
        # Download the text file
        try:
            response = requests.get(file_url, timeout=30)
            response.raise_for_status()
            content = response.text
        except Exception as e:
            await status_msg.edit_text(f"❌ *Failed to download file*\n`{str(e)}`", parse_mode='Markdown')
            return
        
        # Parse links (one per line, skip empty lines and comments)
        lines = content.strip().split('\n')
        group_links = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                # Extract WhatsApp group link if present
                if 'chat.whatsapp.com' in line:
                    group_links.append(line)
        
        if not group_links:
            await status_msg.edit_text(
                "❌ *No valid WhatsApp group links found*\n"
                "Make sure the file contains links with 'chat.whatsapp.com'",
                parse_mode='Markdown'
            )
            return
        
        total_links = len(group_links)
        await status_msg.edit_text(
            f"🔍 *Found {total_links} group links*\n"
            f"🔄 Processing each link one by one...\n"
            f"⏱️ Each link may take up to 5 minutes.\n\n"
            f"📊 I'll send status updates for every link with detailed information.",
            parse_mode='Markdown'
        )
        
        # Results storage
        results = []
        success_count = 0
        failed_count = 0
        
        # Get the executor from the main module
        main_module = sys.modules.get('__main__')
        executor = getattr(main_module, 'executor', None)
        
        if not executor:
            logger.warning("Executor not found in main module, using synchronous processing")
        
        # Process each link
        for i, link in enumerate(group_links, 1):
            try:
                # Update status periodically
                if i == 1 or i % 5 == 0 or i == total_links:
                    await update.message.reply_text(
                        f"📊 *Progress Update*\n"
                        f"Processing link {i}/{total_links}...\n"
                        f"✅ Success: {success_count}\n"
                        f"❌ Failed: {failed_count}\n"
                        f"⏳ Please wait...",
                        parse_mode='Markdown'
                    )
                
                # Process the link
                loop = asyncio.get_event_loop()
                
                # FIXED: Use the executor from main module if available
                if executor:
                    success, status, group_name, detailed_status = await loop.run_in_executor(
                        executor, 
                        process_single_group, 
                        driver, link, i, total_links
                    )
                else:
                    # Fallback if executor not available (run synchronously)
                    success, status, group_name, detailed_status = process_single_group(
                        driver, link, i, total_links
                    )
                
                # Store result
                result = {
                    'link': link,
                    'success': success,
                    'status': status,
                    'group_name': group_name,
                    'detailed_status': detailed_status
                }
                results.append(result)
                
                if success:
                    success_count += 1
                else:
                    failed_count += 1
                
                # Get emoji based on status
                emoji, _ = get_status_emoji_and_details(status)
                
                # Send individual result with full link and detailed status
                await update.message.reply_text(
                    f"{emoji} *Link {i}/{total_links}*\n"
                    f"📌 *Group:* {group_name}\n"
                    f"📋 *Status:* {status}\n"
                    f"ℹ️ *Details:* {detailed_status}\n"
                    f"🔗 `{link}`",
                    parse_mode='Markdown'
                )
                
                # Small delay between links to avoid rate limiting
                await asyncio.sleep(3)
                
            except Exception as e:
                failed_count += 1
                results.append({
                    'link': link,
                    'success': False,
                    'status': f"Error: {str(e)}",
                    'group_name': 'Unknown',
                    'detailed_status': f"Exception occurred: {str(e)[:100]}"
                })
                await update.message.reply_text(
                    f"❌ *Link {i}/{total_links} Error*\n"
                    f"🔗 `{link}`\n"
                    f"⚠️ *Error:* `{str(e)[:100]}`",
                    parse_mode='Markdown'
                )
        
        # Send final summary
        summary = f"📊 *Bulk Join Complete*\n\n"
        summary += f"📝 *Total Links:* {total_links}\n"
        summary += f"✅ *Successful:* {success_count}\n"
        summary += f"❌ *Failed:* {failed_count}\n\n"
        
        # Group by status for summary
        status_counts = {}
        
        for r in results:
            if r['success']:
                status_key = r['status']
                # Clean up status for counting
                if "Clicked: Join group" in status_key:
                    status_key = "✅ Joined Group"
                elif "Clicked: Join community" in status_key:
                    status_key = "🌐 Joined Community"
                elif "Clicked: Request to join" in status_key:
                    status_key = "🟡 Request Pending"
                elif "Successfully joined" in status_key:
                    status_key = "✅ Successfully Joined"
                elif "Already in group" in status_key:
                    status_key = "🔵 Already Member"
                elif "Request already sent" in status_key:
                    status_key = "🟡 Request Pending"
                else:
                    status_key = "✅ Success"
            else:
                if "Group is full" in r['status']:
                    status_key = "🔴 Group Full"
                elif "Link revoked" in r['status']:
                    status_key = "⛔ Link Revoked"
                elif "Failed to load" in r['status']:
                    status_key = "⚠️ Load Failed"
                else:
                    status_key = "❌ Failed"
            
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
        
        summary += "*Status Breakdown:*\n"
        for status, count in status_counts.items():
            summary += f"• {status}: {count}\n"
        
        # Add sample of processed links
        summary += f"\n*Sample Results:*\n"
        for r in results[:5]:  # Show first 5 as sample
            emoji, _ = get_status_emoji_and_details(r['status'])
            short_link = r['link'][:30] + "..." if len(r['link']) > 30 else r['link']
            summary += f"{emoji} {short_link} → {r['status']}\n"
        
        if len(results) > 5:
            summary += f"... and {len(results) - 5} more"
        
        await update.message.reply_text(summary, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Join command error: {traceback.format_exc()}")
        await update.message.reply_text(f"❌ *System Error*\n`{str(e)}`", parse_mode='Markdown')